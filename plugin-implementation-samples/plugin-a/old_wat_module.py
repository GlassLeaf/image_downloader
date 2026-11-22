"""``test.wat`` の公開 API を Python で扱える形にした移植版。

WAT は wasm-bindgen が生成した低レベル表現なので、局所変数名は ``a`` や
``j`` になっている。このモジュールでは、外部 API の引数順は保持しながら、
内部ではデータ形式から推定できる用途名を使う。
"""

import base64
import math
import struct
from collections.abc import Sequence
from typing import Any

ByteInput = str | bytes | bytearray | Sequence[int]


def _as_bytes(value: ByteInput, length: int | None = None) -> bytes:
    """Wasm 側のポインタと長さの組を Python の bytes に対応付ける。"""
    if isinstance(value, str):
        raw_bytes = value.encode("utf-8")
    elif isinstance(value, (bytes, bytearray)):
        raw_bytes = bytes(value)
    else:
        raw_bytes = bytes(value)
    return raw_bytes if length is None else raw_bytes[:length]




class TestWatModule:
    """WAT の線形メモリと公開関数を保持する小さな Python 実行コンテキスト。"""

    def __init__(self, initial_memory: bytes | bytearray | None = None) -> None:
        self.memory = bytearray(initial_memory or b"")

    # wasm-bindgen の公開メモリ API。Python では解放済みブロックを再利用せず、
    # 呼び出し側がポインタを観測できる点だけを再現する。
    def wbindgen_malloc(self, byte_count: int, alignment: int = 1) -> int:
        if byte_count < 0 or alignment <= 0:
            raise ValueError("byte_count must be non-negative and alignment must be positive")
        allocation_start = (len(self.memory) + alignment - 1) // alignment * alignment
        self.memory.extend(b"\x00" * (allocation_start + byte_count - len(self.memory)))
        return allocation_start

    def wbindgen_realloc(
        self, pointer: int, old_byte_count: int, new_byte_count: int, alignment: int = 1
    ) -> int:
        if pointer < 0 or old_byte_count < 0 or pointer + old_byte_count > len(self.memory):
            raise ValueError("invalid source allocation")
        replacement_pointer = self.wbindgen_malloc(new_byte_count, alignment)
        copied_byte_count = min(old_byte_count, new_byte_count)
        self.memory[replacement_pointer:replacement_pointer + copied_byte_count] = self.memory[
            pointer:pointer + copied_byte_count
        ]
        return replacement_pointer

    def wbindgen_free(self, pointer: int, byte_count: int, alignment: int = 1) -> None:
        """WAT の free に対応する API。Python の bytearray では解放不要。"""
        if pointer < 0 or byte_count < 0 or alignment <= 0:
            raise ValueError("invalid allocation")

    def create_data(
        self,
        encoded_records: ByteInput,
        record_byte_length: int,
        horizontal_extent: float,
        vertical_extent: float,
        first_key: ByteInput,
        first_key_length: int,
        second_key: ByteInput,
        second_key_length: int,
    ) -> tuple[int,int]:
        """WAT の ``createData`` を移植し、``(byte_offset, int32_count)`` を返す。

        レコードは 8 バイト単位。先頭レコードの bit 27..29 と第 2 word の
        最上位バイトをヘッダとして読み、以降のレコードを Int32 9 要素へ展開する。
        戻り値の領域は :meth:`subarray` で JavaScript と同様に読める。
        """
        record_bytes = _as_bytes(encoded_records, record_byte_length)
        if len(record_bytes) < 8:
            return (0, 0)

        first_key_bytes = _as_bytes(first_key, first_key_length)
        first_key_hash = 100
        for key_byte in first_key_bytes:
            # WAT: (hash * 31 + byte) & 4095
            first_key_hash = (first_key_hash * 31 + key_byte) & 0xFFF

        second_key_bytes = _as_bytes(second_key, second_key_length)
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
            return (0, 0)

        horizontal_tile_count = math.ceil(horizontal_extent / grid_span)
        vertical_tile_count = math.ceil(vertical_extent / grid_span)
        horizontal_remainder = int(horizontal_extent) - horizontal_tile_count * border_width
        vertical_remainder = int(vertical_extent) - vertical_tile_count * border_width

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
                horizontal_remainder - horizontal_position
                if horizontal_tile == last_horizontal_tile
                else inner_span
            ) + border_width
            vertical_size = (
                vertical_remainder - vertical_position
                if vertical_tile == last_vertical_tile
                else inner_span
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

        memory_padding = (-len(self.memory)) % 4
        self.memory.extend(b"\x00" * memory_padding)
        result_byte_offset = len(self.memory)
        self.memory.extend(struct.pack(f"<{len(int32_values)}i", *int32_values))
        return result_byte_offset, len(int32_values)

    def get_array(self,source: str | bytes | bytearray, length: int | None = None) -> list[Any]:
        """WAT の ``getArray(a, b)`` を移植する。

        入力の先頭 66 バイトは識別子であり、残りは Base64 である。復号結果は
        ``uint16 little-endian の要素数 + その個数の 8-byte レコード`` の繰り返し。
        要素数 0 は WAT が JavaScript の ``null`` として返すため ``None`` にする。
        """
        if source is None:
            return []

        input_bytes = _as_bytes(source, length)
        base64_offset = 66
        if len(input_bytes) < base64_offset + 1:
            return []

        # WAT の ``aa[66] < -64``: UTF-8 継続バイトなら Base64 の開始位置として無効。
        if 0x80 <= input_bytes[base64_offset] <= 0xBF:
            return []

        try:
            decoded_bytes = base64.b64decode(input_bytes[base64_offset:], validate=True)
        except Exception:
            return []

        records_per_group: list[Any] = []
        read_offset = 0
        while read_offset + 2 <= len(decoded_bytes):
            (record_count,) = struct.unpack_from("<H", decoded_bytes, read_offset)
            read_offset += 2
            record_byte_length = record_count * 8
            if record_count == 0:
                records_per_group.append(None)
            elif read_offset + record_byte_length > len(decoded_bytes):
                break
            else:
                records_per_group.append(list(decoded_bytes[read_offset:read_offset + record_byte_length]))
                read_offset += record_byte_length
        return records_per_group

    def subarray(self, byte_offset: int, int32_count: int) -> list[int]:
        """JS の ``Int32Array(memory).subarray(offset / 4, offset / 4 + count)``。"""
        if byte_offset < 0 or int32_count <= 0 or byte_offset + int32_count * 4 > len(self.memory):
            return []
        return list(struct.unpack_from(f"<{int32_count}i", self.memory, byte_offset))
