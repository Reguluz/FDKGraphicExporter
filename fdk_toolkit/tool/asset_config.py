"""Decode Falcom's text/binary JSON resources without writing to the game.

Binary layout reference: eArmada8/kuro_mdl_tool, misc/kuro_decode_bin_json.py.
The reader below adds bounds/cycle checks and accepts bytes rather than filenames.
"""
from __future__ import annotations
import json
import math
import struct


def _without_comments(text):
    # Asset tool text JSON contains comments. Preserve quoted URLs and escapes.
    output, pos, quoted = [], 0, False
    while pos < len(text):
        ch = text[pos]
        if quoted:
            output.append(ch)
            if ch == '\\' and pos + 1 < len(text):
                pos += 1
                output.append(text[pos])
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True; output.append(ch)
        elif text.startswith('//', pos):
            end = text.find('\n', pos)
            pos = len(text) if end < 0 else end
            output.append('\n')
            continue
        elif text.startswith('/*', pos):
            end = text.find('*/', pos + 2)
            if end < 0:
                raise ValueError('Unterminated JSON comment')
            output.append(' ')
            pos = end + 2
            continue
        else:
            output.append(ch)
        pos += 1
    return ''.join(output)


def decode_asset_config(data):
    """Return a decoded JSON value; also usable for binary .mi model-info files."""
    if not isinstance(data, bytes):
        data = bytes(data)
    if data[:4] != b'JSON':
        return json.loads(_without_comments(data.rstrip(b'\0').decode('utf-8-sig')))
    if len(data) < 12:
        raise ValueError('Truncated Falcom binary JSON header')
    root, = struct.unpack_from('<I', data, 8)
    active = set()
    total = 0

    def unpack(fmt, pos):
        size = struct.calcsize(fmt)
        if pos < 0 or pos + size > len(data):
            raise ValueError(f'Binary JSON read exceeds bounds at {pos:#x}')
        return struct.unpack_from(fmt, data, pos), pos + size

    def string(pos):
        if not 0 <= pos < len(data):
            raise ValueError('Binary JSON string offset exceeds bounds')
        end = data.find(b'\0', pos)
        if end < 0:
            raise ValueError('Unterminated binary JSON string')
        return data[pos:end].decode('utf-8'), end + 1

    def node(offset, depth=0):
        nonlocal total
        total += 1
        if depth > 128 or total > 1000000 or offset in active:
            raise ValueError('Binary JSON cyclic or excessive structure')
        active.add(offset)
        try:
            (kind,), pos = unpack('<B', offset)
            name = ''
            if kind < 0x10:
                (key_offset,), pos = unpack('<I', pos)
                name, _ = string(key_offset + 4)  # string table prefix is CRC32.
            base = kind & 0x0f
            if kind not in (2, 3, 4, 5, 6, 0x12, 0x13, 0x14, 0x15, 0x16):
                raise ValueError(f'Unsupported binary JSON opcode {kind:#x} at {offset:#x}')
            if base == 2:
                value, _ = string(pos)
            elif base == 3:
                (value,), _ = unpack('<d', pos)
                if not math.isfinite(value):
                    raise ValueError('Nonfinite binary JSON number')
                if value.is_integer(): value = int(value)
            elif base == 6:
                (value,), _ = unpack('<B', pos)
                if value not in (0, 1): raise ValueError('Invalid binary JSON boolean')
                value = bool(value)
            else:
                (count,), pos = unpack('<I', pos)
                if count > (len(data) - pos) // 4:
                    raise ValueError('Binary JSON child count exceeds bounds')
                addresses, _ = unpack(f'<{count}I', pos)
                children = [node(address, depth + 1) for address in addresses]
                if base == 4:
                    if len({key for key, _ in children}) != len(children):
                        raise ValueError('Duplicate binary JSON object keys')
                    value = dict(children)
                else:
                    value = [item for _, item in children]
            return name, value
        finally:
            active.remove(offset)

    return node(root)[1]
