"""Validate a submission ZIP and optionally extract to a fresh directory."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--extract', type=Path)
    args = parser.parse_args()
    prohibited = {'.git', 'node_modules', '__pycache__', '.venv', '.aws', '.ssh', 'runtime', 'data'}
    with zipfile.ZipFile(args.archive) as archive:
        assert archive.testzip() is None, 'ZIP CRC failure'
        names = archive.namelist()
        assert len(names) == len(set(names)), 'Duplicate ZIP entries'
        prefix = 'Ember-midterm/'
        manifest = json.loads(archive.read(prefix + 'manifest.json'))
        for name in names:
            parts = PurePosixPath(name).parts
            assert name.startswith(prefix) and '..' not in parts and not name.startswith('/'), name
            assert not any(x in prohibited for x in parts), name
            leaf = parts[-1]
            assert not (leaf.startswith('.env') and leaf != '.env.example'), name
            assert leaf not in {'state.json', 'chat_memory.json', 'experience.json', 'working_memory.json'}, name
            assert not leaf.endswith(('.dump', '.ember', '.backup', '.pfx', '.pem', '.pyc')), name
            if leaf.endswith(('.py', '.js', '.jsx', '.md', '.yaml', '.txt', '.json')):
                body = archive.read(name)
                assert not re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', body), name
        actual = {name.removeprefix(prefix) for name in names if name != prefix + 'manifest.json'}
        assert actual == set(manifest['files']), 'Manifest entry mismatch'
        for name, digest in manifest['files'].items():
            assert hashlib.sha256(archive.read(prefix + name)).hexdigest() == digest, name
        if args.extract:
            destination = args.extract.resolve()
            assert not destination.exists(), 'Extraction requires a NEW destination'
            destination.mkdir(parents=True)
            for name in names:
                target = (destination / Path(*PurePosixPath(name).parts)).resolve()
                assert destination in target.parents, name
            archive.extractall(destination)
        print(json.dumps({'passed': True, 'entries': len(names), 'sha256_files': len(actual),
            'bytes': args.archive.stat().st_size, 'extracted': str(args.extract) if args.extract else None}))


if __name__ == '__main__':
    main()
