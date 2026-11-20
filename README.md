# claude-mods

A Claude Code plugin marketplace of mods: plugins that change how Claude Code behaves or add agents to it.

| Mod | What it does |
| --- | --- |
| <img src="plugins/early-compact/.claude-plugin/icon.png" alt="" width="20"> [early-compact](plugins/early-compact) | Compacts the conversation while a backgrounded command runs, ahead of the auto-compact threshold. |
| [macos-reverser](plugins/macos-reverser) | An agent that reverse-engineers macOS binaries and apps with Hopper Disassembler and the command-line tools. |

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

## Layout

Each mod lives in `plugins/<mod>/`, with its own README, and is listed in `.claude-plugin/marketplace.json`. `MAINTENANCE.md` declares scheduled maintenance jobs, such as the weekly refresh of early-compact's usage numbers.

Bug reports and suggestions are welcome as GitHub issues.

## License

MIT, see [LICENSE](LICENSE).
