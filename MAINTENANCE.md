# Maintenance

Scheduled jobs for this repo, run by upkeep.

```upkeep-manifest
version: 1
```

## Refresh the early-compact usage summary

`plugins/early-compact/scripts/usage-report.py` measures how early compactions turned out across the Claude Code transcripts in `~/.claude/projects`, and `usage-graphic.py` draws the headline numbers as the SVGs in the plugin README. Claude Code keeps transcripts for about 30 days, so the report merges them with the records stored in `plugins/early-compact/data/*.csv` and `--save` writes the merged records back. This task re-runs both over everything since 0.3.0 (2026-10-24), updates the README's date range to match, and fast-forwards `main` with the result. It must run at least every 30 days or records are lost.

The input lives outside the repo, so the task runs even when `main` has not moved.

```upkeep-task
id: early-compact-usage
title: Refresh the early-compact usage summary graphic
runner: script
workdir: plugins/early-compact
run:
  - python3 scripts/usage-report.py --save --since 2026-10-24 --summary "${TMPDIR:-/tmp}/early-compact-summary.json" > /dev/null
  - python3 scripts/usage-graphic.py "${TMPDIR:-/tmp}/early-compact-summary.json"
  - |
    python3 - "${TMPDIR:-/tmp}/early-compact-summary.json" <<'EOF'
    import json, re, sys
    d = json.load(open(sys.argv[1]))
    span = f"from {d['first'][:10]} to {d['last'][:10]}"
    text = open('README.md').read()
    new, n = re.subn(r"^Numbers from one user's transcripts [^;]*;",
                     f"Numbers from one user's transcripts {span}, since 0.3.0;",
                     text, count=1, flags=re.M)
    if n != 1:
        sys.exit('README summary sentence not found')
    open('README.md', 'w').write(new)
    EOF
writes:
  - plugins/early-compact/data/*.csv
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
