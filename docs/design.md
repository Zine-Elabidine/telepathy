# Telepathy: design

Status: **draft** (2026-09-27). The shape below has been agreed in discussion. The
individual decisions are marked *proposed* until they are locked one by one.

## 1. The problem

Claude Code's auto memory is good: it writes down what you correct, keeps a short index
(`MEMORY.md`) in every session and reads the details when it needs them. But it is
stuck in two ways.

1. **Stuck to one machine.** The memory folder is `~/.claude/projects/<project>/memory/`,
   and `<project>` comes from the repo's path *on this machine*. The docs say it plainly:
   *"Auto memory is machine-local… Files are not shared across machines."* If you work on
   two machines (say Linux and Windows), you end up copying memory files between them by hand.
2. **Stuck to one project.** Each project has exactly one memory folder. Things that are
   true everywhere (who you are, how you want commits written, which email to use in personal
   repos) have to be re-learned or copied into every project.

## 2. The idea

Memory becomes **bundles**: named folders of memory files, such as `personal`,
`my-project` or `rl-notes`. Each project says which bundles it uses. A session sees
those bundles combined as one ordinary memory folder. Git moves the bundles between
machines, and bundles can be shared and installed like packages.

What Telepathy is **not**:
- A new memory engine. There is no database and no embeddings, and nothing replaces the
  agent's own memory. Claude Code keeps writing and reading memory the way it already does.
- A sync service. Git is the transport, and there's nothing to host.

## 3. Principles

1. **Native first.** The agent sees its normal memory. We change *which files* it sees,
   never *how* it uses them. Anything the agent has to remember to call (an MCP tool)
   is a fallback, not the core.
2. **Files are the truth.** A bundle is markdown files with YAML frontmatter. Every index is
   generated from them and can be rebuilt at any time.
3. **Git is the transport.** Your store is a private git repo; shared bundles are public ones.
   History, undo and access control come for free.
4. **Choose, don't copy everything.** A session loads the bundles its project asked for,
   nothing more.
5. **Private by default.** Publishing a bundle is an explicit act, with a scan first.
6. **The core knows nothing about agents.** Store, bundles, combining and git live in the
   core. Each agent gets a small adapter. Claude Code is adapter #1.

## 4. Concepts

| Term | Meaning |
|---|---|
| **Memory** | One markdown file with frontmatter (`name`, `description`, `metadata.type`), the format Claude Code already writes |
| **Bundle** | A folder of memories plus a generated `MEMORY.md` index and a small manifest |
| **Store** | Your private git repo that holds your own bundles, your project bindings and the list of installed bundles |
| **Installed bundle** | Someone else's bundle, cloned from their repo, pinned to a commit, read-only |
| **Binding** | "Project X uses bundles A, B, C, and new project memories go to B" |
| **Session folder** | The folder the agent is pointed at: a combined index plus one linked subfolder per bundle |
| **Project key** | How a project is recognised on any machine: its normalised git remote URL (fallback: folder name) |

## 5. What it looks like on disk *(proposed)*

```
~/.telepathy/
├── store/                          ← your private git repo (cloned on every machine)
│   ├── bundles/
│   │   ├── personal/
│   │   │   ├── bundle.toml
│   │   │   ├── MEMORY.md           ← generated
│   │   │   └── feedback_short_commits.md
│   │   └── my-project/ …
│   ├── projects.toml               ← bindings, keyed by project key
│   └── installed.lock              ← installed bundles + pinned commits
├── installed/
│   └── someone--grpo-notes/        ← clone of someone else's bundle repo
└── sessions/
    └── github.com--me--my-project/ ← what Claude Code is pointed at
        ├── MEMORY.md               ← generated: one section per bundle
        ├── personal/   → ~/.telepathy/store/bundles/personal
        ├── my-project/ → ~/.telepathy/store/bundles/my-project
        └── grpo-notes/ → ~/.telepathy/installed/someone--grpo-notes
```

`projects.toml`:

```toml
["github.com/me/my-project"]
bundles = ["personal", "my-project", "grpo-notes"]
write = "my-project"        # where project memories go; user/feedback memories go to "personal"
```

