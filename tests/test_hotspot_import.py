import json
import pytest
from whale_cursor.project import Project
from whale_cursor.hotspots import parse_hotspots
from whale_cursor.prompts import make_brief
from whale_cursor.ui import Window


def test_ai_metadata_multiframe_and_multiple_targets():
    p = Project.defaults()
    p.slots["Arrow"] = [p.slots["Arrow"][0].clone() for _ in range(3)]
    data = {
        "items": [
            {
                "id": "slots.Arrow",
                "cell_px": [512, 512],
                "timeline": [
                    {"frame": 1, "hotspot_px": [100, 200], "duration_ms": 150},
                    {"frame": 2, "hotspot_px": [120, 240]},
                    {"frame": 3, "hotspot_px": [140, 280]},
                ],
            },
            {"filename": "slots.Hand.png", "hotspot": {"x": 60, "y": 80}},
        ]
    }
    result = parse_hotspots(data, p, ("slots", "Arrow"))
    assert [(r.key, r.frame, r.x, r.y) for r in result] == [
        ("Arrow", 0, 50, 100),
        ("Arrow", 1, 60, 120),
        ("Arrow", 2, 70, 140),
        ("Hand", 0, 60, 80),
    ]
    assert result[0].duration == 150


def test_common_dictionary_formats_and_current_sequence():
    p = Project.defaults()
    p.slots["Arrow"] = [p.slots["Arrow"][0].clone() for _ in range(2)]
    changes = parse_hotspots(
        {"Arrow": [20, 30], "Hand": {"x": 40, "y": 50}}, p, ("slots", "Arrow")
    )
    assert len(changes) == 3 and changes[1].x == 20
    changes = parse_hotspots({"hotspots": [[11, 12], [21, 22]]}, p, ("slots", "Arrow"))
    assert [r.x for r in changes] == [11, 21]
    result = parse_hotspots(
        {"normalized": True, "hotspot": [0.5, 1]}, p, ("slots", "Arrow")
    )
    assert [(r.x, r.y) for r in result] == [(128, 255)] * 2
    assert (
        parse_hotspots({"hotspot_x": 42, "hotspot_y": 53}, p, ("slots", "Arrow"))[0].x
        == 42
    )


@pytest.mark.parametrize(
    "data",
    [
        {"Arrow": [-1, 22]},
        {"Arrow": [256, 0]},
        {"frames": [{"frame": 99, "hotspot": [0, 0]}]},
        {"items": [{"id": "slots.Missing", "hotspot": [2, 3]}]},
        {"frames": [{"frame": 1, "hotspot": [2, 3]}, {"frame": 1, "hotspot": [4, 5]}]},
        {"size": 0, "hotspot": [0, 0]},
    ],
)
def test_invalid_json_has_no_partial_mutation(data):
    p = Project.defaults()
    before = [(f.x, f.y) for f in p.slots["Arrow"]]
    with pytest.raises(ValueError):
        parse_hotspots(data, p, ("slots", "Arrow"))
    assert [(f.x, f.y) for f in p.slots["Arrow"]] == before


def test_import_is_one_undo_record_and_duration_is_opt_in(app):
    w = Window(Project.defaults())
    try:
        f = w.project.slots["Arrow"][0]
        before = (f.x, f.y, f.duration, f.image.tobytes())
        edits = parse_hotspots(
            {"hotspot": [10, 20], "duration_ms": 200}, w.project, ("slots", "Arrow")
        )
        w.apply_hotspot_changes(edits)
        assert (f.x, f.y, f.duration) == (10, 20, before[2])
        assert f.image.tobytes() == before[3]
        w.undo()
        f = w.project.slots["Arrow"][0]
        assert (f.x, f.y, f.duration) == before[:3]
        w.apply_hotspot_changes(edits, timing=True)
        assert f.duration == 200
    finally:
        w.save_timer.stop()
        w.close()
        w.deleteLater()
        app.processEvents()


def test_generated_prompt_manifest_is_importable():
    p = Project.defaults()
    text = make_brief(p, {"slots.Arrow": 1, "slots.Hand": 1}, 256, 4, "鲸鱼")
    data = json.loads(text[text.index("{") :])
    edits = parse_hotspots(data, p, ("slots", "Arrow"))
    assert {r.key for r in edits} == {"Arrow", "Hand"}
