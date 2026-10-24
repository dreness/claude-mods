#!/usr/bin/env python3
"""Report on early-compact usage from Claude Code transcripts.

Scans ~/.claude/projects/**/*.jsonl (or --root) for scheduled early compactions
(compact_while_waiting calls up to 0.2.x, scheduled notes on background Bash
results from 0.3.0) and compaction boundaries, classifies each, and estimates
time saved.

Time model, per early compaction:
  hidden   = overlap of the compaction with the background task it waited on
  exposed  = compaction time outside that overlap (someone waited on it)
  equiv    = fraction of an auto-compaction cycle it replaces:
             (pre - post) / (auto_pre - auto_post), medians over auto-compactions
  net      = equiv * median auto-compaction duration - exposed
"""

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

TOOL = 'mcp__early-compact__compact_while_waiting'
BG_RE = re.compile(r'running in background with ID: (\w+)')
TASK_RE = re.compile(r'<task-id>(\w+)</task-id>')
SCHEDULED_RE = re.compile(r'early-compact: compaction scheduled while background task (\w+) runs \(context (\d+)% full\)')
# Turn-end outcomes that mean no compaction ran; "compacted while waiting" is read from the boundary.
NOTICE_RE = re.compile(r'early-compact: (skipped \([^)]*\)|dropped \([^)]*\)|gave up.*)')
# Neighbouring compactions count as a double when either limit holds.
DOUBLE_S = 180
DOUBLE_TOKENS = 30_000
# Calls scheduled below this ran with a lowered threshold, i.e. testing.
DEFAULT_THRESHOLD = 60


