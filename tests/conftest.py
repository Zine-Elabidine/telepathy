import subprocess
from pathlib import Path

import pytest


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own Telepathy home, Claude config dir and git identity, so the
    real ~/.telepathy and ~/.claude are never touched."""
    monkeypatch.setenv("TELEPATHY_HOME", str(tmp_path / "machine-a" / ".telepathy"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "machine-a" / ".claude"))
    monkeypatch.setenv("TELEPATHY_MACHINE", "machine-a")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # git also reads $XDG_CONFIG_HOME/git/ignore (default ~/.config/git/ignore), which may
    # already ignore .claude/settings.local.json on a real machine
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    return tmp_path


def use_machine(tmp_path: Path, monkeypatch, name: str) -> None:
    monkeypatch.setenv("TELEPATHY_HOME", str(tmp_path / name / ".telepathy"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / name / ".claude"))
    monkeypatch.setenv("TELEPATHY_MACHINE", name)


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "remote.git"
    run("git", "init", "-q", "--bare", "-b", "main", str(path))
    return path


def make_project(path: Path, remote_url: str = "git@github.com:me/my-project.git") -> Path:
    path.mkdir(parents=True)
    run("git", "init", "-q", "-b", "main", cwd=path)
    run("git", "remote", "add", "origin", remote_url, cwd=path)
    return path


def memory(path: Path, name: str, type_: str, body: str, modified: str = "") -> Path:
    fm = f"---\nname: {name}\ndescription: \"{body[:40]}\"\nmetadata:\n  type: {type_}\n"
    if modified:
        fm += f"  modified: {modified}\n"
    path.write_text(fm + f"---\n\n{body}\n", encoding="utf-8")
    return path
