# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Capture validation and delivered image coordinates retain their SDK contracts."""

import base64
import binascii
import struct
import weakref
import zlib
from dataclasses import replace
from types import MappingProxyType

import pytest

from computer_use.errors import ComputerUseError
from computer_use.protocol.captures import capture_metadata, decode_capture
from computer_use.protocol.png import PNG, decode_png, png_size
from computer_use.session.desktop_store import DesktopStore
from computer_use.session.image_coordinates import ImageCoordinates
from computer_use.session.images import image_metadata, register_image


def chunk(kind, data=b""):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def image(scanlines, *, separate=False):
    header = chunk(b"IHDR", struct.pack(">IIBBBBB", 5, 5, 8, 0, 0, 0, 1))
    # Chunk names use the original Node ASCII conversion. The CRC retains the high bit.
    masked_idat = bytes(byte | 128 for byte in b"IDAT")
    compressed = zlib.compress(scanlines) + b"unused bytes after the completed zlib stream"
    payload = chunk(masked_idat, compressed[:5])
    if separate:
        payload += chunk(b"tEXt", b"discontinuous")
    return b"\x89PNG\r\n\x1a\n" + header + payload + chunk(b"IDAT", compressed[5:]) + chunk(b"IEND")


@pytest.fixture
def scanlines():
    # Grayscale Adam7 pass row counts and byte lengths, including each filter byte.
    return b"".join(bytes(count * length) for count, length in ((1, 2), (1, 2), (1, 3), (2, 2), (1, 4), (3, 3), (2, 6)))


def encoded(value):
    return base64.b64encode(value).decode("ascii")


def test_adam7_image_preserves_png_type_and_identity_callback(scanlines):
    original = image(scanlines)
    png = decode_png(encoded(original))
    assert type(png) is PNG
    assert png_size(memoryview(png)) == {"width": 5, "height": 5}
    callbacks = []

    def copied(source, value):
        callbacks.append(source is png)
        return value

    png._bytes_copied = copied
    assert bytes(png) == original
    assert callbacks == [True]


def test_capture_and_delivery_metadata_follow_exact_image_lifetime(scanlines):
    pixels = encoded(image(scanlines))

    def capture(identity):
        return decode_capture(
            {
                "id": identity,
                "kind": "window",
                "scope": identity,
                "startedAt": 1,
                "completedAt": 2,
                "width": 5,
                "height": 5,
                "png": pixels,
            },
            "window",
        )

    first, second = capture("first"), capture("second")
    assert first == second and first is not second
    assert capture_metadata(first)["scope"] == "first"
    assert capture_metadata(second)["scope"] == "second"
    assert capture_metadata(bytes(first)) is None

    register_image(first, {}, lambda _image: "")
    alias = bytes(first)
    equal_bytes = memoryview(alias).tobytes()
    assert bytes(first) is alias
    assert equal_bytes == alias and equal_bytes is not alias
    assert image_metadata(alias) is capture_metadata(first)
    assert image_metadata(equal_bytes) is None
    assert image_metadata(second) is None

    register_image(first, {}, lambda _image: "")
    assert image_metadata(alias) is None
    new_alias = bytes(first)
    assert new_alias is not alias
    assert image_metadata(new_alias) is capture_metadata(first)

    weak_image = weakref.ref(first)
    del first
    assert weak_image() is None
    assert image_metadata(new_alias) is None
    assert capture_metadata(second)["scope"] == "second"


@pytest.mark.parametrize("failure", ["bad_filter", "extra_scanline_byte", "interrupted_idat", "bad_crc", "bad_padding"])
def test_png_rejects_invalid_capture_bytes(scanlines, failure):
    if failure == "bad_filter":
        png = image(scanlines[:-6] + b"\x05" + scanlines[-5:])
    elif failure == "extra_scanline_byte":
        png = image(scanlines + b"\0")
    elif failure == "interrupted_idat":
        png = image(scanlines, separate=True)
    elif failure == "bad_crc":
        png = image(scanlines)
        png = png[:-1] + bytes([png[-1] ^ 1])
    else:
        png = image(scanlines)
    value = encoded(png)
    if failure == "bad_padding":
        value += "="
    with pytest.raises(ValueError, match="protocol: invalid PNG Base64 or corrupt PNG data"):
        decode_png(value)


@pytest.mark.parametrize(
    ("value", "context_type", "detail"),
    [
        ("!", binascii.Error, "Only base64 data is allowed"),
        ("\ud800", ValueError, "string argument should contain only ASCII characters"),
        ("QR==", None, None),
    ],
)
def test_png_preserves_base64_error_context(value, context_type, detail):
    with pytest.raises(ValueError, match="protocol: invalid PNG Base64 or corrupt PNG data") as caught:
        decode_png(value)
    error = caught.value
    assert error.__cause__ is None and not error.__suppress_context__
    if context_type is None:
        assert error.__context__ is None
    else:
        assert type(error.__context__) is context_type and str(error.__context__) == detail


