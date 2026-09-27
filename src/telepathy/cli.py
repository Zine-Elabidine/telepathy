"""The `tp` command."""

import argparse
import json
import os
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

from . import __version__, claude, config, memfile, session, store
from .gitx import GitError, project_key


def _project(path: Path | None = None) -> tuple[str, Path]:
    found = project_key(path or Path.cwd())
    if found is None:
        sys.exit("tp: run this from a project folder")
    return found


def _need_store() -> None:
    if not store.exists():
        sys.exit("tp: no store on this machine yet; run `tp init [repo-url]` first")


# --- commands ----------------------------------------------------------------------------

def cmd_init(a) -> None:
    how = store.init(a.url)
    where = config.store_dir()
    print({"exists": f"Store already set up at {where}",
           "cloned": f"Cloned your store into {where}",
           "created": f"Created a new store at {where}"}[how])
    if how == "created":
        print("  It only lives on this machine for now. To sync it, create a private git repo "
              f"and run:\n  git -C {where} remote add origin <url> && tp sync")
    if not a.no_hooks:
        path = claude.install_hooks()
        print(f"Claude Code hooks installed in {path}")
    print(f"Bundles: {', '.join(store.list_bundles()) or '(none)'}")
    print("Next: in a project folder, run `tp use personal <project-bundle>`.")


def cmd_new(a) -> None:
    _need_store()
    path = store.new_bundle(a.name, a.description or "")
    store.commit(f"tp new {a.name}")
    print(f"Bundle {a.name!r} at {path}")


def cmd_use(a) -> None:
    _need_store()
    key, root = _project()
    for b in a.bundles:
        if not store.bundle_path(b).exists():
            store.new_bundle(b)
            print(f"Created bundle {b!r}")
    try:
        binding = store.make_binding(a.bundles, a.write)
    except ValueError as e:
        sys.exit(f"tp: {e}")
    store.save_binding(key, binding)
    store.commit(f"tp use {' '.join(a.bundles)} ({key})")
    index = session.build(key, binding)
    changed = claude.ensure_project(root, session.session_dir(key))
    res = store.sync(timeout=20)
    print(f"{key} now uses: {', '.join(binding.bundles)}")
    print(f"  new project memories go to {binding.write!r}"
          + (f", memories about you to {binding.personal!r}" if binding.personal else ""))
    print(f"  index: {index.lines} lines, {index.bytes} bytes"
          + (f" (collapsed: {', '.join(index.collapsed)})" if index.collapsed else ""))
    if changed:
        print("Start a new Claude Code session to load it.")
    if res.offline:
        print("  (offline: saved locally, will sync later)")
    if claude.reads_blocked(root):
        print("Warning: blockReadsOutsideWorkingDirectories is on, so Claude Code will ignore "
              "this memory folder.")


def cmd_import(a) -> None:
    _need_store()
    src = Path(a.source).expanduser()
    if not src.is_dir():
        sys.exit(f"tp: {src} is not a folder")
    dest = store.new_bundle(a.bundle)
    src_lines = memfile.parse_index((src / "MEMORY.md").read_text(encoding="utf-8")) \
        if (src / "MEMORY.md").exists() else {}
    copied, skipped = [], []
    for p in sorted(src.iterdir()):
        if not memfile.is_memory_file(p):
            continue
        if (dest / p.name).exists():
            skipped.append(p.name)
            continue
        shutil.copy2(p, dest / p.name)
        copied.append(p.name)
    store.write_index(a.bundle, {f: src_lines[f] for f in copied if f in src_lines})
    store.commit(f"tp import {len(copied)} memories into {a.bundle}")
    print(f"Imported {len(copied)} memories into {a.bundle!r} (source left untouched)")
    if skipped:
        print(f"  skipped {len(skipped)} already in the bundle: {', '.join(skipped)}")


def cmd_status(a) -> None:
    print(f"telepathy {__version__}  store: {config.store_dir()}"
          + ("" if store.exists() else "  (not set up: run `tp init`)"))
    if not store.exists():
        return
    print(f"hooks: {'installed' if claude.hooks_installed() else 'NOT installed (tp init)'}")
    print(f"bundles: {', '.join(store.list_bundles()) or '(none)'}")
    rb = store.remote_branch()
    print(f"sync: {'origin/' + rb[1] if rb else 'no remote (local only)'}"
          + ("; uncommitted changes" if store.dirty() else ""))
    found = project_key(Path.cwd())
    if found is None:
        return
    key, root = found
    binding = store.load_bindings().get(key)
    print(f"\nproject: {key}")
    if binding is None:
        print("  no bundles chosen (Claude Code uses its own memory here); `tp use ...`")
        return
    sdir = session.session_dir(key)
    index = session.render(binding, {b: store.read_index(b) for b in binding.bundles})
    print(f"  bundles: {', '.join(binding.bundles)}  (writes: {binding.write})")
    print(f"  memory folder: {sdir}")
    print(f"  Claude Code setting: "
          f"{'ok' if claude.project_points_here(root, sdir) else 'missing (next session start fixes it)'}")
    print(f"  index: {index.lines}/{config.INDEX_MAX_LINES} lines, "
          f"{index.bytes}/{config.INDEX_MAX_BYTES} bytes"
          + (f"; collapsed: {', '.join(index.collapsed)}" if index.collapsed else ""))
    for p in session.conflict_copies(binding.bundles):
        print(f"  conflict copy to review: {p}")


