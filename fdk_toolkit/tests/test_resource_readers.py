"""Small malformed-input regressions for the read-only resource adapters."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tool'))
from archive_pac import PacArchive
from asset_config import decode_asset_config


def packed(name='asset/common/model/test.mdl', payload=b'MDL test'):
    name_bytes = name.encode('utf-8') + b'\0'
    header_size = 48 + len(name_bytes)
    return (struct.pack('<4sIII', b'FPAC', 1, header_size, 1)
            + struct.pack('<4Q', 0, 48, len(payload), header_size)
            + name_bytes + payload)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.temporary.name) / 'test.pac'

    def tearDown(self):
        self.temporary.cleanup()

    def test_random_access_and_prefix(self):
        self.path.write_bytes(packed())
        archive = PacArchive(self.path)
        self.assertEqual(len(archive), 1)
        entry = archive.entries[0]
        self.assertEqual(archive.read(entry), b'MDL test')
        self.assertEqual(archive.read_prefix(entry.path, 4), b'MDL ')
        self.assertEqual(archive.read_prefix(entry, 999), b'MDL test')

    def test_reject_bad_offsets_and_paths(self):
        for name in ('../outside', 'C:/outside', '/absolute', 'asset\\model.mdl'):
            self.path.write_bytes(packed(name))
            with self.assertRaises(ValueError): PacArchive(self.path)
        content = bytearray(packed())
        struct.pack_into('<Q', content, 40, 2**63)
        self.path.write_bytes(content)
        with self.assertRaises(ValueError): PacArchive(self.path)
        self.path.write_bytes(packed()[:-2])
        with self.assertRaises(ValueError): PacArchive(self.path)

    def test_source_changed_requires_reindex(self):
        self.path.write_bytes(packed())
        archive = PacArchive(self.path)
        self.path.write_bytes(packed(payload=b'MDL changed'))
        with self.assertRaises(ValueError): archive.read(archive.entries[0])

    def test_text_comments_and_string_literals(self):
        data = b'\xef\xbb\xbf{ /*c*/ "url": "http://host/a/*literal*/", // c\n"n": 2}\x00'
        self.assertEqual(decode_asset_config(data), {'url': 'http://host/a/*literal*/', 'n': 2})
        with self.assertRaises(ValueError): decode_asset_config(b'{/* unfinished')

    def test_binary_values_and_bad_pointers(self):
        # Named double a=42 stored below an unnamed root object.
        data = (struct.pack('<4sII', b'JSON', 0, 20) + b'\0\0\0\0a\0\0\0'
                + struct.pack('<BII', 0x14, 1, 29) + struct.pack('<BId', 3, 12, 42.0))
        self.assertEqual(decode_asset_config(data), {'a': 42})
        bad = bytearray(data); struct.pack_into('<I', bad, 25, 20)
        with self.assertRaises(ValueError): decode_asset_config(bad)  # self-reference
        bad = bytearray(data); struct.pack_into('<I', bad, 25, 5000)
        with self.assertRaises(ValueError): decode_asset_config(bad)
        bad = bytearray(data); bad[29] = 0x77
        with self.assertRaises(ValueError): decode_asset_config(bad)


if __name__ == '__main__':
    unittest.main(verbosity=2)
