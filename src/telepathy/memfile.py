"""Memory files and index lines, in the format Claude Code writes them.

A memory file:

    ---
    name: short-slug
    description: "one line"
    metadata:
      type: user | feedback | project | reference
    ---
    body

An index (MEMORY.md) line:

    - [Title](file.md) — one-line hook
"""

import re
from dataclasses import dataclass
from pathlib import Path

LINK = re.compile(r"\]\(([^)\s]+)\)")


@dataclass
class Memory:
    path: Path
    name: str = ""
    description: str = ""
    type: str = ""
    modified: str = ""


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def frontmatter(text: str) -> dict[str, str]:
    """The small YAML subset Claude Code uses: top-level scalars, plus one nested
    `metadata:` map whose keys are flattened into the same dict."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^(\s*)([\w-]+):\s*(.*)$", line)
        if m and m.group(3):
            out.setdefault(m.group(2), _unquote(m.group(3)))
    return out


def read(path: Path) -> Memory:
    try:
        fm = frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        fm = {}
    return Memory(path, fm.get("name", ""), fm.get("description", ""),
                  fm.get("type", ""), fm.get("modified", ""))


def is_memory_name(name: str) -> bool:
    return name.endswith(".md") and name != "MEMORY.md" and not name.startswith(".") \
        and ".conflict-" not in name


def is_memory_file(path: Path) -> bool:
    return path.is_file() and is_memory_name(path.name)


def is_conflict_copy(path: Path) -> bool:
    return ".conflict-" in path.name


def link_target(line: str) -> str | None:
    m = LINK.search(line)
    return m.group(1) if m and line.lstrip().startswith("-") else None


def index_line(mem: Memory) -> str:
    title = mem.name.replace("-", " ").replace("_", " ").strip().capitalize() or mem.path.stem
    hook = mem.description or mem.type or "memory"
    return f"- [{title}]({mem.path.name}) — {hook}"


def parse_index(text: str) -> dict[str, str]:
    """file name -> its index line, in order, for lines that link to a local file."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        target = link_target(line)
        if target and "/" not in target and target.endswith(".md"):
            out.setdefault(target, line)
    return out