def cmd_sync(a) -> None:
    _need_store()
    res = store.sync()
    if a.quiet:
        return
    if res.offline:
        print("Offline (or remote unreachable); changes are committed locally.")
    elif store.remote_branch() is None:
        print("No remote; committed locally.")
    else:
        print(("Pulled new memories. " if res.pulled else "Up to date. ")
              + ("Pushed." if res.pushed else ""))
    for f in res.conflicts:
        print(f"Resolved a conflict in {f}")


def cmd_hooks(a) -> None:
    if a.remove:
        print("Removed Telepathy hooks." if claude.remove_hooks() else "No Telepathy hooks found.")
    else:
        print(f"Hooks installed in {claude.install_hooks()}")


# --- hooks (run by Claude Code) ------------------------------------------------------------

def _payload() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return {}


def _recently_fetched(minutes: int = 15) -> bool:
    stamp = config.store_dir() / ".git" / "FETCH_HEAD"
    try:
        return (datetime.now().timestamp() - stamp.stat().st_mtime) < minutes * 60
    except OSError:
        return False


def hook_start(payload: dict) -> str:
    if not store.exists():
        return ""
    found = project_key(Path(payload.get("cwd") or os.getcwd()))
    if found is None:
        return ""
    key, root = found
    old_state = session.load_state(key)
    binding = store.load_bindings().get(key)
    if binding is None:
        # Maybe another machine chose bundles for this project. Look, but at most every
        # 15 minutes, so projects without bundles don't pay a network call per session.
        if not _recently_fetched():
            store.sync(push=False, timeout=10)
            binding = store.load_bindings().get(key)
        if binding is None:
            return ""
    session.harvest(key, binding)             # anything left over from a session that crashed
    res = store.sync(push=False, timeout=10)
    index = session.build(key, binding)
    first = claude.ensure_project(root, session.session_dir(key))

    out = []
    if first:
        out.append("Telepathy: this project's memory now comes from the bundles "
                   f"{', '.join(binding.bundles)}. Claude Code switches to them from the next "
                   f"session. Until then, this is that memory (files under "
                   f"{session.session_dir(key)}):\n\n{index.text}")
    else:
        changed = session.arrivals(old_state.get("head", ""), binding.bundles)
        if changed:
            out.append("Telepathy: these memories were added or changed (usually on another "
                       "machine) after your memory index was loaded. Read them when relevant:")
            out += [session.index_line_for(b, f) for _, b, f in changed]
    if res.conflicts:
        out.append("Telepathy: resolved sync conflicts in " + ", ".join(res.conflicts)
                   + "; the other versions are saved next to them as *.conflict-N.md.")
    return "\n".join(out)


def hook_end(payload: dict) -> str:
    if not store.exists():
        return ""
    found = project_key(Path(payload.get("cwd") or os.getcwd()))
    if found is None:
        return ""
    key, _ = found
    binding = store.load_bindings().get(key)
    if binding is None:
        return ""
    session.harvest(key, binding)
    session.build(key, binding)
    store.commit(f"tp: session end on {config.machine()} ({key})")
    if store.remote_branch() is not None:
        claude.spawn_background("sync", "--quiet")
    return ""


def cmd_hook(a) -> None:
    """Never break the agent's session: log errors and exit 0."""
    try:
        # Windows pipes default to the ANSI code page; memories can hold any character.
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        text = (hook_start if a.event == "start" else hook_end)(_payload())
        if text:
            print(text)
    except Exception:
        log = config.home() / "hook-errors.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now().isoformat()} {a.event}\n{traceback.format_exc()}")
        except OSError:
            pass
        print(f"telepathy: hook {a.event} failed, see {log}", file=sys.stderr)


# --- entry point -----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="tp", description="Your coding agent's memory, on every machine.")
    p.add_argument("--version", action="version", version=f"telepathy {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="set up this machine: clone or create your store, install hooks")
    s.add_argument("url", nargs="?", help="your private memory repo (omit to create a local store)")
    s.add_argument("--no-hooks", action="store_true", help="don't touch Claude Code settings")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("use", help="choose the bundles this project loads")
    s.add_argument("bundles", nargs="+")
    s.add_argument("--write", help="bundle for new project memories (default: the last non-personal one)")
    s.set_defaults(fn=cmd_use)

    s = sub.add_parser("new", help="create an empty bundle")
    s.add_argument("name")
    s.add_argument("--description")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("import", help="copy an existing memory folder into a bundle")
    s.add_argument("source", help="e.g. ~/.claude/projects/<project>/memory")
    s.add_argument("bundle")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("status", help="what this machine and project are using")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("sync", help="pull and push the store now")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("hooks", help="install (or --remove) the Claude Code hooks")
    s.add_argument("--remove", action="store_true")
    s.set_defaults(fn=cmd_hooks)

    s = sub.add_parser("hook", help=argparse.SUPPRESS)
    s.add_argument("event", choices=["start", "end"])
    s.set_defaults(fn=cmd_hook)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except (GitError, claude.SettingsError, RuntimeError, ValueError) as e:
        sys.exit(f"tp: {e}")
