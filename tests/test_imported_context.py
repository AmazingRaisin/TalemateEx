"""
Context database entries copied from the scene a character was imported from
(talemate.imported_context), read only by the characters imported with them.
"""

import json

import pytest

import talemate.instance as instance
import talemate.save as save
from conftest import MockScene, bootstrap_scene
from talemate.agents.context import ActiveAgent
from talemate.agents.memory.schema import MemoryDocument
from talemate.character import Character, activate_character, deactivate_character
from talemate.context import active_scene, prompt_local_character
from talemate.imported_context import imported_documents
from talemate.load import transfer_character
from talemate.scene_message import CharacterMessage
from talemate.tale_mate import Actor, Player

EVERYONE = ["Lonzo", "Fern", "Frieren", "Sarah"]


def new_scene() -> MockScene:
    scene = MockScene()
    scene.test_agents = bootstrap_scene(scene)
    scene.character_dependent_history = True
    return scene


async def add(scene, name: str, player: bool = False, **fields) -> Character:
    character = Character(name=name, is_player=player, **fields)
    await scene.add_actor((Player if player else Actor)(character, None))
    scene.active_characters = [c.name for c in scene.characters]
    return character


async def camp_data(sarah_viewers=()) -> dict:
    """
    The camp: Sarah has a secret (a private attribute, its public value), Fern
    is away while Sarah confesses to Lonzo and Frieren.
    """

    scene = new_scene()
    token = active_scene.set(scene)
    try:
        scene.title = "The Camp"
        await add(scene, "Lonzo", player=True)
        await add(scene, "Fern")
        await add(scene, "Frieren")
        await add(
            scene,
            "Sarah",
            base_attributes={"name": "Sarah", "secret": "Stole the map."},
            private_attributes=["secret"],
            public_attributes={"secret": "Seems nervous."},
            attributes_private_viewers=list(sarah_viewers),
        )
        await scene.push_history(CharacterMessage("Lonzo: Morning."))
        await deactivate_character(scene, "Fern")
        await scene.push_history(CharacterMessage("Sarah: I stole the map."))
        await activate_character(scene, "Fern")
        scene.archived_history = [
            {
                "id": "a1",
                "text": "The camp wakes up.",
                "start": 0,
                "end": 0,
                "ts": "PT0S",
                "character_names": EVERYONE,
            },
            {
                "id": "a2",
                "text": "Sarah confesses she stole the map.",
                "start": 1,
                "end": 1,
                "ts": "PT1H",
                "character_names": ["Lonzo", "Frieren", "Sarah"],
            },
        ]
        return json.loads(json.dumps(scene.serialize, cls=save.SceneEncoder))
    finally:
        active_scene.reset(token)