def ts(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp() if s else None


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(x.get('text', '') if isinstance(x.get('text'), str) else
                         text_of(x.get('content')) for x in content if isinstance(x, dict))
    return ''


def is_human_prompt(d):
    if d.get('type') != 'user' or d.get('isMeta') or d.get('isCompactSummary'):
        return False
    if d.get('turnOrigin') == 'task_notification' or d.get('promptSource') == 'system':
        return False
    c = (d.get('message') or {}).get('content')
    if isinstance(c, list) and any(isinstance(x, dict) and x.get('type') == 'tool_result' for x in c):
        return False
    t = text_of(c).lstrip()
    return bool(t) and not t.startswith(('<task-notification>', '<local-command', '<command-',
                                         'This session is being continued', '[Request interrupted'))


def load(path):
    rows = []
    with open(path, errors='replace') as f:
        for line in f:
            if '"api-request' in line[:40]:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def analyze_file(path, since=0.0):
    """Returns (invocations, boundaries, signals) for one transcript; signals count from `since`."""
    rows = load(path)
    events = []  # (time, kind, data) in file order
    bg_start, task_end = {}, {}
    pending_uses = {}
    signals = Counter()
    for d in rows:
        t = ts(d.get('timestamp'))
        typ = d.get('type')
        if typ == 'queue-operation' and d.get('operation') == 'enqueue':
            for tid in TASK_RE.findall(d.get('content') or ''):
                task_end.setdefault(tid, t)
            continue
        if typ == 'system' and d.get('subtype') == 'compact_boundary':
            events.append((t, 'boundary', d))
            continue
        if typ == 'system' and d.get('subtype') == 'turn_duration':
            events.append((t, 'turn_end', d))
            continue
        if typ == 'attachment':
            a = d.get('attachment') or {}
            if a.get('type') == 'hook_additional_context' and (t or 0) >= since:
                body = ' '.join(a.get('content') or [])
                if 'near auto-compaction' in body:
                    signals['reminders'] += 1
                    events.append((t, 'reminder', d))
                if 'in the foreground with context above' in body:
                    signals['missed'] += 1
                m = SCHEDULED_RE.search(body)
                if m:
                    events.append((t, 'invoke', {'id': a.get('toolUseID') or d.get('uuid'), 'called': t,
                                                 'result': 'scheduled', 'instructions': m.group(1),
                                                 'percent': int(m.group(2))}))
            continue
        if typ == 'system':
            m = NOTICE_RE.search(text_of(d.get('content')) or text_of((d.get('message') or {}).get('content')))
            if m:
                events.append((t, 'notice', m.group(1)))
            continue
        if typ not in ('user', 'assistant'):
            continue
        c = (d.get('message') or {}).get('content')
        if typ == 'user':
            txt = text_of(c)
            for tid in TASK_RE.findall(txt):
                if '<task-notification>' in txt:
                    task_end.setdefault(tid, t)
            if isinstance(c, str) and '<command-name>/compact</command-name>' in c:
                events.append((t, 'slash_compact', d))
            if '[Request interrupted' in txt:
                events.append((t, 'interrupt', d))
            if is_human_prompt(d):
                events.append((t, 'prompt', d))
        if not isinstance(c, list):
            continue
        for x in c:
            if not isinstance(x, dict):
                continue
            if x.get('type') == 'tool_use' and x.get('name') == TOOL:
                pending_uses[x['id']] = (t, (x.get('input') or {}).get('instructions', ''))
            elif x.get('type') == 'tool_result':
                body = text_of(x.get('content'))
                for tid in BG_RE.findall(body):
                    bg_start.setdefault(tid, t)
                    events.append((t, 'bg_start', tid))
                if x.get('tool_use_id') in pending_uses:
                    t0, instr = pending_uses.pop(x['tool_use_id'])
                    events.append((t, 'invoke', {'id': x['tool_use_id'], 'called': t0,
                                                 'result': body, 'instructions': instr}))

    sidechain = '/subagents/' in str(path)
    invocations = []
    boundaries = [e[2] for e in events if e[1] == 'boundary']
    for i, (t, kind, data) in enumerate(events):
        if kind != 'invoke':
            continue
        r = data['result']
        pct = re.search(r'Context is (\d+)%', r)
        inv = {'id': data['id'], 'file': str(path), 'time': t,
               'percent': data.get('percent', int(pct.group(1)) if pct else None), 'sidechain': sidechain}
        if 'not needed' in r:
            inv['outcome'] = 'not_needed'
        elif 'subagents' in r:
            inv['outcome'] = 'subagent'
        elif 'failed' in r:
            inv['outcome'] = 'error'
        elif 'scheduled' not in r:
            inv['outcome'] = 'unknown'
        else:
            inv.update(follow_scheduled(events, i, data, bg_start, task_end))
        invocations.append(inv)
    return invocations, boundaries, signals


def follow_scheduled(events, i, data, bg_start, task_end):
    """Finds what happened after a scheduled compaction."""
    t_inv = events[i][0]
    # Task the compaction waits on: named in the instructions, else latest launched before the call.
    started = [(t, tid) for t, k, tid in events[:i] if k == 'bg_start']
    named = [tid for _, tid in started if tid in data['instructions']]
    task = named[-1] if named else (started[-1][1] if started else None)
    out = {'task': task, 'task_start': bg_start.get(task), 'task_end': task_end.get(task)}
    if out['task_end'] is not None and out['task_end'] <= data['called']:
        out['task_done_first'] = True
    turns = 0
    for t, kind, d in events[i + 1:]:
        if kind == 'invoke':
            # A later call replaces this one's instructions; it owns the compaction.
            out['outcome'] = 'superseded'
            return out
        if kind == 'turn_end':
            turns += 1
        elif kind == 'notice':
            out['outcome'] = 'not_compacted'
            out['why'] = d
            return out
        elif kind == 'interrupt':
            out['outcome'] = 'not_compacted'
            out['why'] = 'turn interrupted'
            return out
        elif kind == 'slash_compact':
            out['outcome'] = 'not_compacted'
            out['why'] = 'user ran /compact first'
            return out
        elif kind == 'prompt':
            out['outcome'] = 'not_compacted'
            out['why'] = 'no compaction before next user prompt'
            return out
        elif kind == 'boundary':
            m = d.get('compactMetadata') or {}
            end = t
            start = end - (m.get('durationMs') or 0) / 1000
            out.update(boundary=d.get('uuid'), trigger=m.get('trigger'), c_start=start, c_end=end,
                       duration=(m.get('durationMs') or 0) / 1000, pre=m.get('preTokens'),
                       post=m.get('postTokens'), turns_waited=turns)
            if m.get('trigger') != 'manual':
                out['outcome'] = 'not_compacted'
                out['why'] = f"preempted by {m.get('trigger')} compaction"
                return out
            out['outcome'] = 'compacted'
            ts_, te = out['task_start'], out['task_end']
            if ts_ is not None and te is not None:
                out['hidden'] = max(0.0, min(end, te) - max(start, ts_))
            elif ts_ is not None:
                out['hidden'] = max(0.0, end - max(start, ts_))
            else:
                out['hidden'] = 0.0
            out['exposed'] = out['duration'] - out['hidden']
            late = []
            if te is not None and te <= start:
                late.append('task finished before compaction started')
            if turns > 1:
                late.append(f'ran {turns} turn ends after scheduling')
            out['late'] = late
            return out
    out['outcome'] = 'not_compacted'
    out['why'] = 'no compaction before transcript ends'
    return out


def scan_baseline(root):
    """All compaction boundaries everywhere, plus which sessions had the mod loaded."""
    boundaries = {}
    mod_files = set()
    for path in root.rglob('*.jsonl'):
        if '/subagents/' in str(path):
            continue
        with open(path, errors='replace') as f:
            for line in f:
                if '"compact_boundary"' in line and '"api-request' not in line[:40]:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get('subtype') == 'compact_boundary':
                        d['_file'] = str(path)
                        boundaries.setdefault(d.get('uuid'), d)
                elif '"early-compact"' in line and 'mcp_instructions_delta' in line:
                    mod_files.add(str(path))
    return list(boundaries.values()), mod_files


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def fmt_s(x):
    if x is None:
        return '-'
    return f'{x / 60:.1f} min' if abs(x) >= 120 else f'{x:.0f}s'


def iso(t):
    return datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M') if t else '-'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', type=Path, default=Path.home() / '.claude' / 'projects')
    ap.add_argument('--json', type=Path, help='also write per-invocation records here')
    ap.add_argument('--since', metavar='YYYY-MM-DD',
                    help='ignore calls and compactions before this local date')
    args = ap.parse_args()
    since = datetime.strptime(args.since, '%Y-%m-%d').timestamp() if args.since else 0.0

    files = [p for p in args.root.rglob('*.jsonl') if TOOL in p.read_text(errors='replace')]
    invocations, signals = {}, Counter()
    for path in files:
        invs, _, sig = analyze_file(path, since)
        signals += sig
        for inv in invs:
            # Resumed or forked sessions copy history; keep the copy that saw the outcome.
            prev = invocations.get(inv['id'])
            if prev is None or (prev.get('why', '').endswith('transcript ends') and inv['outcome'] != prev['outcome']):
                invocations[inv['id']] = inv
    invs = sorted((i for i in invocations.values() if (i['time'] or 0) >= since), key=lambda x: x['time'] or 0)

    all_bounds, mod_files = scan_baseline(args.root)
    for b in all_bounds:
        b['_t'] = ts(b.get('timestamp'))
    all_bounds = [b for b in all_bounds if (b['_t'] or 0) >= since]
    ours = {i['boundary'] for i in invs if i.get('outcome') == 'compacted'}
    by_file = defaultdict(list)
    for b in all_bounds:
        by_file[b['_file']].append(b)
    for bs in by_file.values():
        bs.sort(key=lambda b: b['_t'] or 0)

    # Double compactions: ours plus a neighbour soon before/after, little context growth between.
    doubles = []
    for inv in invs:
        if inv.get('outcome') != 'compacted':
            continue
        for bs in by_file.values():
            idx = next((k for k, b in enumerate(bs) if b.get('uuid') == inv['boundary']), None)
            if idx is None:
                continue
            for j in (idx - 1, idx + 1):
                if 0 <= j < len(bs):
                    a, b = sorted((bs[idx], bs[j]), key=lambda b: b['_t'])
                    gap = b['_t'] - a['_t']
                    growth = (b['compactMetadata'].get('preTokens') or 0) - (a['compactMetadata'].get('postTokens') or 0)
                    if gap <= DOUBLE_S or growth <= DOUBLE_TOKENS:
                        other = bs[j]
                        doubles.append((inv, other, gap, growth))
                        inv.setdefault('double', []).append(
                            f"{other['compactMetadata'].get('trigger')} compaction {fmt_s(gap)} "
                            f"{'before' if j < idx else 'after'}, {growth:,} tokens of growth between")
            break

    out = []
    p = out.append
    first = min((i['time'] for i in invs if i['time']), default=None)
    p('# early-compact usage report\n')
    p(f'Transcripts scanned: {sum(1 for _ in args.root.rglob("*.jsonl"))} under `{args.root}`; '
      f'{len(files)} mention the tool; {len(mod_files)} sessions had the mod loaded.')
    if args.since:
        p(f'Ignoring calls and compactions before {args.since}.')
    p(f'First invocation: {iso(first)}. Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}.\n')

    oc = Counter(i['outcome'] for i in invs)
    sched = [i for i in invs if i['outcome'] not in ('not_needed', 'subagent', 'error', 'unknown')]
    comp = [i for i in invs if i['outcome'] == 'compacted']
    late = [i for i in comp if i.get('late')]
    p('## Invocations\n')
    p('| Outcome | Count |\n| --- | --- |')
    p(f'| Total calls | {len(invs)} |')
    p(f'| Below threshold ("not needed") | {oc["not_needed"]} |')
    p(f'| Scheduled a compaction | {len(sched)} |')
    p(f'| - compacted | {len(comp)} |')
    p(f'| - of which late | {len(late)} |')
    p(f'| - of which double-compacted | {sum(1 for i in comp if i.get("double"))} |')
    p(f'| - superseded by a later call | {oc["superseded"]} |')
    p(f'| - did not compact | {oc["not_compacted"]} |')
    for k in ('subagent', 'error', 'unknown'):
        if oc[k]:
            p(f'| {k} | {oc[k]} |')
    p('')
    why = Counter(i['why'] for i in invs if i['outcome'] == 'not_compacted')
    if why:
        p('Did not compact, by reason:\n')
        for k, v in why.most_common():
            p(f'- {k}: {v}')
        p('')
    nn = [i['percent'] for i in invs if i['outcome'] == 'not_needed' and i['percent'] is not None]
    if nn:
        p(f'"Not needed" calls ran at a median {med(nn):.0f}% context (range {min(nn)}-{max(nn)}%).')
    sp = [i['percent'] for i in sched if i['percent'] is not None]
    if sp:
        p(f'Scheduled calls ran at a median {med(sp):.0f}% context (range {min(sp)}-{max(sp)}%).')
    p(f'Reminders injected (crossed threshold): {signals["reminders"]}; '
      f'missed-chance notes (15s+ foreground Bash above threshold): {signals["missed"]}.\n')

    # Baseline: compactions in sessions with the mod vs everything.
    autos = [b for b in all_bounds if b['compactMetadata'].get('trigger') == 'auto']
    manual_other = [b for b in all_bounds if b['compactMetadata'].get('trigger') == 'manual' and b.get('uuid') not in ours]
    autos_mod = [b for b in autos if b['_file'] in mod_files]
    since = [b for b in autos if first and b['_t'] and b['_t'] >= first]
    dur = lambda bs: med([(b['compactMetadata'].get('durationMs') or 0) / 1000 for b in bs])
    p('## Compactions\n')
    p('| Kind | Count | Median duration | Median pre-tokens | Median post-tokens |\n| --- | --- | --- | --- | --- |')
    def row(name, bs):
        m = [b['compactMetadata'] for b in bs]
        pre, post = med([x.get('preTokens') for x in m]), med([x.get('postTokens') for x in m])
        p(f'| {name} | {len(bs)} | {fmt_s(dur(bs))} | {f"{pre:,.0f}" if pre else "-"} | {f"{post:,.0f}" if post else "-"} |')
    row('early-compact', [b for b in all_bounds if b.get('uuid') in ours])
    span = f'since {args.since}' if args.since else 'all time'
    row(f'auto, {span}', autos)
    row('auto, since first invocation', since)
    row('auto, in sessions with mod loaded', autos_mod)
    row(f'other manual (/compact etc.), {span}', manual_other)
    p('')

    p('## Time\n')
    a_pre = med([b['compactMetadata'].get('preTokens') for b in autos])
    a_post = med([b['compactMetadata'].get('postTokens') for b in autos])
    a_dur = dur(autos)
    p(f'Auto-compaction baseline: median {fmt_s(a_dur)} at {a_pre:,.0f} pre-tokens.\n' if a_dur else '')
    real = [i for i in comp if (i['percent'] or 0) >= DEFAULT_THRESHOLD]
    for label, group in (('All early compactions', comp),
                         (f'Excluding test runs (scheduled below {DEFAULT_THRESHOLD}%)', real)):
        p(f'**{label}** ({len(group)}):\n')
        hidden = sum(i['hidden'] for i in group)
        exposed = sum(i['exposed'] for i in group)
        total = sum(i['duration'] for i in group)
        if not total:
            p('- None.\n')
            continue
        p(f'- Took {fmt_s(total)} in total; {fmt_s(hidden)} ({hidden / total:.0%}) overlapped '
          f'the background task they waited on, {fmt_s(exposed)} did not.')
        p(f'- Fully hidden: {sum(1 for i in group if i["exposed"] < 1)}; '
          f'under half hidden: {sum(1 for i in group if i["hidden"] < i["duration"] / 2)}.')
        if a_pre and a_dur:
            equiv = [max(0.0, (i['pre'] - i['post']) / (a_pre - a_post))
                     for i in group if i.get('pre') and i.get('post') is not None]
            replaced = sum(equiv) * a_dur
            p(f'- Each replaces a median {med(equiv):.2f} of an auto-compaction cycle (compacting earlier '
              f'frees less context); together {sum(equiv):.1f} auto-compactions, ~{fmt_s(replaced)} of blocking time.')
            p(f'- Estimated net time saved: {fmt_s(replaced)} replaced - {fmt_s(exposed)} still waited on = '
              f'**{fmt_s(replaced - exposed)}** ({fmt_s((replaced - exposed) / len(group))} per compaction).')
        p('')
    p('Assumes the backgrounded command would otherwise have run in the foreground for the same time, '
      'and ignores any value of avoiding a mid-task auto-compaction.\n')
    done_first = [i for i in sched if i.get('task_done_first')]
    if done_first:
        p(f'Calls made after their background task had already finished (nothing left to overlap): '
          f'{len(done_first)} of {len(sched)} scheduled.\n')

    if late:
        p('## Late compactions\n')
        for i in late:
            p(f'- {iso(i["time"])} {Path(i["file"]).parent.name}: {"; ".join(i["late"])} '
              f'(task {i.get("task")}, hidden {fmt_s(i["hidden"])} of {fmt_s(i["duration"])})')
        p('')
    if doubles:
        p(f'## Double compactions\n\nPairs within {fmt_s(DOUBLE_S)} or {DOUBLE_TOKENS:,} tokens of growth.\n')
        seen = set()
        for inv, other, gap, growth in doubles:
            pair = frozenset((inv['boundary'], other.get('uuid')))
            if pair in seen:
                continue
            seen.add(pair)
            p(f'- {iso(inv["time"])} {Path(inv["file"]).parent.name}: early-compact and '
              f'{other["compactMetadata"].get("trigger")} compaction {fmt_s(gap)} apart, '
              f'{growth:,} tokens of growth between')
        p('')

    p('## Scheduled calls\n')
    p('| When | Project | Ctx | Outcome | Task bg time | Compaction | Hidden | Notes |\n| --- | --- | --- | --- | --- | --- | --- | --- |')
    for i in sched:
        bg = (i['task_end'] - i['task_start']) if i.get('task_end') and i.get('task_start') else None
        notes = '; '.join((['task done before call'] if i.get('task_done_first') else []) +
                          (['test threshold'] if (i['percent'] or 0) < DEFAULT_THRESHOLD else []) +
                          (i.get('late') or []) + (i.get('double') or []) + ([i['why']] if i.get('why') else []))
        p(f'| {iso(i["time"])} | {Path(i["file"]).parent.name.removeprefix("-Users-andre-")} | {i["percent"]}% | '
          f'{i["outcome"]} | {fmt_s(bg)} | {fmt_s(i.get("duration"))} | {fmt_s(i.get("hidden"))} | {notes} |')
    print('\n'.join(out))

    if args.json:
        args.json.write_text(json.dumps(invs, indent=1, default=str))


if __name__ == '__main__':
    sys.exit(main())
