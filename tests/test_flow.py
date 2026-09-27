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

    settings = json.loads((project / ".claude/settings.local.json").read_text(encoding="utf-8"))
    assert settings["autoMemoryDirectory"] == config.tilde(sdir)
    assert set(claude.allow_rules()) <= set(settings["permissions"]["allow"])
    # never committed into the project
    exclude = (project / ".git/info/exclude").read_text(encoding="utf-8")
    assert ".claude/settings.local.json" in exclude
    run("git", "check-ignore", "-q", ".claude/settings.local.json", cwd=project)

    assert session.is_link(sdir / "personal") and session.is_link(sdir / "my-project")
    assert "## my-project" in (sdir / "MEMORY.md").read_text(encoding="utf-8")
    # hooks installed in the (fake) user settings, pointing at this interpreter
    user_settings = config.claude_config_dir() / "settings.json"
    hooks = json.loads(user_settings.read_text(encoding="utf-8"))["hooks"]
    assert "telepathy hook start" in hooks["SessionStart"][0]["hooks"][0]["command"]
    # binding reached the remote
    assert "my-project" in run("git", "--git-dir", str(remote), "show", "main:projects.toml")


def test_existing_settings_are_kept(tmp_path, monkeypatch, remote):
    project = setup_machine(tmp_path, monkeypatch, "machine-a", remote, "p")
    (project / ".claude").mkdir()
    (project / ".claude/settings.local.json").write_text(
        json.dumps({"model": "x", "permissions": {"allow": ["Bash(ls)"]}}), encoding="utf-8")
    monkeypatch.chdir(project)
    main(["use", "personal", "my-project"])
    s = json.loads((project / ".claude/settings.local.json").read_text(encoding="utf-8"))
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
    with (sdir / "MEMORY.md").open("a", encoding="utf-8") as f:
        f.write("- [Tabs](personal/user_tabs.md) — prefers tabs\n"
                "- [Staging](project_staging.md) — staging server name\n")
    monkeypatch.setattr(claude, "spawn_background", lambda *a: store.sync())
    hook_end({"cwd": str(project_a)})

    assert (store.bundle_path("personal") / "user_tabs.md").exists()
    assert (store.bundle_path("my-project") / "project_staging.md").exists()
    assert not (sdir / "project_staging.md").exists()
    assert "- [Staging](project_staging.md) — staging server name" in \
        (store.bundle_path("my-project") / "MEMORY.md").read_text(encoding="utf-8")
    assert "- [Tabs](user_tabs.md) — prefers tabs" in \
        (store.bundle_path("personal") / "MEMORY.md").read_text(encoding="utf-8")

    # Machine B: same project at another path. It has no choice of its own yet, so it is
    # told (once) what machine A loads, and chooses for itself.
    project_b = setup_machine(tmp_path, monkeypatch, "machine-b", remote, "elsewhere/my-project")
    out = hook_start({"cwd": str(project_b)})
    assert "machine-a: personal my-project" in out and "tp use personal my-project" in out
    assert hook_start({"cwd": str(project_b)}) == ""          # shown once
    assert not (project_b / ".claude").exists()               # nothing chosen, nothing changed
    monkeypatch.chdir(project_b)
    main(["use", "personal", "my-project"])
    assert hook_start({"cwd": str(project_b)}) == ""          # set up by `tp use` already
    assert "](my-project/project_staging.md)" in \
        (session.session_dir(key) / "MEMORY.md").read_text(encoding="utf-8")
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
    bundle = store.bundle_path("my-project")
    assert "from B" in (bundle / f).read_text(encoding="utf-8")   # newer wins
    assert "from A" in (bundle / "project_x.conflict-1.md").read_text(encoding="utf-8")
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
    path.write_text(json.dumps(other), encoding="utf-8")
    claude.install_hooks()
    claude.install_hooks()                         # idempotent
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["hooks"]["SessionStart"]) == 2
    assert claude.remove_hooks()
    assert json.loads(path.read_text(encoding="utf-8")) == other
    assert (path.parent / "settings.json.before-telepathy").exists()


def test_links_never_delete_bundle_contents(tmp_path, monkeypatch):
    store.init()
    b = store.make_binding(["personal", "proj"])
    session.build("local/p", b)
    memory(store.bundle_path("proj") / "keep.md", "keep", "project", "important")
    session.build("local/p", store.make_binding(["personal"]))   # proj dropped from binding
    assert not (session.session_dir("local/p") / "proj").exists()
    assert (store.bundle_path("proj") / "keep.md").exists()


def test_plain_folder_project(tmp_path, monkeypatch, remote):
    use_machine(tmp_path, monkeypatch, "machine-a")
    main(["init", str(remote)])
    folder = tmp_path / "machine-a" / "Research"
    folder.mkdir()
    monkeypatch.chdir(folder)
    main(["use", "personal", "research"])
    assert "dir/Research" in store.load_bindings()
    assert claude.project_points_here(folder, session.session_dir("dir/Research"))

    use_machine(tmp_path, monkeypatch, "machine-b")
    main(["init", str(remote)])
    other = tmp_path / "machine-b" / "somewhere" / "Research"
    other.mkdir(parents=True)
    assert "machine-a: personal research" in hook_start({"cwd": str(other)})


