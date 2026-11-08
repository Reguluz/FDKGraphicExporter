"""Read-only, bounded FPAC access for Falcom's PAC archives.

Format independently implemented from inspected files and public documentation:
https://github.com/coinkillerl/FPACker#the-fpac-file-format
https://github.com/eArmada8/kuro_dlc_tool/blob/main/sky_extract_pac.py

FPAC itself is uncompressed. Entry bytes may carry a separate game-specific
wrapper; this module deliberately leaves that payload unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path, PurePosixPath
import struct


@dataclass(frozen=True)
class PacEntry:
    path: str
    offset: int
    size: int
    hash: int

    @property
    def stored_size(self):
        return self.size

    @property
    def compressed_size(self):
        return self.size

    @property
    def uncompressed_size(self):
        # Archive-level size only, not the size after a payload wrapper decode.
        return self.size

    def as_dict(self):
        return asdict(self)


class PacArchive:
    """Index a PAC without extracting it; each read opens its own file handle.

    Safe for concurrent readers. Rejects bad offsets and unsafe virtual paths.
    A modified source archive requires constructing a fresh index.
    """
    def __init__(self, filename):
        self.path = Path(filename).resolve()
        self.file_size = self.path.stat().st_size
        self.signature = (self.file_size, self.path.stat().st_mtime_ns)
        with self.path.open('rb') as stream:
            header = stream.read(16)
            if len(header) != 16:
                raise ValueError(f'Truncated FPAC header: {self.path}')
            magic, count, header_size, version = struct.unpack('<4sIII', header)
            if magic != b'FPAC':
                raise ValueError(f'Not an FPAC archive: {self.path}')
            if version != 1:
                raise ValueError(f'Unsupported FPAC header version {version}: {self.path}')
            table_end = 16 + count * 32
            if not table_end <= header_size <= self.file_size or header_size > 256 * 1024 * 1024:
                raise ValueError(f'Invalid or excessive FPAC index size: {self.path}')
            blob = header + stream.read(header_size - 16)
            if len(blob) != header_size:
                raise ValueError(f'Truncated FPAC index: {self.path}')
        self.version, self.header_size = version, header_size
        entries = []
        by_path = {}
        for index in range(count):
            hashed, name_offset, size, offset = struct.unpack_from('<4Q', blob, 16 + index * 32)
            if not table_end <= name_offset < header_size:
                raise ValueError(f'Invalid FPAC name offset at entry {index}')
            end = blob.find(b'\0', name_offset)
            if end < 0:
                raise ValueError(f'Unterminated FPAC name at entry {index}')
            name = blob[name_offset:end].decode('utf-8')
            path = PurePosixPath(name)
            if (not name or '\\' in name or ':' in name or path.is_absolute()
                    or '..' in path.parts or str(path) != name):
                raise ValueError(f'Unsafe or noncanonical FPAC path: {name!r}')
            if offset < header_size or offset + size > self.file_size:
                raise ValueError(f'FPAC entry exceeds archive bounds: {name}')
            if name in by_path:
                raise ValueError(f'Duplicate FPAC path: {name}')
            entry = PacEntry(name, offset, size, hashed)
            entries.append(entry)
            by_path[name] = entry
        self.entries = tuple(entries)
        self.by_path = by_path

    def __len__(self):
        return len(self.entries)

    def get(self, path):
        return self.by_path.get(path)

    def _entry(self, entry_or_path):
        entry = self.by_path[entry_or_path] if isinstance(entry_or_path, str) else entry_or_path
        if self.by_path.get(entry.path) != entry:
            raise ValueError('Entry does not belong to this FPAC index')
        stat = self.path.stat()
        if (stat.st_size, stat.st_mtime_ns) != self.signature:
            raise ValueError(f'FPAC changed after indexing; reload the index: {self.path}')
        return entry

    def read(self, entry_or_path):
        entry = self._entry(entry_or_path)
        return self._read(entry, entry.size)

    def read_prefix(self, entry_or_path, size=64):
        if not isinstance(size, int) or size < 0:
            raise ValueError('Prefix size must be a nonnegative integer')
        entry = self._entry(entry_or_path)
        return self._read(entry, min(size, entry.size))

    def _read(self, entry, size):
        with self.path.open('rb') as stream:
            stream.seek(entry.offset)
            result = stream.read(size)
        if len(result) != size:
            raise ValueError(f'Truncated FPAC entry: {entry.path}')
        return result


def read_resource(game_root, source):
    """Read the shared source notation 'relative/archive.pac::virtual/path'."""
    archive, path = source.split('::', 1)
    root = Path(game_root).resolve()
    candidate = (root / archive).resolve()
    candidate.relative_to(root)
    return PacArchive(candidate).read(path)
