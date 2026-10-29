import struct
import pytest
from PIL import Image
from whale_cursor.codec import (
    Frame,
    encode_cur,
    decode_cur,
    encode_ani,
    decode_ani,
    normalize,
    split_sheet,
    transform,
    import_images,
    SIZES,
)


@pytest.fixture
def frame():
    im = Image.new("RGBA", (256, 256))
    im.paste((220, 30, 80, 127), (10, 20, 180, 200))
    im.putpixel((14, 26), (1, 2, 3, 255))
    return Frame(im, 22, 47, 100)


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("hot", [(0, 0), (22, 47), (128, 128), (255, 255)])
def test_cur_header_hotspot_and_decode(frame, size, hot):
    frame.x, frame.y = hot
    data = encode_cur(frame, (size,))
    reserved, typ, count = struct.unpack_from("<HHH", data)
    assert (reserved, typ, count) == (0, 2, 1)
    w, h, _, _, hx, hy, length, offset = struct.unpack_from("<BBBBHHII", data, 6)
    assert (w or 256, h or 256) == (size, size)
    assert (hx, hy) == tuple(min(size - 1, int(v * size / 256 + 0.5)) for v in hot)
    assert offset == 22 and offset + length == len(data)
    decoded = decode_cur(data)
    assert decoded.image.size == (256, 256)
    assert abs(decoded.x - hot[0]) <= max(1, 256 // size)
    assert abs(decoded.y - hot[1]) <= max(1, 256 // size)


def test_cur_alpha_exact(frame):
    out = decode_cur(encode_cur(frame, (256,)))
    assert out.image.tobytes() == frame.image.tobytes()
    assert out.image.getpixel((20, 30))[3] == 127


def test_multi_resolution_chooses_largest(frame):
    data = encode_cur(frame, SIZES)
    assert struct.unpack_from("<H", data, 4)[0] == 6
    assert decode_cur(data).image.tobytes() == frame.image.tobytes()


def test_ani_sequence_and_rates(frame):
    a = frame
    b = frame.clone()
    b.x = 100
    b.duration = 250
    data = encode_ani([a, b], (256,), sequence=[0, 1, 0])
    assert data[:4] == b"RIFF"
    assert len(data) == struct.unpack_from("<I", data, 4)[0] + 8
    out = decode_ani(data)
    assert len(out) == 3
    assert [v.x for v in out] == [22, 100, 22]
    assert [v.duration for v in out] == [100, 250, 100]
    assert out[0].image.tobytes() == a.image.tobytes()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"RIFF\0\0\0\0ACON",
        b"\x00\x00\x02\x00\x01\x00",
        b"RIFF\xff\xff\xff\xffACON",
    ],
)
def test_reject_malformed(data):
    with pytest.raises(ValueError):
        decode_ani(data)


def test_cur_reject_outside_payload(frame):
    data = bytearray(encode_cur(frame))
    struct.pack_into("<I", data, 18, 0xFFFFFF)
    with pytest.raises(ValueError):
        decode_cur(data)


def test_ani_reject_bad_sequence(frame):
    data = bytearray(encode_ani([frame]))
    i = data.index(b"seq ")
    struct.pack_into("<I", data, i + 8, 5)
    with pytest.raises(ValueError):
        decode_ani(data)


def test_sheet_order_and_non_square():
    im = Image.new("RGBA", (128, 64))
    im.paste("red", (0, 0, 64, 64))
    im.paste("blue", (64, 0, 128, 64))
    frames = split_sheet(im, 2, 1)
    assert frames[0].image.getpixel((128, 128))[:3] == (255, 0, 0)
    assert frames[1].image.getpixel((128, 128))[:3] == (0, 0, 255)
    rect = normalize(Image.new("RGBA", (100, 50), "red"))
    assert rect.getbbox() == (0, 64, 256, 192)
    with pytest.raises(ValueError):
        split_sheet(im, 3, 1)


def test_transform_preserves_hotspot_relation(frame):
    assert transform(frame, "flip_h").x == 233
    r = transform(frame, "rotate")
    assert (r.x, r.y) == (208, 22)
    t = transform(frame, "offset", dx=5, dy=-3)
    assert (t.x, t.y) == (27, 44)
    assert t.image.getpixel((19, 23)) == (1, 2, 3, 255)
    with pytest.raises(ValueError):
        transform(frame, "offset", dx=-100)
    with pytest.raises(ValueError):
        transform(frame, "crop", box=(100, 100, 200, 200))


def test_gif_duration_and_composition(tmp_path):
    a = Image.new("RGBA", (32, 32), "red")
    b = Image.new("RGBA", (32, 32), "blue")
    path = tmp_path / "a.gif"
    a.save(path, save_all=True, append_images=[b], duration=[100, 250], loop=0)
    frames = import_images(path)
    assert len(frames) == 2
    assert [f.duration for f in frames] == [100, 250]
    assert frames[1].image.getpixel((128, 128))[:3] == (0, 0, 255)
