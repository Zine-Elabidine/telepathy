"""Adapter #2: Codex (CLI 0.171).

Codex has no memory of its own that we can point somewhere, so its SessionStart hook hands
it everything Claude Code gets natively: the combined index plus the rules for saving.
- Once per machine, `~/.codex/hooks.json` runs `tp hook start|end --agent codex`. Codex
  skips new or changed hooks until the user trusts them in `/hooks`.
- `~/.codex/config.toml` gets the Telepathy home as a writable root, because in its
  default sandbox Codex can only write inside the project.
"""

import os
import shutil
import sys
import tomllib
from pathlib import Path

from . import config
from .claude import HOOK_MARK, _dump, _load, _ours

EVENTS = ("SessionStart", "SessionEnd")


def config_dir() -> Path:
    # Same variable Codex itself honours.
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def hooks_file() -> Path:
    return config_dir() / "hooks.json"


def hook_command(event: str) -> str:
    return f'"{sys.executable}" -m {HOOK_MARK} {event} --agent codex'


def install_hooks() -> Path:
    path = hooks_file()
    data = _load(path)
    if path.exists():
        backup = path.with_name("hooks.json.before-telepathy")
        if not backup.exists():
            shutil.copy2(path, backup)
    hooks = data.setdefault("hooks", {})
    wanted = {
        # additionalContextLimit 0: the whole index, not a head-and-tail preview
        "SessionStart": {"type": "command", "command": hook_command("start"), "timeout": 30,
                         "additionalContextLimit": 0},
        # Codex allows at most 3 s here, so the hook only starts the real work detached
        "SessionEnd": {"type": "command", "command": hook_command("end"), "timeout": 3},
    }
    for event, hook in wanted.items():
        groups = [g for g in hooks.get(event, []) if not _ours(g)]
        groups.append({"hooks": [hook]})
        hooks[event] = groups
    _dump(path, data)
    return path


def remove_hooks() -> bool:
    path = hooks_file()
    data = _load(path)
    hooks = data.get("hooks", {})
    changed = False
    for event in EVENTS:
        groups = hooks.get(event, [])
        kept = [g for g in groups if not _ours(g)]
        if kept != groups:
            changed = True
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if changed:
        _dump(path, data)
    return changed


def hooks_installed() -> bool:
    try:
        hooks = _load(hooks_file()).get("hooks", {})
    except Exception:
        return False
    return all(any(_ours(g) for g in hooks.get(e, [])) for e in EVENTS)


# --- sandbox ---------------------------------------------------------------------------------

def _config_toml() -> Path:
    return config_dir() / "config.toml"


def writable_root() -> str:
    return config.home().resolve().as_posix()


def memory_writable() -> bool:
    try:
        data = tomllib.loads(_config_toml().read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    roots = data.get("sandbox_workspace_write", {}).get("writable_roots", [])
    want = Path(writable_root())
    return any(Path(os.path.expanduser(r)).resolve() == want for r in roots)


def ensure_writable() -> str:
    """Let Codex write memories outside the project. Only ever appends to config.toml (the
    standard library cannot write TOML); if the table already exists, says what to add.
    Returns a message for the user, empty when nothing needed doing."""
    if memory_writable():
        return ""
    path = _config_toml()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return f"{path} is not valid TOML ({e}); add {writable_root()} to writable_roots by hand"
    line = f'writable_roots = ["{writable_root()}"]'
    if "sandbox_workspace_write" in data:
        return (f"Add {writable_root()} to writable_roots under [sandbox_workspace_write] "
                f"in {path}, so Codex can save memories (left untouched: the table exists).")
    if path.exists():
        backup = path.with_name("config.toml.before-telepathy")
        if not backup.exists():
            shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + sep + "# Added by Telepathy: lets Codex save memories\n"
                    "[sandbox_workspace_write]\n" + line + "\n", encoding="utf-8", newline="\n")
    return f"Codex may now write to {writable_root()} (added to {path})."


# --- what the model is told ------------------------------------------------------------------

def context(folder: Path, write: str, personal: str | None, collapsed: list[str],
            index: str) -> str:
    """The memory Codex starts with: how it works, how to save, then the index itself."""
    p = folder.as_posix()
    personal_rule = (f"`user` and `feedback` memories go in `{p}/{personal}/`; `project` and "
                     f"`reference` ones in `{p}/{write}/`." if personal else
                     f"All new memories go in `{p}/{write}/`.")
    collapsed_rule = (" Some bundles are collapsed to one line to save space ("
                      + ", ".join(collapsed) + "): read `" + p + "/<bundle>/MEMORY.md` "
                      "to see their memories." if collapsed else "")
    return f"""# Persistent memory (Telepathy)

You have a persistent, file-based memory, shared with this user's other agents (Claude
Code) and machines. It lives in `{p}`. The index below is `{p}/MEMORY.md`; links in it are
relative to that folder. Read a memory file when it looks relevant to the task.{collapsed_rule}
Memories reflect what was true when written: if one names a file, function or flag, check it
still exists before relying on it.

## Saving a memory

Save what will matter in future sessions: who the user is and how they like to work
(including corrections they give you), ongoing project goals and constraints that the code
does not show, and pointers to outside resources. Do not save what the code, git history or
project docs already record, or what only matters to this conversation. Never save secrets.

One fact per file, named `<short-kebab-slug>.md`, starting with:

```
---
name: <short-kebab-slug>
description: <one-line summary, used to decide relevance later>
metadata:
  type: user | feedback | project | reference
---
```

then the fact. For `feedback` and `project`, add a **Why:** line and a **How to apply:**
line. Convert relative dates to absolute ones. {personal_rule}
Then add one line under that folder's section of `{p}/MEMORY.md`:
`- [Title](<folder>/<file>.md) — short hook`. Update an existing memory instead of writing a
duplicate, and delete memories that turn out to be wrong. Telepathy syncs everything when the
session ends.

{index}"""
