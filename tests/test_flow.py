"""End-to-end: two machines, one remote, Claude Code simulated by writing files where
it would write them."""

import json
import os
from pathlib import Path

from conftest import make_project, memory, run, use_machine
from telepathy import claude, config, session, store
from telepathy.cli import hook_end, hook_start, main


def setup_machine(tmp_path, monkeypatch, name, remote, project_dir):
    use_machine(tmp_path, monkeypatch, name)
    main(["init", str(remote)])
    project = make_project(tmp_path / name / project_dir)
    return project


def test_use_writes_settings_and_links(tmp_path, monkeypatch, remote, capsys):
    project = setup_machine(tmp_path, monkeypatch, "machine-a", remote, "code/my-project")
    monkeypatch.chdir(project)
    main(["use", "personal", "my-project"])
    key = "github.com/me/my-project"
    sdir = session.session_dir(key)

    settings = json.loads((project / ".claude/settings.local.json").read_text())
    assert settings["autoMemoryDirectory"] == config.tilde(sdir)
    assert set(claude.allow_rules()) <= set(settings["permissions"]["allow"])
    # never committed into the project
    assert ".claude/settings.local.json" in (project / ".git/info/exclude").read_text()
    run("git", "check-ignore", "-q", ".claude/settings.local.json", cwd=project)

    assert session.is_link(sdir / "personal") and session.is_link(sdir / "my-project")
    assert "## my-project" in (sdir / "MEMORY.md").read_text()
    # hooks installed in the (fake) user settings, pointing at this interpreter
    hooks = json.loads((config.claude_config_dir() / "settings.json").read_text())["hooks"]
    assert "telepathy hook start" in hooks["SessionStart"][0]["hooks"][0]["command"]
    # binding reached the remote
    assert "my-project" in run("git", "--git-dir", str(remote), "show", "main:projects.toml")


def test_existing_settings_are_kept(tmp_path, monkeypatch, remote):
    project = setup_machine(tmp_path, monkeypatch, "machine-a", remote, "p")
    (project / ".claude").mkdir()
    (project / ".claude/settings.local.json").write_text(
        json.dumps({"model": "x", "permissions": {"allow": ["Bash(ls)"]}}))
    monkeypatch.chdir(project)
    main(["use", "personal", "my-project"])
    s = json.loads((project / ".claude/settings.local.json").read_text())
    assert s["model"] == "x" and "Bash(ls)" in s["permissions"]["allow"]


def test_session_round_trip_between_machines(tmp_path, monkeypatch, remote):
    # Machine A: choose bundles, have "Claude" write two memories, end the session.
    project_a = setup_machine(tmp_path, monkeypatch, "machine-a", remote, "a/my-project")
    monkeypatch.chdir(project_a)
    main(["use", "personal", "my-project"])
    key = "github.com/me/my-project"
    sdir = session.session_dir(key)

    # Claude writes one memory inside the bundle folder (as measured) ...
    memory(sdir / "personal" / "user_tabs.md", "user-tabs", "user", "Prefers tabs")
    # ... and one stray at the top of the memory folder, with its index line.
    memory(sdir / "project_staging.md", "project-staging", "project", "Staging is kestrel-02")
    with (sdir / "MEMORY.md").open("a") as f:
        f.write("- [Tabs](personal/user_tabs.md) — prefers tabs\n"
                "- [Staging](project_staging.md) — staging server name\n")
    monkeypatch.setattr(claude, "spawn_background", lambda *a: store.sync())
    hook_end({"cwd": str(project_a)})

    assert (store.bundle_path("personal") / "user_tabs.md").exists()
    assert (store.bundle_path("my-project") / "project_staging.md").exists()
    assert not (sdir / "project_staging.md").exists()
    assert "- [Staging](project_staging.md) — staging server name" in \
        (store.bundle_path("my-project") / "MEMORY.md").read_text()
    assert "- [Tabs](user_tabs.md) — prefers tabs" in \
        (store.bundle_path("personal") / "MEMORY.md").read_text()

    # Machine B: same project at another path. No `tp use` needed.
    project_b = setup_machine(tmp_path, monkeypatch, "machine-b", remote, "elsewhere/my-project")
    out = hook_start({"cwd": str(project_b)})
    assert "from the next session" in out          # first session on this machine
    assert "kestrel-02" not in out                 # index only, bodies are read on demand
    assert "](my-project/project_staging.md)" in out
    assert claude.project_points_here(project_b, session.session_dir(key))
    assert (session.session_dir(key) / "my-project" / "project_staging.md").exists()

    # A again: a new memory arrives from B; A's next session start announces it.
    sdir_b = session.session_dir(key)
    memory(sdir_b / "my-project" / "project_db.md", "project-db", "project", "DB is Postgres 17")
    hook_end({"cwd": str(project_b)})

    use_machine(tmp_path, monkeypatch, "machine-a")
    out = hook_start({"cwd": str(project_a)})
    assert "another" in out and "](my-project/project_db.md)" in out
    out = hook_start({"cwd": str(project_a)})
    assert out == ""                               # nothing new the second time


