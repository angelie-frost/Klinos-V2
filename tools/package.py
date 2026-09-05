#!/usr/bin/env python3
"""Zip the plugin for distribution, bumping the version number each run.

    python3 tools/package.py                 -> dist/klinos_v1.zip
    python3 tools/package.py                 -> dist/klinos_v2.zip
    python3 tools/package.py --name export   -> dist/export_v3.zip
    python3 tools/package.py --version 7     -> dist/klinos_v7.zip

The next number comes from scanning the output folder, so deleting old zips
rewinds the count and nothing is stored between runs. Version also gets written
into package.json and manifest.json so a downloaded zip can be traced back.
"""
import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INCLUDE_FILES = ['manifest.json', 'code.js', 'ui.html',
                 'README.md', 'SETUP.md', 'package.json', 'jsconfig.json']
INCLUDE_DIRS = ['tools', '.vscode']
SKIP = {'.DS_Store', '__pycache__', '.pyc'}


def next_version(outdir: Path, name: str) -> int:
    pattern = re.compile(re.escape(name) + r'_v(\d+)\.zip$')
    highest = 0
    for f in outdir.glob(f'{name}_v*.zip'):
        m = pattern.search(f.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def stamp_version(version: int, dry: bool):
    """Record the version in both manifests so a stray zip can be identified."""
    touched = []
    pkg = ROOT / 'package.json'
    if pkg.exists():
        data = json.loads(pkg.read_text())
        data['version'] = f'0.{version}.0'
        if not dry:
            pkg.write_text(json.dumps(data, indent=2) + '\n')
        touched.append(f'package.json version -> {data["version"]}')

    man = ROOT / 'manifest.json'
    if man.exists():
        data = json.loads(man.read_text())
        base = re.sub(r' v\d+$', '', data.get('name', 'Plugin'))
        data['name'] = f'{base} v{version}'
        if not dry:
            man.write_text(json.dumps(data, indent=2) + '\n')
        touched.append(f'manifest name -> {data["name"]}')
    return touched


def collect():
    files = []
    for f in INCLUDE_FILES:
        path = ROOT / f
        if path.exists():
            files.append(path)
        else:
            print(f'  ! missing {f}', file=sys.stderr)
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob('*')):
            if path.is_file() and not any(sk in path.name for sk in SKIP):
                files.append(path)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', default='klinos')
    ap.add_argument('--outdir', default='dist')
    ap.add_argument('--version', type=int, help='force a version instead of auto-incrementing')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    outdir = ROOT / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)

    version = args.version or next_version(outdir, args.name)
    target = outdir / f'{args.name}_v{version}.zip'
    if target.exists() and not args.version:
        print(f'{target.name} already exists, refusing to overwrite', file=sys.stderr)
        return 1

    for line in stamp_version(version, args.dry_run):
        print(f'  {line}')

    files = collect()
    if args.dry_run:
        print(f'\nwould write {target.relative_to(ROOT)} with {len(files)} files')
        return 0

    root_name = f'{args.name}_v{version}'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in files:
            z.write(path, Path(root_name) / path.relative_to(ROOT))

    size = target.stat().st_size
    print(f'\n{target.relative_to(ROOT)}  {len(files)} files  {size / 1024:.0f} KB')
    if size > 15 * 1024 * 1024:
        print('  ! over the 15 MB Figma Community publishing limit', file=sys.stderr)
    else:
        print(f'  {100 * size / (15 * 1024 * 1024):.1f}% of the 15 MB publishing limit')
    return 0


if __name__ == '__main__':
    sys.exit(main())