def write(tmp_path, name: str, data: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.fixture
async def capital():
    scene = new_scene()
    token = active_scene.set(scene)
    scene.title = "The Capital"
    await add(scene, "Lonzo", player=True)
    await add(scene, "Stark")
    yield scene
    active_scene.reset(token)


async def import_from(scene, path, name, **options):
    options = {"copy_context": True, "copy_character_info": True, **options}
    await transfer_character(scene, path, name, history="clean", **options)
    await activate_character(scene, name)


def documents(scene) -> list[MemoryDocument]:
    return [
        MemoryDocument(doc["text"], doc["meta"], doc["id"], doc["text"])
        for doc in imported_documents(scene)
    ]


def readable(scene, viewer) -> list[str]:
    memory = instance.get_agent("memory")
    # building an old scene sets the agents up for it
    memory.scene = scene
    token = prompt_local_character.set(viewer)
    try:
        return sorted(str(d) for d in documents(scene) if memory._visible_to_prompt(d))
    finally:
        prompt_local_character.reset(token)


# ---------------------------------------------------------------------------
# copying
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_what_is_copied_and_who_reads_it(capital, tmp_path):
    await import_from(capital, write(tmp_path, "camp.json", await camp_data()), "Fern")

    # the history she perceived, and the old scene's other characters' info
    # (Lonzo is in this scene himself)
    assert readable(capital, "Fern") == [
        "Sarah's secret: Seems nervous.",
        "The camp wakes up.",
    ]
    text = " ".join(readable(capital, "Fern"))
    assert "stole the map" not in text.lower()
    assert "Lonzo" not in text

    # no one else
    assert readable(capital, "Stark") == []
    assert readable(capital, None) == []
    narrator = instance.get_agent("narrator")
    with ActiveAgent(narrator, narrator.progress_story):
        assert readable(capital, "Fern") == []


@pytest.mark.asyncio
async def test_private_info_it_could_see_there(capital, tmp_path):
    data = await camp_data(sarah_viewers=["Fern"])
    await import_from(capital, write(tmp_path, "camp.json", data), "Fern")

    text = " ".join(readable(capital, "Fern"))
    assert "Stole the map." in text
    assert "Seems nervous." not in text


@pytest.mark.asyncio
async def test_only_history_or_only_character_info(capital, tmp_path):
    path = write(tmp_path, "camp.json", await camp_data())
    await import_from(capital, path, "Fern", copy_character_info=False)
    assert readable(capital, "Fern") == ["The camp wakes up."]

    await import_from(capital, path, "Frieren", copy_context=False)
    assert not any("camp" in line for line in readable(capital, "Frieren"))
    assert any("Sarah" in line for line in readable(capital, "Frieren"))


@pytest.mark.asyncio
async def test_a_second_character_from_the_same_scene_later(capital, tmp_path):
    data = await camp_data()
    await import_from(capital, write(tmp_path, "camp.json", data), "Fern")

    # the camp was played further before Frieren comes along
    data["archived_history"].append(
        {
            "id": "a3",
            "text": "They break camp.",
            "start": 2,
            "end": 2,
            "ts": "PT2H",
            "character_names": EVERYONE,
        }
    )
    await import_from(capital, write(tmp_path, "camp2.json", data), "Frieren")

    assert len(capital.imported_contexts) == 1
    context = next(iter(capital.imported_contexts.values()))
    readers = {entry.text: entry.readers for entry in context.entries.values()}
    # the same entry, both read it
    assert readers["The camp wakes up."] == ["Fern", "Frieren"]
    # what only she perceived, and what happened after Fern left
    assert readers["Sarah confesses she stole the map."] == ["Frieren"]
    assert readers["They break camp."] == ["Frieren"]
    assert "They break camp." not in readable(capital, "Fern")


@pytest.mark.asyncio
async def test_groups_read_what_their_members_read(capital, tmp_path):
    from talemate.groups import (
        add_group,
        add_group_member,
        get_group,
        group_character,
        group_perspective,
    )

    await import_from(capital, write(tmp_path, "camp.json", await camp_data()), "Fern")
    add_group(capital, "travellers")
    add_group_member(capital, "travellers", "Fern")
    add_group_member(capital, "travellers", "Stark")

    group = group_character(capital, get_group(capital, "travellers"))
    with group_perspective(group):
        assert "The camp wakes up." in readable(capital, group.name)

    get_group(capital, "travellers").share_history = True
    group = group_character(capital, get_group(capital, "travellers"))
    with group_perspective(group):
        assert readable(capital, group.name) == []


# ---------------------------------------------------------------------------
# changes in the scene
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_character_it_read_about_joins(capital, tmp_path):
    path = write(tmp_path, "camp.json", await camp_data(sarah_viewers=["Fern"]))
    await import_from(capital, path, "Fern")
    await import_from(
        capital, path, "Sarah", copy_context=False, copy_character_info=False
    )

    # her own sheet takes over, Fern sees her private info as her viewer now
    assert not any("Sarah" in line for line in readable(capital, "Fern"))
    sarah = capital.get_character("Sarah")
    assert sarah.attributes_private_viewers == ["Fern"]
    context = next(iter(capital.imported_contexts.values()))
    assert "Sarah" not in context.private_access.get("Fern", {})


@pytest.mark.asyncio
async def test_removing_its_history_removes_what_it_read(capital, tmp_path):
    from unittest.mock import MagicMock

    from talemate.server.world_state_manager import WorldStateManagerPlugin

    data = await camp_data()
    await import_from(capital, write(tmp_path, "camp.json", data), "Fern")
    await import_from(capital, write(tmp_path, "camp.json", data), "Frieren")

    details = await capital.world_state_manager.get_character_details("Fern")
    assert details.imported_context[0]["title"] == "The Camp"
    assert details.imported_context[0]["history"] == 1

    handler = MagicMock()
    handler.scene = capital
    plugin = WorldStateManagerPlugin(handler)
    await plugin.handle_remove_character_imported_history({"name": "Fern"})

    assert readable(capital, "Fern") == []
    context = next(iter(capital.imported_contexts.values()))
    # what Frieren reads stays
    assert all(entry.readers == ["Frieren"] for entry in context.entries.values())

    await capital.remove_character(capital.get_character("Frieren"))
    assert capital.imported_contexts == {}


@pytest.mark.asyncio
async def test_kept_in_the_save_and_restored_with_the_context_database(
    capital, tmp_path, monkeypatch
):
    await import_from(capital, write(tmp_path, "camp.json", await camp_data()), "Fern")
    data = json.loads(json.dumps(capital.serialize, cls=save.SceneEncoder))
    assert data["imported_contexts"]

    memory = instance.get_agent("memory")
    synced = []

    async def sync(objects, scopes=None):
        synced.append((objects, scopes))

    async def set_db():
        pass

    monkeypatch.setattr(memory, "sync", sync)
    monkeypatch.setattr(memory, "set_db", set_db)
    await capital.commit_to_memory()
    ids = [doc["id"] for doc in synced[-1][0]]
    assert sum(1 for doc_id in ids if doc_id.startswith("imported.")) == len(
        imported_documents(capital)
    )


@pytest.mark.asyncio
async def test_moving_on_brings_what_it_read(capital, tmp_path):
    camp = await camp_data()
    camp_id = camp["id"]
    await import_from(capital, write(tmp_path, "camp.json", camp), "Fern")
    data = json.loads(json.dumps(capital.serialize, cls=save.SceneEncoder))

    harbor = new_scene()
    token = active_scene.set(harbor)
    try:
        await add(harbor, "Lonzo", player=True)
        await import_from(harbor, write(tmp_path, "capital.json", data), "Fern")
        assert camp_id in harbor.imported_contexts
        assert "The camp wakes up." in readable(harbor, "Fern")
    finally:
        active_scene.reset(token)


@pytest.mark.asyncio
async def test_the_old_scenes_history_override_counts(capital, tmp_path):
    data = await camp_data()
    data["character_data"]["Fern"]["character_dependent_history_override"] = -1
    await import_from(
        capital, write(tmp_path, "camp.json", data), "Fern", copy_character_info=False
    )
    assert readable(capital, "Fern") == [
        "Sarah confesses she stole the map.",
        "The camp wakes up.",
    ]
