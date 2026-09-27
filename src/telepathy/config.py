"""Where things live. Every path can be overridden by an environment variable, which is
how the tests run against throwaway folders instead of the real home directory."""

import os
import socket
from pathlib import Path

# Claude Code loads at most this much of MEMORY.md; we stay a little under it.
INDEX_MAX_LINES = 190
INDEX_MAX_BYTES = 24_000

# Memory types (Claude Code's frontmatter `metadata.type`) that describe the user rather
# than the project. New memories of these types go to the personal bundle.
PERSONAL_TYPES = {"user", "feedback"}


def home() -> Path:
    return Path(os.environ.get("TELEPATHY_HOME") or Path.home() / ".telepathy")


def store_dir() -> Path:
    return home() / "store"


def bundles_dir() -> Path:
    return store_dir() / "bundles"


def sessions_dir() -> Path:
    return home() / "sessions"


def state_dir() -> Path:
    return home() / "state"


def claude_config_dir() -> Path:
    # Same variable Claude Code itself honours.
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def machine() -> str:
    """This machine's name in the store. Saved at `tp init`, so it stays the same even if
    the hostname changes."""
    if name := os.environ.get("TELEPATHY_MACHINE"):
        return name
    try:
        if name := (home() / "machine").read_text(encoding="utf-8").strip():
            return name
    except OSError:
        pass
    return socket.gethostname()


def set_machine(name: str) -> None:
    home().mkdir(parents=True, exist_ok=True)
    (home() / "machine").write_text(name.strip() + "\n", encoding="utf-8", newline="\n")


def tilde(path: Path) -> str:
    """`~/...` when the path is under the home directory (portable across machines),
    absolute otherwise."""
    try:
        return "~/" + path.resolve().relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
