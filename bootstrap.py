"""Set up the provided kit on Windows, Linux or macOS. Does not start a backend."""
from pathlib import Path
import shutil
import subprocess
import sys
import venv


def main() -> int:
    if sys.version_info < (3, 11):
        print('Python 3.11 or newer is required.', file=sys.stderr)
        return 1
    root = Path(__file__).resolve().parent
    env = root / '.venv'
    python = env / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    try:
        if not env.exists():
            venv.EnvBuilder(with_pip=True).create(env)
        if not python.is_file():
            raise RuntimeError('Existing .venv is incomplete or from another OS. Remove it and rerun setup.')
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', 'requirements-dev.txt'], cwd=root, check=True)
        if not (root / '.env').exists():
            shutil.copyfile(root / '.env.example', root / '.env')
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'Setup failed: {exc}', file=sys.stderr)
        return 1
    command = r'.\.venv\Scripts\python.exe' if sys.platform == 'win32' else '.venv/bin/python'
    print(f'\nSetup complete. From {root}:\n  {command} -m processor')
    print('The processor reads .env automatically. Keep it running in this terminal.')
    print('This kit does not include the candidate backend.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
