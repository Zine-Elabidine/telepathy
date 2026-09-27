"""Adapter #1: Claude Code.

- Per project and machine, `.claude/settings.local.json` points `autoMemoryDirectory` at
  the session folder and allows reading/editing through its links (Claude Code's own
  memory-folder permission does not follow links; measured on 2.1.283).
- Once per machine, SessionStart/SessionEnd hooks in the user settings run `tp hook`.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config
from .gitx import git, ok

LOCAL_SETTINGS = ".claude/settings.local.json"
HOOK_MARK = "telepathy hook"


class SettingsError(RuntimeError):
    pass


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError as e:
        raise SettingsError(f"{path} is not valid JSON ({e}); fix it and retry") from e
    if not isinstance(data, dict):
        raise SettingsError(f"{path} does not contain a JSON object")
    return data


def _dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _rule_path(path: Path) -> str:
    # Permission rules: `~/x` is home-relative, `//x` is absolute.
    p = config.tilde(path)
    return p if p.startswith("~/") else "/" + p


def allow_rules() -> list[str]:
    home = _rule_path(config.home())
    return [f"Read({home}/**)",
            f"Edit({_rule_path(config.store_dir())}/**)",
            f"Edit({_rule_path(config.sessions_dir())}/**)"]


# --- per project -----------------------------------------------------------------------------

def ensure_project(root: Path, session_dir: Path) -> bool:
    """Point this project's Claude Code memory at the session folder. True if anything
    changed, which means it takes effect from the next session."""
    path = root / LOCAL_SETTINGS
    data = _load(path)
    changed = False
    want = config.tilde(session_dir)
    if data.get("autoMemoryDirectory") != want:
        data["autoMemoryDirectory"] = want
        changed = True
    allow = data.setdefault("permissions", {}).setdefault("allow", [])
    for rule in allow_rules():
        if rule not in allow:
            allow.append(rule)
            changed = True
    if changed:
        _dump(path, data)
    _exclude(root)
    return changed


def _exclude(root: Path) -> None:
    """Keep the local settings file out of the project's git without touching the project:
    .git/info/exclude is local to this clone."""
    if not (root / ".git").exists() or ok(root, "check-ignore", "-q", LOCAL_SETTINGS):
        return
    common = Path(git(root, "rev-parse", "--git-common-dir"))
    exclude = (common if common.is_absolute() else root / common) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if LOCAL_SETTINGS not in text.splitlines():
        exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n")
                           + LOCAL_SETTINGS + "\n", encoding="utf-8")


def project_points_here(root: Path, session_dir: Path) -> bool:
    try:
        return _load(root / LOCAL_SETTINGS).get("autoMemoryDirectory") == config.tilde(session_dir)
    except SettingsError:
        return False


def reads_blocked(root: Path | None = None) -> bool:
    """`blockReadsOutsideWorkingDirectories` makes Claude Code ignore a memory folder chosen
    by a repository settings file."""
    files = [config.claude_config_dir() / "settings.json"]
    if root:
        files += [root / ".claude/settings.json", root / LOCAL_SETTINGS]
    for f in files:
        try:
            if _load(f).get("permissions", {}).get("blockReadsOutsideWorkingDirectories"):
                return True
        except SettingsError:
            pass
    return False


# --- per machine: hooks -----------------------------------------------------------------------

def user_settings() -> Path:
    return config.claude_config_dir() / "settings.json"


def hook_command(event: str) -> str:
    # The interpreter of this installation, so the hook works whatever PATH the agent has.
    # The trailing word is the marker used to find our hooks again.
    return f'"{sys.executable}" -m telepathy hook {event}'


def _ours(group: dict) -> bool:
    return any(HOOK_MARK in h.get("command", "") for h in group.get("hooks", []))


def install_hooks() -> Path:
    path = user_settings()
    data = _load(path)
    if path.exists():
        backup = path.with_name("settings.json.before-telepathy")
        if not backup.exists():
            shutil.copy2(path, backup)
    hooks = data.setdefault("hooks", {})
    wanted = {
        "SessionStart": {"type": "command", "command": hook_command("start"), "timeout": 30},
        "SessionEnd": {"type": "command", "command": hook_command("end"), "timeout": 30},
    }
    for event, hook in wanted.items():
        groups = [g for g in hooks.get(event, []) if not _ours(g)]
        groups.append({"hooks": [hook]})
        hooks[event] = groups
    _dump(path, data)
    return path


def remove_hooks() -> bool:
    path = user_settings()
    data = _load(path)
    hooks = data.get("hooks", {})
    changed = False
    for event in ("SessionStart", "SessionEnd"):
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
        hooks = _load(user_settings()).get("hooks", {})
    except SettingsError:
        return False
    return all(any(_ours(g) for g in hooks.get(e, [])) for e in ("SessionStart", "SessionEnd"))


def spawn_background(*args: str) -> None:
    """Run `python -m telepathy <args>` detached, so a slow push never delays exit."""
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, "-m", "telepathy", *args], **kwargs)
