import sys
import pytest
from whale_cursor import native
from whale_cursor.project import Project
from whale_cursor.codec import encode_cur, encode_ani, SIZES


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows loader only; no registry changes"
)
def test_windows_loads_cur_and_ani(tmp_path):
    p = Project.defaults()
    f = p.slots["Arrow"][0]
    for size in SIZES:
        path = tmp_path / f"{size}.cur"
        path.write_bytes(encode_cur(f, (size,)))
        native.validate_file(path, size)
        path = tmp_path / f"{size}.ani"
        path.write_bytes(encode_ani([f, f.clone()], (size,)))
        native.validate_file(path, size)
