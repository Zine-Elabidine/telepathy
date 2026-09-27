"""The store: your private git repo of bundles and project bindings, and moving it
between machines."""

import json
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config, memfile
from .gitx import GitError, git, ok

PROJECTS = "projects.toml"


# --- bundles -------------------------------------------------------------------------

def bundle_path(name: str) -> Path:
    return config.bundles_dir() / name


def list_bundles() -> list[str]:
    d = config.bundles_dir()
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []


def new_bundle(name: str, description: str = "") -> Path:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"invalid bundle name: {name!r}")
    path = bundle_path(name)
    path.mkdir(parents=True, exist_ok=True)
    manifest = path / "bundle.toml"
    if not manifest.exists() or (description and description != bundle_description(name)):
        manifest.write_text(f"name = {json.dumps(name)}\ndescription = {json.dumps(description)}\n",
                            encoding="utf-8")
    if not (path / "MEMORY.md").exists():
        (path / "MEMORY.md").write_text("", encoding="utf-8")
    return path


def bundle_description(name: str) -> str:
    try:
        return tomllib.loads((bundle_path(name) / "bundle.toml").read_text(encoding="utf-8")) \
            .get("description", "")
    except (OSError, tomllib.TOMLDecodeError):
        return ""


def memories(bundle: str) -> list[memfile.Memory]:
    path = bundle_path(bundle)
    return [memfile.read(p) for p in sorted(path.iterdir()) if memfile.is_memory_file(p)] \
        if path.exists() else []


def read_index(bundle: str) -> dict[str, str]:
    p = bundle_path(bundle) / "MEMORY.md"
    return memfile.parse_index(p.read_text(encoding="utf-8")) if p.exists() else {}


def write_index(bundle: str, updates: dict[str, str] | None = None) -> dict[str, str]:
    """Regenerate a bundle's MEMORY.md. The files decide what is listed; existing lines
    (and `updates`, which win) keep the titles Claude wrote; new files get a line built
    from their frontmatter."""
    lines = read_index(bundle)
    lines.update(updates or {})
    mems = {m.path.name: m for m in memories(bundle)}
    out = {f: line for f, line in lines.items() if f in mems}
    for f, m in mems.items():
        out.setdefault(f, memfile.index_line(m))
    text = "".join(line + "\n" for line in out.values())
    p = bundle_path(bundle) / "MEMORY.md"
    if not p.exists() or p.read_text(encoding="utf-8") != text:
        p.write_text(text, encoding="utf-8")
    return out


# --- bindings --------------------------------------------------------------------------

@dataclass
class Binding:
    bundles: list[str]
    write: str
    personal: str | None = None

    def target_for(self, mem_type: str) -> str:
        if mem_type in config.PERSONAL_TYPES and self.personal:
            return self.personal
        return self.write


def _binding(v: dict) -> Binding:
    bundles = list(v.get("bundles", []))
    return Binding(bundles, v.get("write") or (bundles[-1] if bundles else ""), v.get("personal"))


def parse_projects(text: str) -> dict[str, dict[str, Binding]]:
    """projects.toml -> {machine: {project key: Binding}}. Every machine has its own choice
    for every project. Entries from the first format (one choice shared by all machines)
    are read as this machine's."""
    raw = tomllib.loads(text)
    out: dict[str, dict[str, Binding]] = {}
    for machine, projects in raw.get("machines", {}).items():
        out[machine] = {key: _binding(v) for key, v in projects.items()}
    for key, v in raw.items():
        if key != "machines" and isinstance(v, dict) and "bundles" in v:
            out.setdefault(config.machine(), {}).setdefault(key, _binding(v))
    return out


def load_all() -> dict[str, dict[str, Binding]]:
    p = config.store_dir() / PROJECTS
    return parse_projects(p.read_text(encoding="utf-8")) if p.exists() else {}


def load_bindings() -> dict[str, Binding]:
    """This machine's choices."""
    return load_all().get(config.machine(), {})


def other_machines(key: str) -> dict[str, Binding]:
    """What the other machines load for this project."""
    return {m: projects[key] for m, projects in load_all().items()
            if m != config.machine() and key in projects}


def dump_projects(everything: dict[str, dict[str, Binding]]) -> str:
    parts = []
    for machine in sorted(everything):
        for key in sorted(everything[machine]):
            b = everything[machine][key]
            parts.append(f"[machines.{json.dumps(machine)}.{json.dumps(key)}]\n"
                         f"bundles = {json.dumps(b.bundles)}\nwrite = {json.dumps(b.write)}\n"
                         + (f"personal = {json.dumps(b.personal)}\n" if b.personal else ""))
    return "\n".join(parts)


def save_binding(key: str, binding: Binding) -> None:
    everything = load_all()
    everything.setdefault(config.machine(), {})[key] = binding
    (config.store_dir() / PROJECTS).write_text(dump_projects(everything), encoding="utf-8")


def make_binding(bundles: list[str], write: str | None = None) -> Binding:
    personal = "personal" if "personal" in bundles else None
    if write is None:
        rest = [b for b in bundles if b != personal]
        write = rest[-1] if rest else bundles[-1]
    if write not in bundles:
        raise ValueError(f"write bundle {write!r} is not one of {bundles}")
    return Binding(bundles, write, personal)


# --- the store repo ----------------------------------------------------------------------

def exists() -> bool:
    return (config.store_dir() / ".git").exists()