Links are symlinks on Linux/macOS. On Windows they are **directory junctions**, which need
no admin rights or Developer Mode. *(Still to test on Windows.)*

## 6. Commands *(proposed)*

| Command | What it does |
|---|---|
| `tp init [repo-url]` | Once per machine: clone (or create) the store, install the agent hooks, optionally import existing memory folders as bundles |
| `tp use <bundle>…` | In a project: choose the bundles this machine loads for it (saved in the store) and write the agent setting |
| `tp new <bundle>` | Create an empty bundle |
| `tp add <source>` | Install a shared bundle, e.g. `github:owner/repo[/path][@ref]` |
| `tp update [bundle]` | Pull newer versions of installed bundles (updates the lock) |
| `tp publish <bundle> --to <repo>` | Export one of your bundles for sharing, after a scan |
| `tp sync` | Pull and push the store by hand (hooks normally do it) |
| `tp status` | Show this project's bundles, the index size and anything not pushed yet |
| `tp move <memory> <bundle>` | Move a memory to another bundle |

The command is **`tp`** (like `rg`, `gh`, `uv`). The package also installs the long name
`telepathy`. `tlp` was avoided because a well-known Linux battery tool already uses it. The
PyPI package name may need a variant if `telepathy` is taken; the project stays Telepathy.

## 7. The session lifecycle (Claude Code adapter)

### What Claude Code gives us (verified in the docs, 2026-09-27)

- `autoMemoryDirectory` in settings moves the memory folder. It's read from any settings
  scope (user, project, local, policy, `--settings`) and must be absolute or start with `~/`.
- From the project scope it is honoured only under the workspace trust rule. With
  `permissions.blockReadsOutsideWorkingDirectories` on, a repo-supplied memory folder is
  ignored completely.
- At session start only the first **200 lines or 25KB** of `MEMORY.md` load. Topic files
  are read on demand.
- Settings are read **before** `SessionStart` hooks run. The hooks run in the background.
- `SessionStart` stdout becomes context that Claude sees.
- `SessionEnd` hooks share a 1.5s budget, which can be raised to 60s with a per-hook `timeout`.
- Plugins can ship hooks (`hooks/hooks.json`). They run once the plugin is enabled.

### What we measured (Claude Code 2.1.283, Linux, 2026-09-27)

| Test | Result |
|---|---|
| `autoMemoryDirectory` via `--settings` | ✅ memory loads from our folder |
| Start hook rewrites `MEMORY.md` before it is used | ❌ the index is read before the hook finishes, even for a 0.2s hook, in headless and interactive sessions |
| Start hook prints what changed | ✅ Claude sees it and uses the new facts |
| Bundles as symlinked subfolders | ⚠️ denied by default (the memory-folder permission does not extend to link targets); ✅ works with allow rules `Read(//<path>/**)` + `Edit(//<path>/**)` for the session folder and the store. `Write(...)` rules are ignored; `Edit` covers writing |
| New memories with a sectioned index | ✅ Claude wrote a `type: user` memory into `personal/` and a `type: project` memory into the project bundle by itself, and updated the right sections (one run, Sonnet) |

### The lifecycle *(proposed)*

**Once per project per machine** (`tp use`, or the first start hook that finds a
binding): write to the project's `.claude/settings.local.json`:

```json
{
  "autoMemoryDirectory": "~/.telepathy/sessions/github.com--me--my-project",
  "permissions": { "allow": [
    "Read(//home/me/.telepathy/**)", "Edit(//home/me/.telepathy/store/**)",
    "Edit(//home/me/.telepathy/sessions/**)"
  ] }
}
```

Also add the file to `.git/info/exclude`, so it can never be committed and the repo itself
doesn't change. Installed bundles get `Read` only, which makes them read-only for the agent.

**Session start** (hook, command type):
1. `git pull --rebase` the store (short timeout; offline is fine).
2. Rebuild the session folder: the links plus the combined index.
3. Print to stdout (it becomes context):
   - memories that arrived from another machine since the folder was last built, because
     the index already loaded is the *old* one (measured above);
   - on the first session of a project on a new machine, where the setting only takes
     effect next time, the whole combined index, so even that session is not blind.

