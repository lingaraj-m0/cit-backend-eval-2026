"""Load the documented KEY=VALUE subset without executing shell commands."""
import os
from pathlib import Path
import re


def load_env(path: str | Path = '.env') -> None:
    """Read optional local config; explicitly exported environment values win.

    Support UTF-8 (with or without a BOM), LF/CRLF, full-line comments and
    matching outer quotes. No interpolation, escape expansion or inline comments.
    Parse completely before changing the environment.
    """
    source = Path(path)
    if not source.exists():
        return
    values = {}
    for number, raw in enumerate(source.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise ValueError(f'{source}:{number}: expected KEY=VALUE')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'{source}:{number}: unmatched quote')
            value = value[1:-1]
        if '\x00' in value:
            raise ValueError(f'{source}:{number}: NUL is not allowed')
        values[key] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)
