# early-compact

A Claude Code mod that compacts the conversation while a slow background command runs, so compaction uses time you would spend waiting anyway instead of interrupting work later when auto-compact kicks in.

## How it works

1. The mod registers a tool, `compact_while_waiting`. Its description tells the model to run any command that may take 15s or more with `run_in_background: true`, even one it would otherwise run in the foreground, then call the tool, passing instructions for the summary (task id, what the command does, what to do with its output).
2. Once context reaches the threshold, the next tool result carries a reminder telling the model to do this until the conversation is compacted, including tests and builds it would normally run in the foreground. A foreground Bash call that then takes 15s or more gets a second reminder after its result. The first reminder is sent again only after context drops below the threshold and crosses it again.
3. If context is at or above the threshold, the tool schedules a compaction and tells the model to end its turn. Below the threshold it answers "not needed" and does nothing.
4. When the main agent's turn ends normally, the mod calls `compact()` with those instructions. The task's completion notice then resumes the model after compaction.

Details:

- `compact()` refuses while a turn is running. The mod retries 8 times at 250ms intervals, then waits for one more turn end and tries again. After that it gives up with a toast.
- A turn that ends aborted or with an error drops the schedule: you are back at the keyboard, so the conversation is left alone.
- Subagents can't use the tool, get no reminders, and their turn ends don't trigger the compaction.
- A later call replaces the instructions from an earlier one.
- If a background task finishes before the turn ends, its notice starts a new turn right away. Compaction then happens when that turn ends instead.
- A status line entry shows while compacting. A toast reports when compaction is done, skipped or abandoned.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `threshold` | 60 | Context fullness (%) at which the tool schedules a compaction. |
| `nudge` | true | Shows a toast when a foreground Bash call takes 15s or more above the threshold, i.e. a missed chance. The model is reminded either way. |

## Install

At the prompt of a terminal session:

```
/plugin install early-compact --marketplace dreness/claude-mods
```

Answer `y` to add the marketplace, pick a scope, then set the options. From a shell: `claude plugin install early-compact --marketplace dreness/claude-mods`.

To run from a checkout instead: `claude --plugin-dir /path/to/plugins/early-compact`.

## Development

```
claude plugin test .                      # unit tests (tests/register.test.ts)
claude plugin validate .
npx -y -p typescript@5.6.3 tsc -p .       # needs .claude-plugin/types, laid when the engine loads the mod
```