**During the session:** nothing. Claude Code reads and writes memory natively, through the
links, straight into the right bundles.

**Session end** (hook, `timeout` raised; the push is detached so a slow network never
blocks exit):
1. Sort stray files: a new memory written at the top of the session folder, instead of
   inside a bundle, goes by its `metadata.type`: `user`/`feedback` → `personal`,
   `project`/`reference` → the binding's `write` bundle.
2. Regenerate each bundle's own `MEMORY.md` from its files' frontmatter.
3. `git add -A && git commit && git push` the store.

## 8. The index budget *(proposed)*

The combined `MEMORY.md` must fit in 200 lines / 25KB, or the tail is silently dropped.

- Build it one section per bundle, in binding order (write bundle first).
- If it fits, list every memory.
- If it doesn't, the biggest bundles collapse to one line each, pointing at their own index
  (`personal/MEMORY.md`). Claude reads that file when it needs it, which is the same
  progressive disclosure Claude Code already uses for topic files.
- `tp status` shows the size and what collapsed.

## 9. Sync and conflicts *(proposed)*

- Two machines rarely edit the same memory offline, so v0.1 stays simple:
  - Rebase on pull.
  - On a conflict in a memory file, keep the version with the newer `modified`
    frontmatter (Claude Code stamps it on every write) and save the other as
    `<name>.conflict-<machine>.md` for review.
- Indexes never conflict: they are generated, so they are rebuilt, never merged.
- Later: an LLM merge for real conflicts (claude-brain does this for a few cents per sync).

## 10. Sharing *(proposed)*

A shared bundle is a git repo, or a subfolder of one, containing memory files and a
`bundle.toml`:

```toml
name = "grpo-notes"
version = "0.3.0"
description = "What I learned implementing GRPO from scratch"
license = "CC-BY-4.0"
authors = ["…"]
```

- `add` clones it into `~/.telepathy/installed/` and records the commit in `installed.lock`.
  Your other machines install the same commit at their next sync.
- Installed bundles are read-only. To change one, fork it: `tp new mine --from grpo-notes`.
- `publish` copies a bundle out to a repo you choose, after:
  - a secrets scan: token patterns (`github_pat_`, `sk-`, `AKIA`, private keys), emails;
  - your own deny list (e.g. company and client names), kept in the store config;
  - a list of every file that would go out, for you to confirm.
- No registry in v0.1. GitHub is the registry; an index page can come later.
- Format: aim to be compatible with **OKF** (Open Knowledge Format, Google Cloud, v0.2),
  which already calls this unit a "bundle": markdown plus YAML frontmatter, with `index.md`
  and `log.md` reserved. *(The details still need checking against the spec before we
  commit to it.)*

## 11. Security

- The store is private. Nothing is published without `publish`.
- The publish scan is described in §10.
- Permission rules are scoped to `~/.telepathy/`, so the agent gets nothing beyond its memory.
- Installed bundles are someone else's text in your agent's context: treat them like code
  you install. Read-only, pinned to a commit, updated only when you ask.
- If `blockReadsOutsideWorkingDirectories` is on, `tp use` warns that the setting
  will be ignored instead of failing silently.

## 12. Prior art and how Telepathy differs

Checked 2026-09-22 and 2026-09-27; star counts are from then.

| Group | Examples | What they do | Difference |
|---|---|---|---|
| Sync tools | claude-brain, memoir (14★), mycelium (31★), ~5 others | Copy memory (or all of `~/.claude`) between machines | They copy everything; no choosing, no combining, no installing other people's knowledge |
| Memory engines | claude-mem, CogniLayer, observational-memory, Snipara | Their own database behind a tool, sometimes shared by Claude Code and Codex | They replace native memory, the agent has to decide to look things up, and there are no bundles |
| Formats | Karpathy's LLM Wiki, OKF | How to keep a knowledge base, and a file format for one | No tool to install a bundle and load it into an agent (we build on them) |
| By hand | Team guides for exporting memory folders, cleaning out secrets and merging them into CLAUDE.md | Manual sharing | Shows the demand for sharing |

