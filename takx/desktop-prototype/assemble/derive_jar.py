#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Derive the Kernel JAR that the assembled tree ships from the Kernel JAR
that the Kernel stage built and verified.

The Kernel stage's JAR, its receipt and its controls are never changed. The
tree gets a COPY with three recorded differences, and this script writes a
receipt that names every one of them with the digest before and after:

1. The pre-generated font atlases are removed: assets/fonts/{arial,cour,
   segoeui}*_msdf.{json,png}, 24 files. Nothing states a licence for them and
   their names are those of Microsoft fonts. In Kernel 8.30.5 no code path
   opens them: GlyphAtlas_open, GlyphAtlas_loadBitmap and GetAtlasResource
   have no caller outside GlyphAtlas.cpp (GLGlyphAtlas2 renders glyphs at run
   time since 8.13.0, takkernel/VERSION.md).
2. assets/fonts/font_resources.json, the index of those atlases, is replaced
   by an index with no atlas, so that it does not name files that are gone.
3. DT_RUNPATH / DT_RPATH is removed from the dynamic section of the native
   libraries listed in STRIP_RUNPATH. The build left a RUNPATH of build
   directories that ends in an empty element; glibc reads an empty element
   as the working directory of the process, which the application user can
   write. Only the dynamic entry is removed (the entries after it move up
   one slot, as 'patchelf --remove-rpath' does); the path string stays in
   .dynstr as dead bytes and no other byte of the library changes.

Any other file below assets/fonts/ is an error: a new atlas must be looked at
before it ships. Every other entry of the JAR is copied with identical bytes,
name, order, date and compression method, and that is checked.

  derive_jar.py --input KERNEL.jar --output DERIVED.jar --receipt RECEIPT.json

