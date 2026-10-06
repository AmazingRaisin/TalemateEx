"""
Lore imported with a character, kept from the characters that aren't from
that scene, and characters that only know what they brought
(talemate.imported_lore).
"""

import json

import pytest

import talemate.instance as instance
import talemate.save as save
from conftest import MockScene, bootstrap_scene
from talemate.agents.memory.schema import MemoryDocument
from talemate.character import Character, activate_character
from talemate.context import active_scene, prompt_local_character
from talemate.load import transfer_character
from talemate.tale_mate import Actor, Player
from talemate.world_state import LorebookSettings, ManualContext
from talemate.world_state.lorebook import SCENE_LORE_ID, lore_hidden_from


def new_scene() -> MockScene:
    scene = MockScene()
    scene.test_agents = bootstrap_scene(scene)
    return scene


async def add(scene, name: str, player: bool = False, **fields) -> Character:
    character = Character(name=name, is_player=player, **fields)
    await scene.add_actor((Player if player else Actor)(character, None))
    scene.active_characters = [c.name for c in scene.characters]
    return character


def world_entry(scene, entry_id, text, lorebook=None):
    meta = {"typ": "world_state"}
    if lorebook:
        meta.update(lorebook_id=lorebook, lorebook_name=lorebook.title())
        scene.world_state.lorebooks.setdefault(
            lorebook, LorebookSettings(id=lorebook, name=lorebook.title())
        )
    scene.world_state.manual_context[entry_id] = ManualContext(
        id=entry_id, text=text, meta=meta
    )


async def camp_data() -> dict:
    """
    The camp's lore: its own world entries, an elves lorebook, a dragons
    lorebook Fern's lore filter hid, a lorebook the capital has as well.
    """

    scene = new_scene()
    token = active_scene.set(scene)
    try:
        scene.title = "The Camp"
        await add(scene, "Lonzo", player=True)
        await add(scene, "Fern", lorebook_disabled=["dragons"])
        await add(scene, "Frieren")
        world_entry(scene, "river", "The river is cold.")
        world_entry(scene, "elves", "Elves live long.", lorebook="elves")
        world_entry(scene, "dragons", "Dragons breathe fire.", lorebook="dragons")
        world_entry(scene, "sun", "The sun rises in the east.", lorebook="common")
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
    await add(scene, "Stark", base_attributes={"name": "Stark", "job": "Guard"})
    world_entry(scene, "walls", "The capital has walls.")
    world_entry(scene, "council", "The council rules.", lorebook="politics")
    world_entry(scene, "sun", "The sun rises in the east.", lorebook="common")
    yield scene
    active_scene.reset(token)


def sees(scene, name, entry_id) -> bool:
    entry = scene.world_state.manual_context[entry_id]
    character = scene.get_character(name)
    return not lore_hidden_from(character, entry_id, entry.meta, scene.world_state)


def entry_id_of(scene, text) -> str:
    return next(
        i for i, e in scene.world_state.manual_context.items() if e.text == text
    )


async def import_fern(scene, path, **options):
    await transfer_character(scene, path, "Fern", **options)
    await activate_character(scene, "Fern")


@pytest.mark.asyncio
async def test_lore_it_brings_and_who_knows_it(capital, tmp_path):
    camp = await camp_data()
    await import_fern(capital, write(tmp_path, "camp.json", camp), import_lore=True)
    world_state = capital.world_state

    camp_lore = f"imported:{camp['id']}"
    assert world_state.lorebooks[camp_lore].name == "The Camp: world entries"
    assert world_state.lorebooks[camp_lore].imported_from == camp["id"]
    assert world_state.lorebooks["elves"].imported_from == camp["id"]
    # what her lore filter hid there, and what both scenes have, isn't copied
    assert "dragons" not in world_state.lorebooks
    assert world_state.lorebooks["common"].imported_from is None
    assert [e.text for e in world_state.manual_context.values()].count(
        "The sun rises in the east."
    ) == 1

    river = entry_id_of(capital, "The river is cold.")
    elves = entry_id_of(capital, "Elves live long.")
    assert sees(capital, "Fern", river) and sees(capital, "Fern", elves)
    # the others aren't from there
    for name in ("Lonzo", "Stark"):
        assert not sees(capital, name, river) and not sees(capital, name, elves)
        assert sees(capital, name, "walls")
    # Fern knows this scene's lore too (it isn't blocked)
    assert sees(capital, "Fern", "walls") and sees(capital, "Fern", "council")


