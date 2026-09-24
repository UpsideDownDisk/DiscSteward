"""Create a source release from an explicit allowlist, without local media/data."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    '.gitignore', '.gitattributes', 'LICENSE', 'README.md', 'CLEAN-PC-SETUP.md',
    'CHANGELOG.md', 'CONTRIBUTING.md', 'THIRD_PARTY.md', 'PUBLISHING.md',
    'pyproject.toml', 'MANIFEST.in', 'discsteward-ui.py',
    'Open DiscSteward.vbs', 'DiscSteward-Diagnostics.cmd',
    'assets/discsteward-icon.png',
)
SOURCE_GROUPS = (('src/bluray_map', '*.py'), ('tests', '*.py'),
                 ('scripts', '*.py'), ('.github', '*.yml'))


def source_files(root=ROOT):
    paths = [root/name for name in ROOT_FILES]
    for folder, pattern in SOURCE_GROUPS:
        paths.extend((root/folder).rglob(pattern))
    for path in paths:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'Missing or unsafe release file: {path}')
    return sorted(set(paths))


def main():
    text = (ROOT/'pyproject.toml').read_text(encoding='utf-8')
    # Keep the release helper runnable on Python 3.10 without a TOML dependency.
    version = re.search(r'^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$', text, re.MULTILINE)
    if not version: raise ValueError('Expected a release version in pyproject.toml.')
    files = source_files()
    destination = ROOT/'dist'
    destination.mkdir(exist_ok=True)
    target = destination/f'DiscSteward-{version[1]}-source.zip'
    checksum = target.with_suffix(target.suffix+'.sha256')
    if target.exists() or checksum.exists():
        raise FileExistsError(f'Release already exists: {target}; choose a new version or output location.')
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, 'DiscSteward/'+path.relative_to(ROOT).as_posix())
    with checksum.open('x', encoding='utf-8') as stream:
        stream.write(f'{hashlib.sha256(target.read_bytes()).hexdigest()}  {target.name}\n')
    print(f'Created {target} ({len(files)} source files)')
    print(f'Checksum: {checksum}')


if __name__ == '__main__':
    main()
