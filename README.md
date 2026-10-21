# claude-mods

A Claude Code plugin marketplace of mods (hooks-module plugins).

| Mod | What it does |
| --- | --- |
| [early-compact](plugins/early-compact) | Compacts the conversation while a slow backgrounded command runs, ahead of the auto-compact threshold. |

## Install

At the prompt of a terminal session:

```
/plugin install early-compact --marketplace dreness/claude-mods
```

Answer `y` to add the marketplace, then pick a scope. From a shell:

```
claude plugin install early-compact --marketplace dreness/claude-mods
```

For another mod, replace `early-compact` with its name from the table above.

In the desktop app: click + next to the prompt box, then Plugins, Add marketplace with `dreness/claude-mods`, then Add plugin.

While this repository is private, installing needs read access on GitHub and a git credential for github.com on your machine (an SSH key, or `gh auth login` then `gh auth setup-git`).

## Layout

Each mod lives in `plugins/<mod>/` and is listed in `.claude-plugin/marketplace.json`.

## License

MIT