What's new: **named bundles, combined per session with native memory, with new memories
landing in the right bundle, plus installing bundles like packages.**

Risks:
- The difference is in the approach, so it's easy to copy.
- Claude Code may add cross-machine memory itself. That would take the sync part, but not
  bundles or sharing, which is why those are the pitch.
- Sharing only works if good bundles exist, so the first ones have to come from us.

## 13. Scope

**v0.1:** Python, stdlib only (git via subprocess), installed with `uv tool install`.
- Claude Code adapter via `init`-installed hooks.
- Commands `init`, `use`, `new`, `status`, `sync`.
- The lifecycle in §7, the index budget in §8, the simple conflict rule in §9.
- Linux and Windows, used daily on two machines before anyone else sees it.

**v0.2:** sharing (`add`, `update`, `publish` with the scan), `bundle.toml`, OKF check, a
Claude Code plugin package.

**Later:** more adapters (Codex; Diwan, whose memory can be designed around bundles from the
start; an MCP server for agents with no memory folder), an LLM merge, a bundle index page.

## 14. What v0.1 does today (2026-09-27)

Built: `init`, `use`, `new`, `import`, `status`, `sync`, `hooks [--remove]`, plus the
hidden `hook start|end` run by Claude Code. 15 tests (two simulated machines sharing a bare
remote, conflicts, offline, index budget, hook install/remove, link safety).

Verified with real Claude Code sessions (2.1.283, Linux), driven only through `tp`:
- a memory in `personal/` and one in the project bundle were both read through the links,
  with `~/` permission rules written by `tp use`;
- asked to remember one fact about the user and one about the project, Claude saved each
  into the right bundle folder; the end hook committed and pushed it in the background.

Where the build differs from the plan above:
- **A project with no bundles is left alone.** No hint, no default `personal`: printing a
  hint into every unbound project's context would be noise, and switching every project to
  `personal` would hide Claude Code's own per-project memory. `tp status` says what's going on.
- **Bundle indexes keep Claude's own lines.** Files decide what's listed; Claude's titles
  and descriptions are kept, and only files without a line get one generated.
- **Hooks go in the user settings via `tp init`** (a backup is kept); the plugin package
  is v0.2.
- `tp import` exists so existing memory folders can become bundles; sources stay untouched.
- **Start-hook announcements are neutral:** anything added or changed since the last
  index build is announced, including edits made outside a session.

### Changed after first use: each machine chooses its own bundles (2026-09-27)

The first version kept one choice per project, shared by every machine. In practice a
machine can need its own bundle (e.g. a `windows` bundle with Windows-only knowledge), so
`projects.toml` now keeps one choice **per machine, per project**:

```toml
[machines."linux"."github.com/me/my-project"]
bundles = ["personal", "my-project"]
write = "my-project"
personal = "personal"
```

- Machine names are saved at `tp init` (`--machine NAME`, default: hostname). Renaming with
  `tp init --machine NEW` carries the choices over. `init` warns when the name is already
  taken in the store: two machines with the same hostname would otherwise share choices.
- On a machine with no choice for a project, nothing is loaded. The first session there says
  once what the other machines load and suggests the `tp use` command.
- Bundle descriptions (`tp new NAME --description ...`) go into the index header, so the
  agent knows what each folder is for when it saves a new memory.
- Choices in the first format are read as the current machine's and rewritten on save.

## 15. Open questions

1. Windows: do junctions plus the permission rules behave as they did on Linux?
2. Two sessions in two projects that share `personal`: both write into the same bundle.
   Regenerating indexes from files should make this safe, but it needs a test.
3. Does Claude reliably write into the right bundle subfolder across models, or only
   sometimes? Stray-file sorting is the safety net. Measure it.
4. Hooks installed by `init` vs a plugin: which one first? The plugin is the better
   distribution channel, but it needs enabling.
5. What an installed bundle's update should look like in-session (announce it at start, like
   memories from another machine?).
