#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests of assemble/derive_jar.py.

Run from the repository root:
  python3 -B -m unittest takx/desktop-prototype/assemble/test_derive_jar.py
The ELF files and the JAR are synthetic and built in memory or in a
temporary directory; no toolchain and no assembled tree are needed.
"""
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import derive_jar  # noqa: E402

RUNPATH = '/build/a/lib:/build/b/lib:'


def elf(entries, strings):
    """A minimal ELF64 file: header, PT_LOAD over everything, PT_DYNAMIC, string table, dynamic section."""
    table = b'\0'
    offsets = {}
    for text in strings:
        offsets[text] = len(table)
        table += text.encode() + b'\0'
    strtab = 64 + 2 * 56
    dynamic = strtab + len(table)
    resolved = []
    for tag, value in entries:
        if tag == derive_jar.DT_STRTAB:
            value = strtab
        elif isinstance(value, str):
            value = offsets[value]
        resolved.append((tag, value))
    body = b''.join(struct.pack('<qQ', tag, value) for tag, value in resolved)
    size = dynamic + len(body)
    header = b'\x7fELF\x02\x01\x01' + b'\0' * 9 + struct.pack('<HHIQQQIHHHHHH', 3, 62, 1, 0, 64, 0, 0, 64, 56, 2, 0, 0, 0)
    load = struct.pack('<IIQQQQQQ', derive_jar.PT_LOAD, 5, 0, 0, 0, size, size, 4096)
    dyn = struct.pack('<IIQQQQQQ', derive_jar.PT_DYNAMIC, 6, dynamic, dynamic, dynamic, len(body), len(body), 8)
    return header + load + dyn + table + body, dynamic, len(body)


def library(with_runpath=True):
    entries = [(1, 'libfirst.so'), (1, 'libsecond.so'), (14, 'libself.so')]
    if with_runpath:
        entries.append((derive_jar.DT_RUNPATH, RUNPATH))
    entries += [(derive_jar.DT_STRTAB, 0), (10, 99), (derive_jar.DT_NULL, 0)]
    return elf(entries, ['libfirst.so', 'libsecond.so', 'libself.so', RUNPATH])


def font_index(names=None):
    names = derive_jar.FONT_ATLASES if names is None else names
    stems = sorted({name[len(derive_jar.FONT_DIR):].rsplit('.', 1)[0] for name in names})
    return json.dumps({'version': 3, 'atlases': [{'fontName': stem, 'style': 'regular', 'type': 'msdf',
                                                  'jsonFilename': stem + '.json', 'pngFilename': stem + '.png'}
                                                 for stem in stems]}).encode()


def kernel_jar(path, extra=None, index=None, without=()):
    entries = {'META-INF/MANIFEST.MF': b'Manifest-Version: 1.0\n', 'com/example/A.class': b'\xca\xfe\xba\xbe' * 50,
               'assets/style/x.json': b'{"a": 1}', derive_jar.FONT_INDEX: index or font_index()}
    for name in derive_jar.FONT_ATLASES:
        entries[name] = ('atlas ' + name).encode()
    entries['libs/linux64/libtakengine.so'] = library()[0]
    entries['libs/linux64/libtakenginejni.so'] = library()[0]
    entries['libs/linux64/libgdal.so'] = library()[0]
    entries.update(extra or {})
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in entries.items():
            if name in without:
                continue
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED if name.endswith('.class') else zipfile.ZIP_STORED
            archive.writestr(info, data)
    return entries


class ElfCase(unittest.TestCase):
    def test_run_path_is_read_and_removed(self):
        data, dynamic, size = library()
        self.assertEqual(derive_jar.run_paths(data), [('DT_RUNPATH', RUNPATH)])
        stripped, removed = derive_jar.strip_run_paths(data)
        self.assertEqual(removed, [('DT_RUNPATH', RUNPATH)])
        self.assertEqual(derive_jar.run_paths(stripped), [])
        self.assertEqual(len(stripped), len(data))
        self.assertEqual(stripped[:dynamic], data[:dynamic])
        self.assertEqual(stripped[dynamic + size:], data[dynamic + size:])
        self.assertIn(RUNPATH.encode(), stripped)
        tags = [struct.unpack_from('<qQ', stripped, dynamic + index * 16) for index in range(size // 16)]
        before = [struct.unpack_from('<qQ', data, dynamic + index * 16) for index in range(size // 16)]
        self.assertEqual(tags, [entry for entry in before if entry[0] != derive_jar.DT_RUNPATH] + [(0, 0)])

    def test_rpath_is_removed_too_and_nothing_changes_without_one(self):
        data, _, _ = elf([(1, 'liba.so'), (derive_jar.DT_RPATH, '/x:'), (derive_jar.DT_STRTAB, 0), (derive_jar.DT_RUNPATH, '/y'),
                          (derive_jar.DT_NULL, 0)], ['liba.so', '/x:', '/y'])
        stripped, removed = derive_jar.strip_run_paths(data)
        self.assertEqual(removed, [('DT_RPATH', '/x:'), ('DT_RUNPATH', '/y')])
        self.assertEqual(derive_jar.run_paths(stripped), [])
        plain, _, _ = library(with_runpath=False)
        self.assertEqual(derive_jar.strip_run_paths(plain), (plain, []))

    def test_not_an_elf_file(self):
        with self.assertRaises(derive_jar.DeriveError):
            derive_jar.run_paths(b'PK\x03\x04' + b'\0' * 100)
        with self.assertRaises(derive_jar.DeriveError):
            derive_jar.run_paths(b'\x7fELF\x01\x01' + b'\0' * 100)


class JarCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.source = os.path.join(self.temporary.name, 'kernel.jar')
        self.target = os.path.join(self.temporary.name, 'out', 'kernel-tree.jar')
        self.receipt = os.path.join(self.temporary.name, 'out', 'derive-receipt.json')

    def tearDown(self):
        self.temporary.cleanup()

    def run_main(self):
        with redirect_stdout(io.StringIO()):
            return derive_jar.main(['--input', self.source, '--output', self.target, '--receipt', self.receipt])

    def test_derivation(self):
        entries = kernel_jar(self.source)
        self.assertEqual(self.run_main(), 0)
        with open(self.receipt, encoding='utf-8') as stream:
            receipt = json.load(stream)
        self.assertEqual(len(receipt['removed']), 24)
        self.assertEqual(sorted(item['entry'] for item in receipt['changed']),
                         sorted((derive_jar.FONT_INDEX,) + derive_jar.STRIP_RUNPATH))
        self.assertEqual(receipt['unchanged_entries'], 4)
        self.assertEqual(receipt['input']['sha256'], derive_jar.sha256_file(self.source))
        self.assertEqual(receipt['output']['sha256'], derive_jar.sha256_file(self.target))
        self.assertEqual(receipt['output']['md5'], derive_jar.md5_file(self.target))
        with zipfile.ZipFile(self.target) as archive:
            names = archive.namelist()
            self.assertEqual(names, [name for name in entries if name not in derive_jar.FONT_ATLASES])
            self.assertEqual(json.loads(archive.read(derive_jar.FONT_INDEX)), {'version': 3, 'atlases': []})
            for name in derive_jar.STRIP_RUNPATH:
                self.assertEqual(derive_jar.run_paths(archive.read(name)), [])
            # A library this script is not told to touch keeps its bytes, RUNPATH included.
            self.assertEqual(archive.read('libs/linux64/libgdal.so'), entries['libs/linux64/libgdal.so'])
            self.assertEqual(archive.read('com/example/A.class'), entries['com/example/A.class'])
            info = archive.getinfo('com/example/A.class')
            self.assertEqual((info.date_time, info.compress_type), ((2020, 1, 1, 0, 0, 0), zipfile.ZIP_DEFLATED))
        for item in receipt['changed']:
            if item['entry'] in derive_jar.STRIP_RUNPATH:
                self.assertEqual(item['removed_run_paths'], [{'kind': 'DT_RUNPATH', 'value': RUNPATH}])
                self.assertEqual(item['bytes_before'], item['bytes_after'])
                self.assertNotEqual(item['sha256_before'], item['sha256_after'])

    def test_same_input_gives_the_same_output(self):
        kernel_jar(self.source)
        self.assertEqual(self.run_main(), 0)
        first = derive_jar.sha256_file(self.target)
        os.remove(self.target)
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(derive_jar.sha256_file(self.target), first)

    def test_an_unexpected_font_file_stops_the_derivation(self):
        kernel_jar(self.source, extra={'assets/fonts/tahoma_msdf.png': b'new atlas'})
        with self.assertRaises(SystemExit):
            self.run_main()
        self.assertFalse(os.path.exists(self.target))
        self.assertFalse(os.path.exists(self.receipt))

    def test_a_missing_atlas_or_library_stops_the_derivation(self):
        kernel_jar(self.source, without=('assets/fonts/arial_msdf.png',))
        with self.assertRaises(SystemExit):
            self.run_main()
        os.remove(self.source)
        kernel_jar(self.source, without=('libs/linux64/libtakenginejni.so',))
        with self.assertRaises(SystemExit):
            self.run_main()

    def test_an_index_that_lists_other_files_stops_the_derivation(self):
        kernel_jar(self.source, index=font_index(derive_jar.FONT_ATLASES[:-2] + ('assets/fonts/other_msdf.json',
                                                                                'assets/fonts/other_msdf.png')))
        with self.assertRaises(SystemExit):
            self.run_main()
        self.assertFalse(os.path.exists(self.target))

    def test_an_existing_output_is_not_overwritten(self):
        kernel_jar(self.source)
        os.makedirs(os.path.dirname(self.target))
        with open(self.target, 'wb') as stream:
            stream.write(b'keep')
        with self.assertRaises(SystemExit):
            self.run_main()
        with open(self.target, 'rb') as stream:
            self.assertEqual(stream.read(), b'keep')


if __name__ == '__main__':
    unittest.main()
