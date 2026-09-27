"""The session folder: what the agent is pointed at. One linked subfolder per bundle and
a generated MEMORY.md with one section per bundle. Building it, and harvesting what the
agent changed back into the bundles."""

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import config, memfile, store
from .gitx import git, key_to_dirname
from .store import Binding

HEADER = "# Memory (managed by Telepathy)"


def session_dir(key: str) -> Path:
    return config.sessions_dir() / key_to_dirname(key)


def _state_path(key: str) -> Path:
    return config.state_dir() / (key_to_dirname(key) + ".json")


def load_state(key: str) -> dict:
    try:
        return json.loads(_state_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(key: str, state: dict) -> None:
    p = _state_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1), encoding="utf-8")


# --- links ------------------------------------------------------------------------------

def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def make_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi  # directory junctions: no admin rights or Developer Mode needed
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def remove_link(link: Path) -> None:
    """Remove the link itself, never what it points to."""
    if link.is_symlink():
        link.unlink()
    elif is_link(link):
        os.rmdir(link)


def _sync_links(sdir: Path, bundles: list[str]) -> None:
    for entry in sdir.iterdir():
        if is_link(entry) and entry.name not in bundles:
            remove_link(entry)
    for b in bundles:
        link, target = sdir / b, store.bundle_path(b)
        if is_link(link):
            if Path(os.path.realpath(link)) == Path(os.path.realpath(target)):
                continue
            remove_link(link)
        elif link.exists():
            raise RuntimeError(f"{link} exists and is not a link; move it away and retry")
        make_link(link, target)


# --- the combined index --------------------------------------------------------------------

def _prefix(line: str, bundle: str) -> str:
    return memfile.LINK.sub(lambda m: f"]({bundle}/{m.group(1)})", line, count=1)


@dataclass
class Index:
    text: str
    lines: int
    bytes: int
    collapsed: list[str] = field(default_factory=list)


def render(binding: Binding, per_bundle: dict[str, dict[str, str]]) -> Index:
    """One section per bundle, write bundle first. If the whole thing would not fit in what
    Claude Code loads, the biggest bundles collapse to a pointer at their own index."""
    order = [binding.write] + [b for b in binding.bundles if b != binding.write]
    roles = []
    if binding.personal and binding.personal != binding.write:
        roles.append(f"`{binding.personal}/` is about the user and applies to every project")
    roles.append(f"`{binding.write}/` is this project")
    head = [HEADER,
            "Memories live in bundle folders: " + "; ".join(roles) + ". Save each new memory "
            "inside the folder it belongs to and add its line under that folder's section.", ""]
    collapsed: list[str] = []

    def build() -> str:
        out = list(head)
        for b in order:
            out.append(f"## {b}")
            lines = per_bundle.get(b, {})
            if b in collapsed:
                out.append(f"- [All {len(lines)} {b} memories]({b}/MEMORY.md) — not listed "
                           f"here to save space; read that index when you need them")
            else:
                out.extend(_prefix(line, b) for line in lines.values())
            out.append("")
        return "\n".join(out)

    text = build()
    while _too_big(text):
        candidates = sorted((b for b in order if b not in collapsed and per_bundle.get(b)),
                            key=lambda b: (b == binding.write, -len(per_bundle[b])))
        if not candidates:
            break
        collapsed.append(candidates[0])
        text = build()
    return Index(text, text.count("\n") + 1, len(text.encode("utf-8")), collapsed)


def _too_big(text: str) -> bool:
    return text.count("\n") + 1 > config.INDEX_MAX_LINES or \
        len(text.encode("utf-8")) > config.INDEX_MAX_BYTES


# --- build and harvest ----------------------------------------------------------------------

def build(key: str, binding: Binding) -> Index:
    sdir = session_dir(key)
    sdir.mkdir(parents=True, exist_ok=True)
    for b in binding.bundles:
        if not store.bundle_path(b).exists():
            store.new_bundle(b)
    _sync_links(sdir, binding.bundles)
    per_bundle = {b: store.write_index(b) for b in binding.bundles}
    index = render(binding, per_bundle)
    target = sdir / "MEMORY.md"
    if not target.exists() or target.read_text(encoding="utf-8") != index.text:
        target.write_text(index.text, encoding="utf-8")
    _save_state(key, {"key": key, "head": store.head(), "index": index.text,
                      "lines": per_bundle, "collapsed": index.collapsed})
    return index


@dataclass
class Harvest:
    moved: list[tuple[str, str]] = field(default_factory=list)   # (file, bundle)
    updated: list[str] = field(default_factory=list)             # bundles whose index changed


def harvest(key: str, binding: Binding) -> Harvest:
    """Bring what the agent did in the session folder back into the bundles:
    - memory files it wrote at the top of the folder move to the bundle their type says
    - index lines it added or edited in the combined MEMORY.md go to that bundle's index.
    Files it wrote inside a bundle folder are already where they belong (through the link)."""
    sdir = session_dir(key)
    res = Harvest()
    if not sdir.exists():
        return res
    built = load_state(key).get("lines", {})
    index_path = sdir / "MEMORY.md"
    text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""

    updates: dict[str, dict[str, str]] = {b: {} for b in binding.bundles}
    top_lines: dict[str, str] = {}
    for line in text.splitlines():
        target = memfile.link_target(line)
        if not target or not target.endswith(".md"):
            continue
        bundle, _, name = target.partition("/")
        if not name:
            top_lines[bundle] = line
        elif bundle in updates and "/" not in name and name != "MEMORY.md":
            plain = memfile.LINK.sub(f"]({name})", line, count=1)
            if built.get(bundle, {}).get(name) != plain:
                updates[bundle][name] = plain

    for p in sorted(sdir.iterdir()):
        if is_link(p) or not memfile.is_memory_file(p):
            continue
        target_bundle = binding.target_for(memfile.read(p).type)
        dest = store.bundle_path(target_bundle) / p.name
        n = 2
        while dest.exists():
            dest = dest.with_name(f"{p.stem}-{n}{p.suffix}")
            n += 1
        shutil.move(str(p), str(dest))
        res.moved.append((p.name, target_bundle))
        if p.name in top_lines:
            updates[target_bundle][dest.name] = memfile.LINK.sub(
                f"]({dest.name})", top_lines[p.name], count=1)

    for b in binding.bundles:
        before = store.read_index(b)
        if store.write_index(b, updates[b]) != before:
            res.updated.append(b)
    return res


def arrivals(old_head: str, bundles: list[str]) -> list[tuple[str, str, str]]:
    """Memories added or changed in the store since `old_head`: (status, bundle, file)."""
    new_head = store.head()
    if not old_head or old_head == new_head:
        return []
    out = []
    diff = git(config.store_dir(), "diff", "--name-status", old_head, new_head, "--",
               *[f"bundles/{b}/" for b in bundles], check=False)
    for row in diff.splitlines():
        status, _, rel = row.partition("\t")
        parts = rel.split("/")
        if len(parts) == 3 and memfile.is_memory_name(parts[2]):
            out.append((status[:1], parts[1], parts[2]))
    return out


def index_line_for(bundle: str, name: str) -> str:
    line = store.read_index(bundle).get(name) or f"- [{name}]({name})"
    return _prefix(line, bundle)


def conflict_copies(bundles: list[str]) -> list[Path]:
    return [p for b in bundles if store.bundle_path(b).exists()
            for p in sorted(store.bundle_path(b).iterdir()) if memfile.is_conflict_copy(p)]

