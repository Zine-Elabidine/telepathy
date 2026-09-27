"""Thin wrapper over the git command line, plus project identity."""

import re
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, check: bool = True, timeout: float | None = 60) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def ok(repo: Path, *args: str, timeout: float | None = 60) -> bool:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, timeout=timeout
        ).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def toplevel(path: Path) -> Path | None:
    try:
        return Path(git(path, "rev-parse", "--show-toplevel"))
    except (GitError, FileNotFoundError, NotADirectoryError):
        return None


def normalize_remote(url: str) -> str:
    """git@github.com:Me/Repo.git, https://github.com/Me/Repo and
    ssh://git@github.com:22/Me/Repo all become github.com/Me/Repo."""
    url = url.strip()
    if m := re.match(r"^[\w.-]+@([^:/]+):(?!\d+/)(.+)$", url):  # scp-like
        host, path = m.groups()
    elif m := re.match(r"^[a-z+]+://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$", url):
        host, path = m.groups()
    else:
        return url.rstrip("/").removesuffix(".git")
    path = path.strip("/").removesuffix(".git").rstrip("/")
    return f"{host.lower()}/{path}"


def project_key(path: Path) -> tuple[str, Path] | None:
    """The key a project is known by on every machine, and its root on this one.
    The git remote (origin first) when there is one, else the folder name."""
    root = toplevel(path)
    if root is None:
        # Not a git repo: the folder itself, known by its name on every machine.
        path = path.resolve()
        return (f"dir/{path.name}", path) if path.is_dir() and path != path.home() else None
    remotes = git(root, "remote", check=False).split()
    if remotes:
        name = "origin" if "origin" in remotes else remotes[0]
        url = git(root, "remote", "get-url", name, check=False)
        if url:
            return normalize_remote(url), root
    return f"local/{root.name}", root


def key_to_dirname(key: str) -> str:
    return re.sub(r"[^\w.-]+", "--", key)