def init(url: str | None = None) -> str:
    """Clone the store from `url`, or create a fresh local one. Returns what happened."""
    store = config.store_dir()
    if exists():
        return "exists"
    store.parent.mkdir(parents=True, exist_ok=True)
    if url:
        git(store.parent, "clone", url, store.name)
        how = "cloned"
    else:
        store.mkdir(parents=True, exist_ok=True)
        git(store, "init", "-q", "-b", "main")
        how = "created"
    config.bundles_dir().mkdir(exist_ok=True)
    if "personal" not in list_bundles():
        new_bundle("personal", "Who I am and how I like to work, in every project")
    gitignore = store / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*.tmp\n.DS_Store\n", encoding="utf-8")
    commit("tp init")
    return how


def dirty() -> bool:
    return bool(git(config.store_dir(), "status", "--porcelain"))


def head() -> str:
    return git(config.store_dir(), "rev-parse", "HEAD", check=False)


def commit(message: str) -> bool:
    store = config.store_dir()
    if not dirty():
        return False
    git(store, "add", "-A")
    git(store, *_identity(), "commit", "-q", "-m", message)
    return True


def _identity() -> list[str]:
    """Commit as the user if git knows who they are, else as `telepathy@<machine>`."""
    if ok(config.store_dir(), "config", "user.email"):
        return []
    return ["-c", "user.name=telepathy", "-c", f"user.email=telepathy@{_safe_host()}"]


def _safe_host() -> str:
    return "".join(c for c in config.machine() if c.isalnum() or c in "-.") or "localhost"


def remote_branch() -> tuple[str, str] | None:
    store = config.store_dir()
    if "origin" not in git(store, "remote", check=False).split():
        return None
    return "origin", git(store, "rev-parse", "--abbrev-ref", "HEAD")


@dataclass
class SyncResult:
    pulled: bool = False
    pushed: bool = False
    offline: bool = False
    conflicts: list[str] = field(default_factory=list)


def sync(pull: bool = True, push: bool = True, timeout: float = 20) -> SyncResult:
    """Commit local changes, merge the remote in (resolving memory conflicts ourselves),
    push. Offline is not an error."""
    store = config.store_dir()
    res = SyncResult()
    commit(f"tp: {config.machine()} {datetime.now(timezone.utc):%Y-%m-%d %H:%M}")
    rb = remote_branch()
    if rb is None:
        return res
    remote, branch = rb
    if pull:
        if not ok(store, "fetch", "-q", remote, timeout=timeout):
            res.offline = True
            return res
        upstream = f"{remote}/{branch}"
        if ok(store, "rev-parse", "--verify", "-q", upstream):
            before = head()
            if not ok(store, *_identity(), "merge", "-q", "--no-edit", upstream):
                if not (store / ".git" / "MERGE_HEAD").exists():
                    raise GitError(f"could not merge {upstream} into the store")
                res.conflicts = _resolve_conflicts()
                commit_merge()
            res.pulled = head() != before
    if push:
        ahead = git(store, "rev-list", "--count", f"{remote}/{branch}..HEAD", check=False) \
            if ok(store, "rev-parse", "--verify", "-q", f"{remote}/{branch}") else "1"
        if ahead not in ("", "0"):
            if ok(store, "push", "-q", "-u", remote, branch, timeout=timeout):
                res.pushed = True
            else:
                res.offline = True
    return res


def commit_merge() -> None:
    git(config.store_dir(), *_identity(), "-c", "core.editor=true", "commit", "-q", "--no-edit")


def _show(stage: int, path: str) -> str | None:
    try:
        return git(config.store_dir(), "show", f":{stage}:{path}") + "\n"
    except GitError:
        return None


def _resolve_conflicts() -> list[str]:
    """Merge conflicts, settled without asking:
    - MEMORY.md: union of both sides' lines (indexes are regenerated anyway)
    - projects.toml: union of choices, ours wins on the same machine and project
    - a memory file: the newer `modified` wins; the other side is kept next to it as
      `<name>.conflict-N.md` for review
    - edited on one side, deleted on the other: keep the edit."""
    store = config.store_dir()
    files = git(store, "diff", "--name-only", "--diff-filter=U").splitlines()
    for rel in files:
        ours, theirs = _show(2, rel), _show(3, rel)
        dest = store / rel
        if ours is None or theirs is None:
            dest.write_text(ours if ours is not None else theirs or "", encoding="utf-8")
        elif rel.endswith("MEMORY.md"):
            lines = memfile.parse_index(theirs)
            lines.update(memfile.parse_index(ours))
            dest.write_text("".join(v + "\n" for v in lines.values()), encoding="utf-8")
        elif rel == PROJECTS:
            merged: dict[str, dict[str, Binding]] = {}
            for text in (theirs, ours):          # ours last: it wins on the same entry
                try:
                    for machine, projects in parse_projects(text).items():
                        merged.setdefault(machine, {}).update(projects)
                except tomllib.TOMLDecodeError:
                    pass
            dest.write_text(dump_projects(merged), encoding="utf-8")
        else:
            m_ours = memfile.frontmatter(ours).get("modified", "")
            m_theirs = memfile.frontmatter(theirs).get("modified", "")
            win, lose = (theirs, ours) if m_theirs > m_ours else (ours, theirs)
            dest.write_text(win, encoding="utf-8")
            n = 1
            while (alt := dest.with_name(f"{dest.stem}.conflict-{n}{dest.suffix}")).exists():
                n += 1
            alt.write_text(lose, encoding="utf-8")
        git(store, "add", "-A", "--", rel)
    git(store, "add", "-A")
    return files
