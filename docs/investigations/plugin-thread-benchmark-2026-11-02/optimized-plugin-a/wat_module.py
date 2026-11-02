"""Stateless Python implementation of the plugin's WAT record format."""

from __future__ import annotations

import base64
import math
import struct


def decode_placeholder(source: str) -> tuple[bytes | None, ...]:
    """Decode complete immutable record groups; reject truncated data."""
    if not isinstance(source, str):
        raise ValueError("placeholder must be a string")
    raw = source.encode("utf-8")
    if len(raw) < 67:
        raise ValueError("placeholder header or Base64 data is missing")
    try:
        decoded = base64.b64decode(raw[66:], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid placeholder Base64") from exc
    groups: list[bytes | None] = []
    offset = 0
    while offset < len(decoded):
        if offset + 2 > len(decoded):
            raise ValueError("truncated placeholder group header")
        (count,) = struct.unpack_from("<H", decoded, offset)
        offset += 2
        size = count * 8
        if offset + size > len(decoded):
            raise ValueError("truncated placeholder records")
        groups.append(decoded[offset : offset + size] if count else None)
        offset += size
    return tuple(groups)


def build_transform_table(records: bytes, width: int, height: int, iv: str, salt: str) -> tuple[int, ...]:
    """Return the original WAT integer table without emulated linear memory."""
    record_bytes = records
    if not isinstance(records, bytes) or len(records) % 8:
        raise ValueError("records must contain complete 8-byte records")
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    if len(record_bytes) < 8:
        raise ValueError("invalid transform header or empty records")

    first_key_bytes = iv.encode("utf-8")
    first_key_hash = 100
    for key_byte in first_key_bytes:
        # WAT: (hash * 31 + byte) & 4095
        first_key_hash = (first_key_hash * 31 + key_byte) & 0xFFF

    second_key_bytes = salt.encode("utf-8")
    second_key_hash = 2
    for key_byte in second_key_bytes:
        # WAT: (hash ^ byte) % 10; 0 は 2 に正規化する。
        second_key_hash = (second_key_hash ^ key_byte) % 10
    multiplier = second_key_hash or 2

    header_word, header_metadata = struct.unpack_from("<II", record_bytes)
    grid_span = (header_metadata >> 24) & 0xFF
    type_code = (header_word >> 27) & 0x7
    border_width = type_code * 2
    inner_span = grid_span - border_width
    if grid_span == 0 or inner_span <= 0:
        raise ValueError("invalid transform header or empty records")

    horizontal_tile_count = math.ceil(width / grid_span)
    vertical_tile_count = math.ceil(height / grid_span)
    horizontal_remainder = int(width) - horizontal_tile_count * border_width
    vertical_remainder = int(height) - vertical_tile_count * border_width

    # 最初の 5 要素は、展開済みレコード群に共通するヘッダ。
    int32_values = [
        horizontal_remainder,
        vertical_remainder,
        first_key_hash,
        multiplier,
        type_code,
    ]
    metadata_scale = grid_span * multiplier
    last_horizontal_tile = horizontal_tile_count - 1
    last_vertical_tile = vertical_tile_count - 1

    for record_offset in range(0, len(record_bytes) // 8 * 8, 8):
        packed_word, packed_metadata = struct.unpack_from("<II", record_bytes, record_offset)
        horizontal_position = (packed_word >> 15) & 0xFFF
        vertical_position = (packed_word >> 3) & 0xFFF
        horizontal_tile = horizontal_position // inner_span
        vertical_tile = vertical_position // inner_span

        # select_if(value_if_true, value_if_false, condition) をそのまま移植。
        horizontal_size = (
            horizontal_remainder - horizontal_position if horizontal_tile == last_horizontal_tile else inner_span
        ) + border_width
        vertical_size = (
            vertical_remainder - vertical_position if vertical_tile == last_vertical_tile else inner_span
        ) + border_width

        orientation = (packed_word >> 1) & 0x3
        # br_table の結果は ca=1,3 で (vertical, horizontal)、
        # ca=0,2 で (horizontal, vertical) となる。
        if orientation in (1, 3):
            output_width, output_height = vertical_size, horizontal_size
        else:
            output_width, output_height = horizontal_size, vertical_size

        metadata_byte_2 = (packed_metadata >> 16) & 0xFF
        metadata_byte_1 = (packed_metadata >> 8) & 0xFF
        has_flag = packed_word & 1
        int32_values.extend(
            (
                (horizontal_position - type_code) * multiplier + first_key_hash,
                (vertical_position - type_code) * multiplier + first_key_hash,
                metadata_byte_2 * metadata_scale + first_key_hash,
                metadata_byte_1 * metadata_scale + first_key_hash,
                multiplier * orientation + first_key_hash,
                (multiplier if has_flag else 0) + first_key_hash,
                output_width,
                output_height,
                horizontal_tile_count * vertical_tile + horizontal_tile,
            )
        )

    return tuple(int32_values)
