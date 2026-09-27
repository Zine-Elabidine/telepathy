# Telepathy

Your coding agent's memory, on every machine.

Claude Code remembers what you teach it, but only on the machine where you taught it,
and only inside the project folder where it happened. Telepathy turns memory into
**bundles** you choose per project:

```
tp init                          # once per machine: connect your private memory repo
tp use personal my-project       # per project, per machine: pick the bundles it loads
tp add github:someone/grpo-notes # install someone else's knowledge
```

Then just open Claude Code. It pulls the latest memories from your other machines and
loads the bundles you chose as one memory. New memories go back into the right bundle and
get pushed when the session ends.

- **Bundles, not one blob.** Your personal rules, a project's memory and a shared knowledge
  bundle, combined in one session.
- **Every machine.** Linux, macOS and Windows. Git moves the memories; no server to run.
- **Shareable.** A bundle is a folder of markdown in a git repo. Install one like a package.
- **Native.** Claude Code still sees ordinary auto memory: always loaded, plain files.

> Status: **v0.1 in progress.** Works on Linux with Claude Code (tested on 2.1.283).
> Windows is written but not yet tested. Sharing (`tp add`, `tp publish`) comes in v0.2.
> Design: [docs/design.md](docs/design.md).

## Try it (from source)

```
uv tool install git+https://github.com/Zine-Elabidine/telepathy   # gives `tp`
tp init git@github.com:you/my-memories.git   # an empty private repo, or omit for local-only
cd ~/code/my-project
tp import ~/.claude/projects/<this-project>/memory my-project   # optional: bring existing memories
tp use personal my-project
tp status
```

Start a new Claude Code session in that project. `tp hooks --remove` undoes the hooks
(`tp init` also keeps a copy of your settings as `settings.json.before-telepathy`).

## License

MIT
