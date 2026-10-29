import json
import zipfile
import pytest
from whale_cursor.project import Project
from whale_cursor.codec import decode_ani, decode_cur


def test_portable_project_roundtrip(tmp_path):
    p = Project.defaults()
    p.name = "测试方案"
    p.slots["Arrow"].append(p.slots["Arrow"][0].clone())
    p.slots["Arrow"][1].x = 50
    p.slots["Arrow"][1].duration = 250
    p.events["click"] = [p.slots["Hand"][0].clone()]
    p.settings["trail"] = True
    path = tmp_path / "portable.whalepack"
    p.save(path)
    q = Project.load(path)
    assert q.name == "测试方案" and len(q.slots) == 17
    assert (
        q.slots["Arrow"][1].x == 50
        and q.slots["Arrow"][1].duration == 250
        and q.settings["trail"]
    )
    assert q.events["click"][0].image.tobytes() == p.events["click"][0].image.tobytes()


def test_export_mixed_and_single_only(tmp_path):
    p = Project.defaults()
    p.slots["Wait"].append(p.slots["Wait"][0].clone())
    paths = p.export(tmp_path / "all")
    assert len(paths) == 17
    assert paths["Wait"].endswith(".ani")
    assert len(decode_ani(open(paths["Wait"], "rb").read())) == 2
    assert decode_cur(open(paths["Arrow"], "rb").read()).image.size == (256, 256)
    assert list(p.export(tmp_path / "one", "Hand")) == ["Hand"]


def test_atomic_save_keeps_previous_on_failure(tmp_path):
    p = Project.defaults()
    path = tmp_path / "p.whalepack"
    p.save(path)
    before = path.read_bytes()
    p.slots["Arrow"][0].x = 999
    with pytest.raises(ValueError):
        p.save(path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_invalid_version(tmp_path):
    path = tmp_path / "bad.whalepack"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("project.json", json.dumps({"version": 999, "size": 64}))
    with pytest.raises(ValueError):
        Project.load(path)
