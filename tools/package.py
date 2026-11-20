"""Build a clean source archive without recordings, caches or private research inputs."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '.cache', 'data', 'dist', '__pycache__'}
EXCLUDED_FILES = {'research/challenge.html', 'research/challenge.txt'}


def main():
    output = ROOT / 'dist/harbor-source.zip'
    output.parent.mkdir(exist_ok=True)
    included = []
    for path in sorted(ROOT.rglob('*')):
        relative = path.relative_to(ROOT)
        if not path.is_file() or EXCLUDED.intersection(relative.parts):
            continue
        if path.suffix in {'.pyc', '.log'} or relative.as_posix() in EXCLUDED_FILES:
            continue
        included.append((path, relative))
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, relative in included:
            archive.write(path, Path('harbor') / relative)
    print(f'{output}: {len(included)} files, {output.stat().st_size} bytes')


if __name__ == '__main__':
    main()
