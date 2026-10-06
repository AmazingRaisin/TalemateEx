"""
Time passages with combined units (the custom advance time option), exact time
passage labels, and scene time / history timestamps built from them.
"""

import types

import pytest

from talemate.history import (
    collect_time_passages,
    history_with_relative_time,
    set_time_passage_duration,
    update_time_passage_by_id,
)
from talemate.scene_message import CharacterMessage, TimePassageMessage
from talemate.tale_mate import Scene
from talemate.util.time import (
    time_passage_to_human,
    validate_time_passage_duration,
)


def _msg(text: str) -> CharacterMessage:
    return CharacterMessage(message=text, source="ai")


def _time(ts: str) -> TimePassageMessage:
    return TimePassageMessage(ts=ts, message=time_passage_to_human(ts))


def make_scene(history: list, archived_history: list | None = None):
    scene = types.SimpleNamespace(
        ts="PT0S",
        history=history,
        archived_history=archived_history or [],
        layered_history=[],
    )
    scene.fix_time = lambda: Scene._fix_time(scene)
    scene.message_index = lambda mid: Scene.message_index(scene, mid)
    return scene


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "duration, label",
    [
        ("P2Y21DT2H", "2 Years, 3 Weeks and 2 Hours later"),
        ("P1Y2M3DT4H5M", "1 Year, 2 Months, 3 Days, 4 Hours and 5 Minutes later"),
        ("P7D", "1 Week later"),
        ("P14D", "2 Weeks later"),
        ("P17D", "2 Weeks and 3 Days later"),
        ("PT30H", "1 Day and 6 Hours later"),
        ("PT90M", "1 Hour and 30 Minutes later"),
        # the fixed advance time options keep their labels
        ("P10Y", "10 Years later"),
        ("P6M", "6 Months later"),
        ("P1M", "1 Month later"),
        ("P3D", "3 Days later"),
        ("PT12H", "12 Hours later"),
        ("PT5M", "5 Minutes later"),
    ],
)
def test_time_passage_labels_are_exact(duration, label):
    assert time_passage_to_human(duration) == label


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("duration", ["P2Y21DT2H", "P7D", "PT5M", " P1D "])
def test_valid_durations(duration):
    assert validate_time_passage_duration(duration) == duration.strip()


@pytest.mark.parametrize(
    "duration",
    [
        # what the week options used to send
        "P7D:1 Week later",
        "",
        None,
        "PT0S",
        "P0D",
        "-P1D",
        "2 weeks",
    ],
)
def test_invalid_durations(duration):
    with pytest.raises(ValueError):
        validate_time_passage_duration(duration)


# ---------------------------------------------------------------------------
# scene time and history
# ---------------------------------------------------------------------------


def test_combined_durations_add_up_for_history():
    tp1 = _time("P2Y21DT2H")
    tp2 = _time("PT30M")
    tp3 = _time("P1M3D")
    scene = make_scene(
        history=[
            _msg("A"),  # 0
            tp1,  # 1
            _msg("B"),  # 2
            tp2,  # 3
            _msg("C"),  # 4
            tp3,  # 5
            _msg("D"),  # 6
        ],
        archived_history=[
            {"text": "A", "start": 0, "end": 0, "ts": "PT0S", "id": "a"},
            {"text": "B", "start": 2, "end": 2, "ts": "PT0S", "id": "b"},
            {"text": "C", "start": 4, "end": 4, "ts": "PT0S", "id": "c"},
            {"text": "D", "start": 6, "end": 6, "ts": "PT0S", "id": "d"},
        ],
    )

    scene.fix_time()

    assert scene.ts == "P2Y1M24DT2H30M"
    assert [entry["ts"] for entry in scene.archived_history] == [
        "PT0S",
        "P2Y21DT2H",
        "P2Y21DT2H30M",
        "P2Y1M24DT2H30M",
    ]

    relative = history_with_relative_time(scene.archived_history, scene.ts)
    assert relative[-1]["time"] == "Recently"
    assert relative[-2]["time"] == "1 Month and 3 Days ago"
    assert relative[0]["time"] == (
        "2 Years, 1 Month, 3 Weeks, 3 Days, 2 Hours and 30 Minutes ago"
    )


def test_update_with_combined_duration_recalculates_history():
    tp = _time("PT2H")
    scene = make_scene(
        history=[_msg("A"), tp, _msg("B")],
        archived_history=[
            {"text": "A", "start": 0, "end": 0, "ts": "PT0S", "id": "a"},
            {"text": "B", "start": 2, "end": 2, "ts": "PT2H", "id": "b"},
        ],
    )

    update_time_passage_by_id(scene, tp.id, duration="P2Y21DT2H")

    assert tp.ts == "P2Y21DT2H"
    assert tp.message == "2 Years, 3 Weeks and 2 Hours later"
    assert scene.archived_history[1]["ts"] == "P2Y21DT2H"
    assert scene.ts == "P2Y21DT2H"