def test_each_machine_chooses_its_own_bundles(tmp_path, monkeypatch, remote):
    project_a = setup_machine(tmp_path, monkeypatch, "machine-a", remote, "a/p")
    monkeypatch.chdir(project_a)
    main(["use", "personal", "my-project"])

    project_b = setup_machine(tmp_path, monkeypatch, "machine-b", remote, "b/p")
    monkeypatch.chdir(project_b)
    main(["new", "windows", "--description", "Windows-specific: paths, shells, quirks"])
    main(["use", "personal", "windows", "my-project", "--write", "my-project"])
    key = "github.com/me/my-project"
    index = (session.session_dir(key) / "MEMORY.md").read_text(encoding="utf-8")
    assert "`windows/` = Windows-specific: paths, shells, quirks" in index
    assert session.is_link(session.session_dir(key) / "windows")

    use_machine(tmp_path, monkeypatch, "machine-a")
    store.sync()
    assert store.load_bindings()[key].bundles == ["personal", "my-project"]   # A unchanged
    assert store.other_machines(key)["machine-b"].bundles == ["personal", "windows", "my-project"]
    hook_start({"cwd": str(project_a)})
    assert not (session.session_dir(key) / "windows").exists()


def test_first_format_is_read_as_this_machines(tmp_path, monkeypatch):
    store.init()
    (config.store_dir() / "projects.toml").write_text(
        '["dir/Research"]\nbundles = ["personal", "research"]\nwrite = "research"\n',
        encoding="utf-8")
    assert store.load_bindings()["dir/Research"].bundles == ["personal", "research"]
    store.save_binding("local/x", store.make_binding(["x"]))
    text = (config.store_dir() / "projects.toml").read_text(encoding="utf-8")
    assert '[machines."machine-a"."dir/Research"]' in text and '["dir/Research"]' not in text


def test_rename_machine_and_same_name_warning(tmp_path, monkeypatch, remote, capsys):
    project = setup_machine(tmp_path, monkeypatch, "machine-a", remote, "p")
    monkeypatch.chdir(project)
    main(["use", "personal", "my-project"])
    monkeypatch.delenv("TELEPATHY_MACHINE")          # from here on, the saved name counts
    main(["init", "--machine", "linux"])
    assert "linux" in store.load_all() and "machine-a" not in store.load_all()
    assert config.machine() == "linux"
    assert store.load_bindings()["github.com/me/my-project"].bundles == ["personal", "my-project"]

    # a second machine whose hostname-based default collides with an existing name
    monkeypatch.setenv("TELEPATHY_HOME", str(tmp_path / "machine-b" / ".telepathy"))
    monkeypatch.setattr(config.socket, "gethostname", lambda: "linux")
    capsys.readouterr()
    main(["init", str(remote)])
    assert "'linux' (this machine's hostname) already has bundle choices" in capsys.readouterr().out


def _unnamed_machine(tmp_path, monkeypatch, name, hostname):
    """A machine that never saved a name (e.g. set up before names existed): it goes by its
    hostname, which it may share with another machine."""
    monkeypatch.setenv("TELEPATHY_HOME", str(tmp_path / name / ".telepathy"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / name / ".claude"))
    monkeypatch.delenv("TELEPATHY_MACHINE", raising=False)
    monkeypatch.setattr(config.socket, "gethostname", lambda: hostname)


def test_naming_an_unnamed_machine_keeps_its_choices(tmp_path, monkeypatch, remote, capsys):
    _unnamed_machine(tmp_path, monkeypatch, "machine-a", "shared-host")
    main(["init", str(remote)])
    (config.home() / "machine").unlink()             # choices saved before names existed
    project = make_project(tmp_path / "machine-a" / "p")
    monkeypatch.chdir(project)
    main(["use", "personal", "my-project"])
    key = "github.com/me/my-project"
    assert store.named_machines() == {"shared-host"}

    capsys.readouterr()
    main(["init", "--machine", "windows"])
    assert "Copied 1 project choices from 'shared-host'" in capsys.readouterr().out
    assert config.machine() == "windows"
    assert store.load_bindings()[key].bundles == ["personal", "my-project"]   # not orphaned
    # the hostname may be another machine's too, so its choices stay where they were
    assert store.other_machines(key)["shared-host"].bundles == ["personal", "my-project"]


def test_first_format_store_gives_no_false_warning(tmp_path, monkeypatch, remote, capsys):
    _unnamed_machine(tmp_path, monkeypatch, "machine-a", "some-host")
    main(["init", str(remote)])
    (config.store_dir() / "projects.toml").write_text(
        '["dir/Research"]\nbundles = ["personal", "research"]\nwrite = "research"\n',
        encoding="utf-8")
    (config.home() / "machine").unlink()
    capsys.readouterr()
    main(["init"])
    assert "Warning" not in capsys.readouterr().out
    assert store.named_machines() == set()


def test_machine_flag_refused_while_env_overrides_it(monkeypatch):
    monkeypatch.setenv("TELEPATHY_MACHINE", "from-env")
    try:
        main(["init", "--machine", "other"])
    except SystemExit as e:
        assert "TELEPATHY_MACHINE" in str(e.code)
    else:
        raise AssertionError("init --machine should refuse while TELEPATHY_MACHINE is set")
    assert not (config.home() / "machine").exists()


def test_init_commits_only_as_the_user(tmp_path, monkeypatch):
    store.init()                                     # the test gitconfig has no identity
    assert run("git", "log", "--all", "--format=%ae", cwd=config.store_dir()).strip() == ""
    assert store.dirty()                             # the next commit takes the files along


def test_written_files_use_lf(tmp_path, monkeypatch, remote):
    project = setup_machine(tmp_path, monkeypatch, "machine-a", remote, "p")
    monkeypatch.chdir(project)
    main(["use", "personal", "my-project"])
    written = [project / ".claude/settings.local.json",
               config.claude_config_dir() / "settings.json",
               config.store_dir() / "projects.toml", config.home() / "machine",
               session.session_dir("github.com/me/my-project") / "MEMORY.md"]
    for path in written:
        assert b"\r\n" not in path.read_bytes(), path
