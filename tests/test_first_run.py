import sys
import pytest
from PIL import Image
from whale_cursor import first_run, native
from whale_cursor.codec import Frame, encode_ani
from whale_cursor.project import Project, user_dir


def test_partial_and_empty_projects_never_fill_bundled_images(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Project, "defaults", classmethod(lambda cls: pytest.fail("bundled fill"))
    )
    for slots in ({}, {"Arrow": [Frame(Image.new("RGBA", (256, 256), "red"))]}):
        project = Project(slots=slots)
        path = tmp_path / "partial.whalepack"
        project.save(path)
        loaded = Project.load(path)
        assert loaded.slots.keys() == slots.keys()


def test_system_import_preserves_animation_hotspots_size_and_missing_roles(
    tmp_path, monkeypatch
):
    animation = tmp_path / "本机 忙碌.ani"
    frames = [
        Frame(Image.new("RGBA", (256, 256), color), 64, 32, duration)
        for color, duration in (("red", 100), ("blue", 200))
    ]
    animation.write_bytes(encode_ani(frames, (32,)))
    monkeypatch.setattr(native, "WINDOWS", True)
    monkeypatch.setattr(first_run, "cursor_paths", lambda: {"Wait": animation})
    monkeypatch.setattr(
        first_run,
        "capture_cursor",
        lambda role: (
            (Image.new("RGBA", (32, 32), "black"), 3, 4)
            if role in ("Arrow", "Wait")
            else None
        ),
    )
    project, warnings = first_run.read_system_project()
    assert not warnings
    assert project.size == 32 and project.settings["click_effect"] == "none"
    assert set(project.slots) == {"Arrow", "Wait"}
    assert [frame.duration for frame in project.slots["Wait"]] == [100, 200]
    assert (project.slots["Arrow"][0].x, project.slots["Arrow"][0].y) == (24, 32)
    animation.unlink()
    path = tmp_path / "分享.whalepack"
    project.save(path)
    assert len(Project.load(path).slots["Wait"]) == 2


def test_bad_source_falls_back_to_live_snapshot_without_using_demo(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(native, "WINDOWS", True)
    monkeypatch.setattr(
        first_run, "cursor_paths", lambda: {"Arrow": tmp_path / "missing.cur"}
    )
    monkeypatch.setattr(
        first_run,
        "capture_cursor",
        lambda role: (
            (Image.new("RGBA", (48, 48), "red"), 0, 0) if role == "Arrow" else None
        ),
    )
    project, warnings = first_run.read_system_project()
    assert project.size == 48 and warnings
    assert set(project.slots) == {"Arrow"}


def test_capture_alpha_handles_transparent_black_white_and_soft_edges():
    black = bytes([0, 0, 0, 0, 0, 0, 0, 0, 255, 255, 255, 0, 0, 0, 128, 0])
    white = bytes([255, 255, 255, 0, 0, 0, 0, 0, 255, 255, 255, 0, 127, 127, 255, 0])
    image = first_run.composite_capture(black, white, 4, 1)
    assert [image.getpixel((x, 0)) for x in range(4)] == [
        (0, 0, 0, 0),
        (0, 0, 0, 255),
        (255, 255, 255, 255),
        (255, 0, 0, 128),
    ]


def test_first_open_reads_local_once_bundles_demo_and_resumes(app, monkeypatch):
    from whale_cursor.ui import Window

    local = Project(
        name="本机原有指针",
        slots={"Arrow": [Frame(Image.new("RGBA", (256, 256), "red"), 8, 9)]},
    )
    calls = []
    monkeypatch.setattr(
        first_run, "read_system_project", lambda: (calls.append(1) or local.clone(), [])
    )
    monkeypatch.setattr(native, "recover_runtime", lambda: False)
    window = Window()
    assert calls == [1] and window.project.name == "本机原有指针"
    bundled = user_dir() / "schemes" / "鲸鱼娘（自带）.whalepack"
    assert len(Project.load(bundled).slots) == 17
    assert (user_dir() / "schemes" / "本机原有指针.whalepack").exists()
    window.project.slots["Arrow"][0].x = 100
    window.reset_slot()
    assert window.frame.x == 8
    window.project.name = "用户继续编辑"
    window.close()
    window.deleteLater()
    resumed = Window()
    assert calls == [1] and resumed.project.name == "用户继续编辑"
    resumed.close()
    resumed.deleteLater()
    app.processEvents()


def test_bundled_scheme_does_not_overwrite_existing_customization():
    path = first_run.bundled_scheme()
    project = Project.load(path)
    project.name = "保留用户改动"
    project.save(path)
    first_run.bundled_scheme()
    assert Project.load(path).name == "保留用户改动"


def test_file_only_animation_dimensions_and_static_live_colors(tmp_path, monkeypatch):
    from whale_cursor.codec import encode_cur

    file = tmp_path / "custom.ani"
    file.write_bytes(
        encode_ani([Frame(Image.new("RGBA", (256, 256), "blue"))] * 2, (48,))
    )
    static = tmp_path / "static.cur"
    static.write_bytes(encode_cur(Frame(Image.new("RGBA", (256, 256), "blue")), (32,)))
    monkeypatch.setattr(native, "WINDOWS", True)
    monkeypatch.setattr(
        first_run, "cursor_paths", lambda: {"NWPen": file, "Arrow": static}
    )
    monkeypatch.setattr(
        first_run,
        "capture_cursor",
        lambda role: (
            (Image.new("RGBA", (32, 32), "red"), 0, 0) if role == "Arrow" else None
        ),
    )
    project, _ = first_run.read_system_project()
    assert project.size == 48 and len(project.slots["NWPen"]) == 2
    assert project.slots["Arrow"][0].image.getpixel((10, 10)) == (255, 0, 0, 255)


def test_reset_baseline_follows_scheme_undo(app):
    from whale_cursor.ui import Window

    original = Project(
        slots={"Arrow": [Frame(Image.new("RGBA", (256, 256), "red"), 12, 20)]}
    )
    window = Window(original)
    changed = original.clone()
    changed.slots["Arrow"][0].x = 90
    window.set_project(changed)
    window.undo()
    window.project.slots["Arrow"][0].x = 123
    window.reset_slot()
    assert window.frame.x == 12
    window.close()
    window.deleteLater()
    app.processEvents()


@pytest.mark.skipif(
    sys.platform != "win32", reason="Read real Windows cursors without installing"
)
def test_windows_read_only_first_run_and_self_contained_export(tmp_path):
    before = native.snapshot()
    project, warnings = first_run.read_system_project()
    assert project.slots.get("Arrow") and len(project.slots) >= 14
    assert project.slots["Arrow"][0].image.getchannel("A").getbbox()
    for frames in project.slots.values():
        assert frames[0].image.getchannel("A").getbbox()
    assert native.snapshot() == before
    path = tmp_path / "便携 本机.whalepack"
    project.save(path)
    loaded = Project.load(path)
    exported = loaded.export(tmp_path / "导出")
    for filename in exported.values():
        native.validate_file(filename, project.size)
    assert native.snapshot() == before