@pytest.mark.asyncio
async def test_characters_and_lorebooks_that_come_later(capital, tmp_path):
    path = write(tmp_path, "camp.json", await camp_data())
    await import_fern(capital, path, import_lore=True, block_scene_knowledge=True)
    river = entry_id_of(capital, "The river is cold.")

    # someone new isn't from there
    await add(capital, "Mia")
    assert not sees(capital, "Mia", river)

    # someone else from there knows it (imported without its lore)
    await transfer_character(capital, path, "Frieren")
    assert sees(capital, "Frieren", river)

    # a lorebook added to this scene later is kept from Fern only
    await capital.world_state_manager.save_lorebook_settings(
        LorebookSettings(id="trade", name="Trade")
    )
    world_entry(capital, "coin", "Coins are gold.", lorebook="trade")
    assert not sees(capital, "Fern", "coin")
    assert sees(capital, "Mia", "coin") and sees(capital, "Frieren", "coin")


@pytest.mark.asyncio
async def test_blocking_new_scene_knowledge(capital, tmp_path):
    camp = await camp_data()
    await import_fern(
        capital,
        write(tmp_path, "camp.json", camp),
        import_lore=True,
        block_scene_knowledge=True,
    )
    fern = capital.get_character("Fern")
    assert fern.scene_knowledge_blocked
    assert SCENE_LORE_ID in fern.lorebook_disabled
    assert not sees(capital, "Fern", "walls")
    assert not sees(capital, "Fern", "council")
    # lore both scenes have, and her own, stay
    assert sees(capital, "Fern", "sun")
    assert sees(capital, "Fern", entry_id_of(capital, "Elves live long."))

    # this scene's characters' context database entries are kept from her
    memory = instance.get_agent("memory")
    memory.scene = capital

    def visible(viewer, owner):
        doc = MemoryDocument(
            f"{owner}: job: Guard",
            {"character": owner, "typ": "base_attribute", "attr": "job"},
            f"{owner}.job",
            "",
        )
        token = prompt_local_character.set(viewer)
        try:
            return memory._visible_to_prompt(doc)
        finally:
            prompt_local_character.reset(token)

    assert not visible("Fern", "Stark")
    assert visible("Stark", "Stark") and visible("Lonzo", "Stark")
    assert visible("Fern", "Fern")
    # someone from a scene she came from isn't new to her
    await transfer_character(capital, write(tmp_path, "camp.json", camp), "Frieren")
    assert visible("Fern", "Frieren")


@pytest.mark.asyncio
async def test_the_block_on_the_character_page(capital, tmp_path):
    from unittest.mock import MagicMock

    from talemate.server.world_state_manager import WorldStateManagerPlugin

    await import_fern(capital, write(tmp_path, "camp.json", await camp_data()))
    handler = MagicMock()
    handler.scene = capital
    plugin = WorldStateManagerPlugin(handler)

    await plugin.handle_update_character_scene_knowledge_blocked(
        {"name": "Fern", "blocked": True}
    )
    fern = capital.get_character("Fern")
    assert fern.scene_knowledge_blocked and not sees(capital, "Fern", "walls")
    details = await capital.world_state_manager.get_character_details("Fern")
    assert details.scene_knowledge_blocked and details.origin_scenes

    await plugin.handle_update_character_scene_knowledge_blocked(
        {"name": "Fern", "blocked": False}
    )
    assert not fern.scene_knowledge_blocked
    # its lore filter keeps what it had, it can be changed there
    assert SCENE_LORE_ID in fern.lorebook_disabled


@pytest.mark.asyncio
async def test_moving_on_brings_what_it_could_see(capital, tmp_path):
    camp = await camp_data()
    await import_fern(
        capital,
        write(tmp_path, "camp.json", camp),
        import_lore=True,
        block_scene_knowledge=True,
    )
    data = json.loads(json.dumps(capital.serialize, cls=save.SceneEncoder))

    harbor = new_scene()
    token = active_scene.set(harbor)
    try:
        await add(harbor, "Lonzo", player=True)
        await transfer_character(
            harbor, write(tmp_path, "capital.json", data), "Fern", import_lore=True
        )
        texts = [e.text for e in harbor.world_state.manual_context.values()]
        # the camp's lore she brought to the capital, not the capital's she
        # was kept from
        assert "Elves live long." in texts and "The river is cold." in texts
        assert "The capital has walls." not in texts
        assert harbor.world_state.lorebooks["elves"].imported_from == camp["id"]
        fern = harbor.get_character("Fern")
        assert fern.origin_scenes == [camp["id"], data["id"]]
        assert not fern.scene_knowledge_blocked
    finally:
        active_scene.reset(token)