def test_update_with_amount_and_unit_still_works():
    tp = _time("PT2H")
    scene = make_scene(history=[_msg("A"), tp, _msg("B")])

    update_time_passage_by_id(scene, tp.id, amount=2, unit="weeks")

    assert tp.ts == "P2W"
    assert tp.message == "2 Weeks later"
    assert scene.ts == "P14D"


def test_invalid_update_leaves_passage_unchanged():
    tp = _time("PT2H")
    scene = make_scene(history=[_msg("A"), tp])

    with pytest.raises(ValueError):
        set_time_passage_duration(scene, 1, duration="P7D:1 Week later")
    with pytest.raises(ValueError):
        set_time_passage_duration(scene, 1)
    with pytest.raises(ValueError):
        set_time_passage_duration(scene, 0, duration="PT1H")
    with pytest.raises(IndexError):
        set_time_passage_duration(scene, 5, duration="PT1H")

    assert tp.ts == "PT2H"
    assert tp.message == "2 Hours later"


def test_collected_time_passages_show_exact_label():
    scene = make_scene(history=[_msg("A"), _time("P2Y21DT2H")])

    passages = collect_time_passages(scene)

    assert passages[0]["ts"] == "P2Y21DT2H"
    assert passages[0]["human"] == "2 Years, 3 Weeks and 2 Hours later"


# ---------------------------------------------------------------------------
# advance time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advance_time_with_combined_duration():
    from conftest import MockScene, bootstrap_scene
    from talemate.context import active_scene

    scene = MockScene()
    agents = bootstrap_scene(scene)
    token = active_scene.set(scene)
    try:
        message = await agents["world_state"].advance_time("P2Y21DT2H")
        await agents["world_state"].advance_time("P7D")
    finally:
        active_scene.reset(token)

    assert message.message == "2 Years, 3 Weeks and 2 Hours later"
    assert scene.history[-1].message == "1 Week later"
    assert scene.ts == "P2Y28DT2H"


@pytest.mark.asyncio
async def test_advance_time_rejects_old_week_format():
    from conftest import MockScene, bootstrap_scene
    from talemate.context import active_scene

    scene = MockScene()
    agents = bootstrap_scene(scene)
    token = active_scene.set(scene)
    try:
        with pytest.raises(ValueError):
            await agents["world_state"].advance_time("P7D:1 Week later")
    finally:
        active_scene.reset(token)

    assert scene.history == []


# ---------------------------------------------------------------------------
# exact / rounded history times
# ---------------------------------------------------------------------------


@pytest.fixture
def round_history_times(monkeypatch):
    from talemate.config import get_config

    general = get_config().game.general

    def set_rounding(value: bool):
        monkeypatch.setattr(general, "round_history_times", value)

    set_rounding(False)
    return set_rounding


def history_example():
    """entry 1 -> 1 year later -> entry 2 -> 2 years and 2 months later"""
    scene = make_scene(
        history=[
            _msg("A"),  # 0
            _time("P1Y"),  # 1
            _msg("B"),  # 2
            _time("P2Y2M"),  # 3
        ],
        archived_history=[
            {"text": "A", "start": 0, "end": 0, "ts": "PT0S", "id": "a"},
            {"text": "B", "start": 2, "end": 2, "ts": "PT0S", "id": "b"},
        ],
    )
    scene.fix_time()
    return scene


def test_history_times_are_exact_by_default(round_history_times):
    scene = history_example()

    relative = history_with_relative_time(scene.archived_history, scene.ts)

    assert [r["time"] for r in relative] == [
        "3 Years and 2 Months ago",
        "2 Years and 2 Months ago",
    ]


def test_history_times_can_be_rounded(round_history_times):
    round_history_times(True)
    scene = history_example()

    relative = history_with_relative_time(scene.archived_history, scene.ts)

    assert [r["time"] for r in relative] == [
        "3 Years ago",
        "2 Years and 2 Months ago",
    ]


@pytest.mark.parametrize(
    "scene_time, entry_time, label",
    [
        # years / months are kept apart from days
        ("P1Y", "P11M", "1 Month ago"),
        ("P2Y1M", "P1Y", "1 Year and 1 Month ago"),
        # days still carry into months / years
        ("P60D", "PT0S", "2 Months ago"),
        ("P2Y1M24DT2H30M", "P2Y21DT2H", "1 Month, 3 Days and 30 Minutes ago"),
        ("PT0S", "PT0S", "Recently"),
    ],
)
def test_exact_history_times(round_history_times, scene_time, entry_time, label):
    from talemate.util.time import iso8601_diff_to_human

    assert iso8601_diff_to_human(scene_time, entry_time) == label
