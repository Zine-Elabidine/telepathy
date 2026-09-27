from telepathy import config, memfile, session, store
from telepathy.gitx import key_to_dirname, normalize_remote
from telepathy.store import Binding


def test_normalize_remote():
    want = "github.com/Me/Repo"
    for url in ["git@github.com:Me/Repo.git", "https://github.com/Me/Repo",
                "https://github.com/Me/Repo.git/", "ssh://git@github.com:22/Me/Repo.git",
                "https://user@GitHub.com/Me/Repo"]:
        assert normalize_remote(url) == want, url
    assert key_to_dirname("github.com/Me/Repo") == "github.com--Me--Repo"


def test_frontmatter_claude_format():
    text = ('---\nname: user-indentation\ndescription: "prefers tabs"\nmetadata:\n'
            '  node_type: memory\n  type: user\n  modified: 2026-09-27T18:39:03.066Z\n---\n\nbody\n')
    fm = memfile.frontmatter(text)
    assert fm["name"] == "user-indentation"
    assert fm["description"] == "prefers tabs"
    assert fm["type"] == "user"
    assert fm["modified"] == "2026-09-27T18:39:03.066Z"


def test_parse_index_keeps_local_links_only():
    text = ("# title\n- [A](a.md) — x\n- [B](other/b.md) — y\n"
            "- [C](c.md) — z\nsome text [D](d.md)\n")
    assert list(memfile.parse_index(text)) == ["a.md", "c.md"]


def _bundle_with(name, n):
    path = store.new_bundle(name)
    for i in range(n):
        (path / f"m{i}.md").write_text(f"---\nname: m{i}\ndescription: \"{'x' * 60}\"\n"
                                       f"metadata:\n  type: project\n---\nbody\n", encoding="utf-8")
    return store.write_index(name)


def test_write_index_files_are_the_truth():
    store.init()
    path = store.new_bundle("proj")
    (path / "MEMORY.md").write_text("- [Kept title](a.md) — hand written\n- [Gone](gone.md) — x\n",
                                    encoding="utf-8")
    (path / "a.md").write_text("---\nname: a\ndescription: \"aa\"\n---\n", encoding="utf-8")
    (path / "b.md").write_text("---\nname: b-thing\ndescription: \"bb\"\n---\n", encoding="utf-8")
    lines = store.write_index("proj")
    assert lines == {"a.md": "- [Kept title](a.md) — hand written",
                     "b.md": "- [B thing](b.md) — bb"}


def test_render_sections_and_collapse():
    store.init()
    per = {"personal": _bundle_with("personal", 5), "proj": _bundle_with("proj", 150),
           "wiki": _bundle_with("wiki", 120)}
    b = Binding(["personal", "proj", "wiki"], "proj", "personal")
    index = session.render(b, per)
    assert index.lines <= config.INDEX_MAX_LINES and index.bytes <= config.INDEX_MAX_BYTES
    assert index.collapsed == ["wiki"]            # biggest non-write bundle goes first
    assert "(wiki/MEMORY.md)" in index.text
    assert "](proj/m149.md)" in index.text        # write bundle stays listed
    assert index.text.index("## proj") < index.text.index("## personal")


def test_make_binding_defaults():
    assert store.make_binding(["personal", "proj"]) == Binding(["personal", "proj"], "proj", "personal")
    assert store.make_binding(["proj"]) == Binding(["proj"], "proj", None)
    b = store.make_binding(["personal", "proj", "wiki"], write="proj")
    assert b.target_for("user") == "personal" and b.target_for("project") == "proj"


def test_bindings_roundtrip():
    store.init()
    store.save_binding("github.com/me/x", Binding(["personal", "x"], "x", "personal"))
    store.save_binding("local/y", Binding(["y"], "y"))
    got = store.load_bindings()
    assert got["github.com/me/x"] == Binding(["personal", "x"], "x", "personal")
    assert got["local/y"] == Binding(["y"], "y", None)
