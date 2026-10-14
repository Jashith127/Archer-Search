"""Read and update Archer's .env without disturbing its comments or unrelated lines."""
import re
from pathlib import Path

_LINE = re.compile(r"^\s*(#\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
MANAGED = "# Managed by Archer Settings"


def quote(value: str) -> str:
    """Quote only when a value would otherwise be misread (spaces, #, quotes)."""
    if value and not re.search(r"[\s#'\"\\]", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def update(text: str, changes: dict[str, str | None]) -> str:
    """Return text with each NAME set to a value, or commented out when the value is None.

    An existing active line is replaced in place; otherwise the first commented example
    (`# NAME=`) is reused, so the setting lands next to its documentation. New names go in
    a block at the end of the file.
    """
    lines = text.splitlines()
    remaining = dict(changes)
    for name in list(remaining):
        active = commented = None
        for index, line in enumerate(lines):
            match = _LINE.match(line)
            if match and match[2] == name:
                if match[1] is None and active is None:
                    active = index
                elif match[1] is not None and commented is None:
                    commented = index
        value = remaining[name]
        if active is not None:
            lines[active] = f"# {name}=" if value is None else f"{name}={quote(value)}"
            # Later duplicates would override the edited line when the file is loaded.
            for index in range(len(lines) - 1, active, -1):
                match = _LINE.match(lines[index])
                if match and match[1] is None and match[2] == name:
                    lines[index] = f"# {name}="
            remaining.pop(name)
        elif value is None:
            remaining.pop(name)
        elif commented is not None:
            lines[commented] = f"{name}={quote(value)}"
            remaining.pop(name)
    if remaining:
        if MANAGED not in lines:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(MANAGED)
        lines.extend(f"{name}={quote(value)}" for name, value in remaining.items())
    return "\n".join(lines) + "\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
