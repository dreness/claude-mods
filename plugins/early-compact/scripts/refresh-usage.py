#!/usr/bin/env python3
"""Refresh the stored usage records, the usage SVGs and the README's date range.

Runs usage-report.py --save over everything since 0.3.0, draws the graphic with
usage-graphic.py, then rewrites the README sentence that names the date range.

Configuration comes from the environment, or from plugins/early-compact/.env
(see .env.example); the environment wins. In a linked git worktree, where the
ignored .env is absent, the main checkout's .env is read instead.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN / 'scripts'
# 0.3.0 release; earlier records came from the 0.2.x MCP tool.
SINCE = '2026-10-24'
SENTENCE = re.compile(r"^Numbers from one user's transcripts [^;]*;", re.M)


def env_files():
    yield PLUGIN / '.env'
    p = subprocess.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir', '--show-toplevel'],
                       cwd=PLUGIN, capture_output=True, text=True)
    if p.returncode == 0:
        common, top = p.stdout.split('\n')[:2]
        main = Path(common).parent
        if main != Path(top):
            yield main / PLUGIN.relative_to(top) / '.env'


def load_env():
    """KEY=VALUE lines from the first .env found; # comments, optional quotes."""
    for path in env_files():
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = (s.strip() for s in line.removeprefix('export ').split('=', 1))
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                value = value[1:-1]
            os.environ.setdefault(key, value)
        return


def run(*args, **kw):
    subprocess.run([sys.executable, *map(str, args)], check=True, **kw)


def main():
    load_env()
    report = [SCRIPTS / 'usage-report.py', '--save', '--since', SINCE]
    if os.environ.get('EARLY_COMPACT_TRANSCRIPTS'):
        report += ['--root', Path(os.environ['EARLY_COMPACT_TRANSCRIPTS']).expanduser()]
    with tempfile.TemporaryDirectory() as tmp:
        summary = Path(tmp) / 'summary.json'
        run(*report, '--summary', summary, stdout=subprocess.DEVNULL)
        run(SCRIPTS / 'usage-graphic.py', summary)
        d = json.loads(summary.read_text())
    readme = PLUGIN / 'README.md'
    span = f"from {d['first'][:10]} to {d['last'][:10]}"
    text, n = SENTENCE.subn(f"Numbers from one user's transcripts {span}, since 0.3.0;", readme.read_text(), count=1)
    if n != 1:
        sys.exit('README summary sentence not found')
    readme.write_text(text)


if __name__ == '__main__':
    main()