@pytest.mark.parametrize("compressed", [b"\x00\x00", bytes.fromhex("789c63406060000000040001")])
def test_png_preserves_failed_inflater_context(compressed):
    header = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    png = encoded(b"\x89PNG\r\n\x1a\n" + header + chunk(b"IDAT", compressed) + chunk(b"IEND"))
    with pytest.raises(zlib.error) as failed:
        zlib.decompressobj().decompress(compressed, 5)
    with pytest.raises(ValueError, match="protocol: invalid PNG Base64 or corrupt PNG data") as caught:
        decode_png(png)
    assert isinstance(caught.value.__context__, zlib.error)
    assert str(caught.value.__context__) == str(failed.value)
    assert caught.value.__cause__ is None and not caught.value.__suppress_context__


def test_png_preserves_subclass_encoding_exception_chain():
    class BrokenEncoding(str):
        calls = 0

        def encode(self, encoding):
            self.calls += 1
            raise UnicodeEncodeError(encoding, "\u2603", 0, 1, "ordinal")

    value = BrokenEncoding("QR==")
    with pytest.raises(ValueError, match="protocol: invalid PNG Base64 or corrupt PNG data") as caught:
        decode_png(value)
    error = caught.value.__context__
    assert type(error) is ValueError and str(error) == "string argument should contain only ASCII characters"
    assert error.__cause__ is None and not error.__suppress_context__
    assert isinstance(error.__context__, UnicodeEncodeError)
    assert value.calls == 1


def test_png_size_reads_header_without_claiming_a_full_png(scanlines):
    value = image(scanlines)
    assert png_size(bytearray(value[:24])) == {"width": 5, "height": 5}
    with pytest.raises(ValueError, match="protocol: invalid PNG dimensions"):
        png_size(value[:16] + b"\0" * 4 + value[20:24])
    with pytest.raises(ValueError, match="protocol: invalid PNG header"):
        png_size(value[:23])


def test_image_transform_preserves_integer_products_and_dataclass_replacement():
    source = (2**53 + 1, 200)
    delivered = (2**53 + 7, 100)
    point = (2**53 + 5, 11.5)
    coordinates = ImageCoordinates(source, delivered)
    assert coordinates.source_point(point) == (point[0] * source[0] / delivered[0], 23.0)
    half = replace(coordinates, delivered_size=(50, 50))
    assert half.delivered_rect({"x": 5, "y": 8, "width": 2, "height": 12}) == {
        "x": 5 * (50 / source[0]),
        "y": 2.0,
        "width": 2 * (50 / source[0]),
        "height": 3.0,
    }
    with pytest.raises(ComputerUseError, match="coordinates are outside the delivered image"):
        coordinates.source_point((delivered[0], 0))
    with pytest.raises(ValueError, match="Image dimensions must be positive integers"):
        ImageCoordinates((True, 1), (1, 1))


def test_image_drag_transforms_only_pointer_fields_and_uses_point_override():
    class Shifted(ImageCoordinates):
        def source_point(self, point):
            x, y = super().source_point(point)
            return x + 10, y + 20

    coords = Shifted((200, 300), (100, 100))
    button = object()
    parameters = MappingProxyType({"fromX": 4, "fromY": 5, "toX": 6, "toY": 7, "button": button})
    assert coords.source_parameters("drag", parameters) == {"fromX": 18, "fromY": 35, "toX": 22, "toY": 41, "button": button}
    assert list(parameters.values()) == [4, 5, 6, 7, button]
    with pytest.raises(KeyError, match="toX"):
        coords.source_parameters("drag", {"fromX": 0, "fromY": 0})


def test_desktop_point_requires_one_captured_display_after_the_delivery_transform():
    displays = [
        {"captured": True, "rect": {"x": 0, "y": 0, "width": 10, "height": 10}},
        {"captured": False, "rect": {"x": 8, "y": 0, "width": 10, "height": 10}},
    ]
    state = {"observation": {"displays": displays}}
    coordinates = ImageCoordinates((20, 20), (10, 10))
    assert DesktopStore._check_point(state, coordinates, 3, 2) == (6, 4)
    for x in (4, 5, 9):
        with pytest.raises(ComputerUseError, match="outside a captured display"):
            DesktopStore._check_point(state, coordinates, x, 2)
    with pytest.raises(ComputerUseError, match="finite, nonnegative"):
        DesktopStore._check_point(state, None, 10**1000, 2)
