"""YAML loading for the flat config files this pipeline uses.

PyYAML is used when it is importable. It is not installed in the interpreter
this project currently runs on, so there is a fallback that reads the strict
subset the configs actually need: a single top-level mapping of scalars.

The fallback deliberately refuses anything outside that subset (nesting, lists,
anchors, multi-line strings) rather than guessing, so a config that grows past
what it understands fails loudly instead of being silently half-read.
"""

from __future__ import annotations

import ast
from pathlib import Path

try:  # pragma: no cover - depends on the environment, both paths are tested
    import yaml as _pyyaml
except ImportError:  # pragma: no cover
    _pyyaml = None

HAVE_PYYAML = _pyyaml is not None

_BOOLS = {
    "true": True, "yes": True, "on": True,
    "false": False, "no": False, "off": False,
}
_NULLS = {"", "null", "~", "none"}


class YamlError(ValueError):
    """Raised when a config file cannot be read as a flat scalar mapping."""


def load_yaml(path) -> dict:
    """Load `path` and return a dict. Raises YamlError on anything else."""
    path = Path(path)
    if not path.is_file():
        raise YamlError(f"config file not found: {path}")
    text = path.read_text(encoding="utf-8")

    if HAVE_PYYAML:
        data = _pyyaml.safe_load(text)
    else:
        data = loads_flat(text, source=str(path))

    if data is None or data == {}:
        raise YamlError(f"{path} is empty")
    if not isinstance(data, dict):
        raise YamlError(f"{path} must contain a mapping at the top level, got {type(data).__name__}")
    return data


def loads_flat(text: str, source: str = "<string>") -> dict:
    """Parse a flat `key: value` YAML document. Fallback for no-PyYAML envs."""
    out: dict = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if line[0] in " \t":
            raise YamlError(
                f"{source}:{lineno}: indented line. The fallback YAML reader "
                f"only supports a flat top-level mapping: {raw.rstrip()!r}"
            )
        if line.lstrip().startswith("- "):
            raise YamlError(f"{source}:{lineno}: lists are not supported by the fallback YAML reader")
        if ":" not in line:
            raise YamlError(f"{source}:{lineno}: expected 'key: value', got {raw.rstrip()!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        if not key:
            raise YamlError(f"{source}:{lineno}: empty key in {raw.rstrip()!r}")
        if key in out:
            raise YamlError(f"{source}:{lineno}: duplicate key {key!r}")
        out[key] = _scalar(value.strip(), source, lineno)
    return out


def _strip_comment(line: str) -> str:
    """Drop a trailing `#` comment that is not inside a quoted string."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _scalar(token: str, source: str, lineno: int):
    if token[:1] in ("'", '"'):
        if len(token) < 2 or token[-1] != token[0]:
            raise YamlError(f"{source}:{lineno}: unterminated quoted string {token!r}")
        return ast.literal_eval(token)
    low = token.lower()
    if low in _NULLS:
        return None
    if low in _BOOLS:
        return _BOOLS[low]
    for cast in (int, float):
        try:
            return cast(token)
        except ValueError:
            pass
    return token
