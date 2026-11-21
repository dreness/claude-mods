# Maintenance

Scheduled jobs for this repo, run by upkeep.

```upkeep-manifest
version: 1
```

## Refresh the early-compact usage summary

`plugins/early-compact/scripts/usage-report.py` measures how early compactions turned out across the Claude Code transcripts in `~/.claude/projects`, and `usage-graphic.py` draws the headline numbers as the SVGs in the plugin README. Claude Code keeps transcripts for about 30 days, so the report merges them with the records stored as CSV in `$EARLY_COMPACT_DATA` (default `~/.local/share/early-compact`, outside the repo; never commit them) and `--save` writes the merged records back. This task runs `scripts/refresh-usage.py`, which re-runs both over everything since 0.3.0 (2026-10-24) and updates the README's date range to match, then fast-forwards `main` with the result. Host-specific settings go in `plugins/early-compact/.env` (see `.env.example`). It must run at least every 30 days or records are lost.

The input lives outside the repo, so the task runs even when `main` has not moved.

```upkeep-task
id: early-compact-usage
title: Refresh the early-compact usage summary graphic
runner: script
workdir: plugins/early-compact
run:
  - python3 scripts/refresh-usage.py
writes:
  - plugins/early-compact/docs/usage-light.svg
  - plugins/early-compact/docs/usage-dark.svg
  - plugins/early-compact/README.md
verify:
  - python3 -c "import sys, xml.dom.minidom as m; [m.parse(p) for p in sys.argv[1:]]" docs/usage-light.svg docs/usage-dark.svg
deliver: merge
rerun_unchanged: true
timeout: 5m
suggest:
  cadence: weekly
  cost: small
```