def test_conflict_newer_wins_and_other_kept(tmp_path, monkeypatch, remote):
    project_a = setup_machine(tmp_path, monkeypatch, "machine-a", remote, "a/p")
    monkeypatch.chdir(project_a)
    main(["use", "personal", "my-project"])
    f = "project_x.md"
    memory(store.bundle_path("my-project") / f, "x", "project", "v1", "2026-01-01T00:00:00Z")
    store.sync()

    setup_machine(tmp_path, monkeypatch, "machine-b", remote, "b/p")
    memory(store.bundle_path("my-project") / f, "x", "project", "from B", "2026-01-03T00:00:00Z")
    store.sync()

    use_machine(tmp_path, monkeypatch, "machine-a")
    memory(store.bundle_path("my-project") / f, "x", "project", "from A", "2026-01-02T00:00:00Z")
    res = store.sync()
    assert f"bundles/my-project/{f}" in res.conflicts
    assert "from B" in (store.bundle_path("my-project") / f).read_text()   # newer wins
    assert "from A" in (store.bundle_path("my-project") / "project_x.conflict-1.md").read_text()
    assert res.pushed


def test_offline_is_not_an_error(tmp_path, monkeypatch, remote):
    project = setup_machine(tmp_path, monkeypatch, "machine-a", remote, "p")
    monkeypatch.chdir(project)
    main(["use", "personal", "my-project"])
    remote.rename(tmp_path / "gone.git")
    memory(store.bundle_path("personal") / "user_a.md", "a", "user", "x")
    res = store.sync()
    assert res.offline and not store.dirty()       # committed locally, pushed later


def test_unbound_project_is_left_alone(tmp_path, monkeypatch, remote):
    project = setup_machine(tmp_path, monkeypatch, "machine-a", remote, "p")
    assert hook_start({"cwd": str(project)}) == ""
    assert hook_end({"cwd": str(project)}) == ""
    assert not (project / ".claude").exists()


def test_hooks_remove_restores(tmp_path, monkeypatch):
    path = config.claude_config_dir() / "settings.json"
    path.parent.mkdir(parents=True)
    other = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
             "model": "opus"}
    path.write_text(json.dumps(other))
    claude.install_hooks()
    claude.install_hooks()                         # idempotent
    data = json.loads(path.read_text())
    assert len(data["hooks"]["SessionStart"]) == 2
    assert claude.remove_hooks()
    assert json.loads(path.read_text()) == other
    assert (path.parent / "settings.json.before-telepathy").exists()


def test_links_never_delete_bundle_contents(tmp_path, monkeypatch):
    store.init()
    b = store.make_binding(["personal", "proj"])
    session.build("local/p", b)
    memory(store.bundle_path("proj") / "keep.md", "keep", "project", "important")
    session.build("local/p", store.make_binding(["personal"]))   # proj dropped from binding
    assert not (session.session_dir("local/p") / "proj").exists()
    assert (store.bundle_path("proj") / "keep.md").exists()