The stock Kernel loader extracts its natives below
java.io.tmpdir/takkernel/engine/<MD5 of the JAR>/; the receipt carries the MD5
of the derived JAR for the launcher.
"""
import argparse
import hashlib
import json
import os
import struct
import sys
import zipfile

FONT_DIR = 'assets/fonts/'
FONT_INDEX = FONT_DIR + 'font_resources.json'
FONT_ATLASES = tuple(
    '%s%s_msdf.%s' % (FONT_DIR, stem, suffix)
    for stem in ('arial', 'arialbd', 'arialbi', 'ariali', 'cour', 'courbd', 'courbi', 'couri',
                 'segoeui', 'segoeuib', 'segoeuii', 'segoeuiz')
    for suffix in ('json', 'png'))
EMPTY_FONT_INDEX = b'{\n  "version": 3,\n  "atlases": []\n}\n'
STRIP_RUNPATH = ('libs/linux64/libtakengine.so', 'libs/linux64/libtakenginejni.so')

DT_NULL, DT_STRTAB, DT_RPATH, DT_RUNPATH = 0, 5, 15, 29
PT_LOAD, PT_DYNAMIC = 1, 2


class DeriveError(Exception):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def md5_file(path):
    # Not a security use: the stock Kernel loader names its extraction
    # directory after the MD5 of the JAR.
    digest = hashlib.new('md5', usedforsecurity=False)
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _dynamic(data):
    """Return (offset, size, [(tag, value)], load segments) of a little-endian ELF64 file."""
    if data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1:
        raise DeriveError('not a little-endian ELF64 file')
    phoff, = struct.unpack_from('<Q', data, 0x20)
    phentsize, phnum = struct.unpack_from('<HH', data, 0x36)
    found, loads = None, []
    for index in range(phnum):
        kind, _flags, offset, vaddr, _paddr, filesz, _memsz, _align = struct.unpack_from(
            '<IIQQQQQQ', data, phoff + index * phentsize)
        if kind == PT_DYNAMIC:
            found = (offset, filesz)
        elif kind == PT_LOAD:
            loads.append((vaddr, offset, filesz))
    if found is None:
        raise DeriveError('no PT_DYNAMIC segment')
    offset, size = found
    entries = []
    for position in range(offset, offset + size, 16):
        tag, value = struct.unpack_from('<qQ', data, position)
        entries.append((tag, value))
        if tag == DT_NULL:
            break
    else:
        raise DeriveError('the dynamic section has no DT_NULL entry')
    return offset, size, entries, loads


def run_paths(data):
    """Return the DT_RPATH and DT_RUNPATH strings of an ELF64 file, in section order."""
    _offset, _size, entries, loads = _dynamic(data)
    strtab = next((value for tag, value in entries if tag == DT_STRTAB), None)
    result = []
    for tag, value in entries:
        if tag not in (DT_RPATH, DT_RUNPATH):
            continue
        if strtab is None:
            raise DeriveError('DT_RPATH or DT_RUNPATH without DT_STRTAB')
        for vaddr, file_offset, filesz in loads:
            if vaddr <= strtab < vaddr + filesz:
                start = strtab - vaddr + file_offset + value
                end = data.index(b'\0', start)
                result.append(('DT_RPATH' if tag == DT_RPATH else 'DT_RUNPATH', data[start:end].decode('utf-8', 'replace')))
                break
        else:
            raise DeriveError('DT_STRTAB is outside every PT_LOAD segment')
    return result


def strip_run_paths(data):
    """Return (new bytes, removed [(kind, string)]) with every DT_RPATH and DT_RUNPATH entry removed.

    The remaining entries keep their order and move up; the freed slots at the
    end become DT_NULL. Nothing outside the dynamic section changes."""
    removed = run_paths(data)
    if not removed:
        return data, []
    offset, _size, entries, _loads = _dynamic(data)
    kept = [entry for entry in entries if entry[0] not in (DT_RPATH, DT_RUNPATH)]
    kept += [(DT_NULL, 0)] * (len(entries) - len(kept))
    out = bytearray(data)
    for index, (tag, value) in enumerate(kept):
        struct.pack_into('<qQ', out, offset + index * 16, tag, value)
    out = bytes(out)
    if run_paths(out) or len(out) != len(data):
        raise DeriveError('internal error: a run path survived')
    return out, removed


def _clone(info):
    clone = zipfile.ZipInfo(info.filename, info.date_time)
    clone.compress_type = info.compress_type
    clone.external_attr = info.external_attr
    clone.create_system = info.create_system
    clone.comment = info.comment
    clone.extra = info.extra
    return clone


def derive(source, target):
    """Write the derived JAR; return the receipt (without paths resolved by the caller)."""
    removed, changed, unchanged = [], [], hashlib.sha256()
    unchanged_count = 0
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names):
            raise DeriveError('duplicate entry names in %s' % source)
        fonts = sorted(name for name in names if name.startswith(FONT_DIR) and not name.endswith('/'))
        expected = sorted(FONT_ATLASES + (FONT_INDEX,))
        if fonts != expected:
            raise DeriveError('unexpected content of %s: extra %s, missing %s' % (
                FONT_DIR, sorted(set(fonts) - set(expected)), sorted(set(expected) - set(fonts))))
        for name in STRIP_RUNPATH:
            if name not in names:
                raise DeriveError('%s is not in %s' % (name, source))
        with zipfile.ZipFile(target, 'w') as out:
            for info in archive.infolist():
                data = archive.read(info)
                name = info.filename
                if name in FONT_ATLASES:
                    removed.append({'entry': name, 'bytes': len(data), 'sha256': sha256_bytes(data),
                                    'reason': 'font atlas without a stated licence; not opened by Kernel 8.30.5'})
                    continue
                new = data
                if name == FONT_INDEX:
                    listed = sorted(FONT_DIR + atlas[key] for atlas in json.loads(data)['atlases']
                                    for key in ('jsonFilename', 'pngFilename'))
                    if listed != sorted(FONT_ATLASES):
                        raise DeriveError('%s lists other files than the atlases removed here' % FONT_INDEX)
                    new = EMPTY_FONT_INDEX
                    changed.append({'entry': name, 'change': 'index of the removed atlases replaced by an empty index',
                                    'sha256_before': sha256_bytes(data), 'sha256_after': sha256_bytes(new),
                                    'bytes_before': len(data), 'bytes_after': len(new)})
                elif name in STRIP_RUNPATH:
                    new, paths = strip_run_paths(data)
                    changed.append({'entry': name,
                                    'change': 'dynamic entries removed: ' + (', '.join(kind for kind, _ in paths) or 'none present'),
                                    'removed_run_paths': [{'kind': kind, 'value': value} for kind, value in paths],
                                    'sha256_before': sha256_bytes(data), 'sha256_after': sha256_bytes(new),
                                    'bytes_before': len(data), 'bytes_after': len(new),
                                    'bytes_that_differ': sum(1 for a, b in zip(data, new) if a != b)})
                else:
                    unchanged.update(('%s %s\n' % (sha256_bytes(data), name)).encode())
                    unchanged_count += 1
                out.writestr(_clone(info), new)
    # Read the result back: everything that was not named above must be
    # identical, in the same order.
    check, count = hashlib.sha256(), 0
    special = set(STRIP_RUNPATH) | {FONT_INDEX}
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise DeriveError('the derived JAR has a damaged entry')
        for info in archive.infolist():
            if info.filename in FONT_ATLASES:
                raise DeriveError('%s is still in the derived JAR' % info.filename)
            if info.filename in special:
                continue
            check.update(('%s %s\n' % (sha256_bytes(archive.read(info)), info.filename)).encode())
            count += 1
    if check.hexdigest() != unchanged.hexdigest() or count != unchanged_count:
        raise DeriveError('entries that must not change differ between %s and %s' % (source, target))
    return {'schema': 1,
            'removed': removed,
            'changed': changed,
            'unchanged_entries': unchanged_count,
            'unchanged_entries_sha256': unchanged.hexdigest(),
            'unchanged_entries_note': 'SHA-256 over "<sha256 of content> <name>\\n" of every other entry in JAR order; '
                                      'computed from the input and again from the output'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--receipt', required=True)
    options = parser.parse_args(argv)
    if os.path.exists(options.output):
        raise SystemExit('derive_jar: output %s exists' % options.output)
    os.makedirs(os.path.dirname(os.path.abspath(options.output)), exist_ok=True)
    try:
        receipt = derive(options.input, options.output)
    except DeriveError as error:
        if os.path.exists(options.output):
            os.remove(options.output)
        raise SystemExit('derive_jar: %s' % error) from error
    receipt['input'] = {'path': os.path.abspath(options.input), 'sha256': sha256_file(options.input),
                        'bytes': os.path.getsize(options.input)}
    receipt['output'] = {'path': os.path.abspath(options.output), 'sha256': sha256_file(options.output),
                         'bytes': os.path.getsize(options.output), 'md5': md5_file(options.output)}
    receipt['note'] = ('derived copy for the assembled tree; the Kernel stage JAR, its receipt and its controls '
                       'describe the input, not this file')
    temporary = options.receipt + '.tmp.%d' % os.getpid()
    with open(temporary, 'w', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=1)
        stream.write('\n')
    os.replace(temporary, options.receipt)
    print(json.dumps({'removed': len(receipt['removed']), 'changed': len(receipt['changed']),
                      'unchanged': receipt['unchanged_entries'], 'output_sha256': receipt['output']['sha256']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
