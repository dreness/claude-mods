#!/usr/bin/env python3
"""Draw the usage report's headline numbers as an SVG for the README.

Input is the --summary file from usage-report.py. Writes a light and a dark
variant; the README picks one with <picture>.
"""
import argparse
import json
from pathlib import Path

FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif'
W = 920

THEMES = {
    'light': dict(surface='#fcfcfb', panel='#f3f2ee', text='#0b0b0b', muted='#52514e', faint='#8a8884',
                  grid='#e4e2dc', early='#2a78d6', auto='#eb6834', manual='#1baf7a', other='#c9c7bf'),
    'dark': dict(surface='#1a1a19', panel='#242422', text='#f0efec', muted='#c3c2b7', faint='#8f8e86',
                 grid='#343431', early='#3987e5', auto='#d95926', manual='#199e70', other='#4e4d48'),
}


def esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def fmt_min(sec):
    return f'{sec / 60:.0f} min' if sec >= 90 else f'{sec:.0f}s'


def k(n):
    return f'{n / 1000:.0f}k'


class Svg:
    def __init__(self, t):
        self.t = t
        self.parts = []

    def text(self, x, y, s, size=13, fill=None, weight=400, anchor='start', tabular=False, opacity=1):
        style = f'font-size:{size}px;font-weight:{weight};fill:{fill or self.t["text"]}'
        if tabular:
            style += ';font-variant-numeric:tabular-nums'
        if opacity != 1:
            style += f';opacity:{opacity}'
        self.parts.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" style="{style}">{esc(s)}</text>')

    def rect(self, x, y, w, h, fill, r=0):
        self.parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r}" fill="{fill}"/>')

    def line(self, x1, y1, x2, y2, stroke):
        self.parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="1"/>')

    # Horizontal bar: square at the baseline (left), 4px rounded data-end (right).
    def hbar(self, x, y, w, h, fill):
        r = min(4, w / 2, h / 2)
        self.parts.append(
            f'<path d="M{x:.1f},{y:.1f} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{h - 2 * r:.1f} '
            f'a{r},{r} 0 0 1 {-r},{r} h{-(w - r):.1f} z" fill="{fill}"/>')

    # Vertical column: square at the baseline (bottom), 4px rounded cap.
    def vbar(self, x, y, w, h, fill, cap=True):
        r = min(4, w / 2, h / 2) if cap else 0
        self.parts.append(
            f'<path d="M{x:.1f},{y + h:.1f} v{-(h - r):.1f} a{r},{r} 0 0 1 {r},{-r} h{w - 2 * r:.1f} '
            f'a{r},{r} 0 0 1 {r},{r} v{h - r:.1f} z" fill="{fill}"/>')

    def render(self, h):
        body = '\n'.join(self.parts)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" '
                f'font-family=\'{FONT}\' role="img" aria-label="early-compact usage summary">\n'
                f'<rect width="{W}" height="{h}" fill="{self.t["surface"]}" rx="8"/>\n{body}\n</svg>\n')


def tile(s, x, y, w, value, label, note):
    t = s.t
    s.rect(x, y, w, 96, t['panel'], r=6)
    s.text(x + 14, y + 40, value, size=26, weight=600, tabular=True)
    s.text(x + 14, y + 62, label, size=12, fill=t['muted'])
    s.text(x + 14, y + 80, note, size=11, fill=t['faint'])


def by_day(s, d, x, y, w, h):
    t = s.t
    days = d['by_day']
    s.text(x, y, 'Compactions per day, mod sessions', size=13, weight=600)
    series = (('early', 'early', t['early']), ('auto', 'auto', t['auto']), ('manual', 'manual', t['manual']))
    lx = x
    for key, name, col in series:
        total = sum(c.get(key, 0) for c in days.values())
        s.rect(lx, y + 12, 10, 10, col, r=2)
        s.text(lx + 15, y + 21, f'{name} {total}', size=11, fill=t['muted'])
        lx += 15 + 6.5 * len(f'{name} {total}') + 14
    top, bottom = y + 36, y + h - 22
    peak = max(sum(c.values()) for c in days.values())
    step = 5 if peak > 12 else 2
    ticks = list(range(0, peak + step, step))
    scale = (bottom - top) / ticks[-1]
    for v in ticks:
        yy = bottom - v * scale
        s.line(x + 22, yy, x + w, yy, t['grid'])
        s.text(x + 18, yy + 4, str(v), size=10, fill=t['faint'], anchor='end', tabular=True)
    n = len(days)
    slot = (w - 26) / n
    bw = min(24, slot * 0.6)
    for i, (day, c) in enumerate(days.items()):
        cx = x + 26 + i * slot + (slot - bw) / 2
        yy = bottom
        for j, (key, _, col) in enumerate(series):
            v = c.get(key, 0)
            if not v:
                continue
            hh = v * scale
            gap = 2 if yy < bottom else 0
            s.vbar(cx, yy - hh, bw, hh - gap, col, cap=(j == len(series) - 1 or not any(c.get(k2, 0) for k2, _, _ in series[j + 1:])))
            yy -= hh
        s.text(cx + bw / 2, bottom - sum(c.values()) * scale - 5, str(sum(c.values())), size=10, fill=t['muted'], anchor='middle', tabular=True)
        s.text(cx + bw / 2, bottom + 14, day[5:].replace('-', '/'), size=10, fill=t['faint'], anchor='middle')


def outcomes(s, d, x, y, w, h):
    t = s.t
    why = d['why']
    rows = [('compacted while waiting', d['compacted'], t['early'])]
    other = [('preempted by auto-compact', sum(v for k2, v in why.items() if 'preempted' in k2)),
             ('task finished first, skipped', sum(v for k2, v in why.items() if 'already finished' in k2)),
             ('headless session, gave up', sum(v for k2, v in why.items() if 'headless' in k2)),
             ('superseded by a later call', d['superseded'])]
    rows += [(a, b, t['other']) for a, b in other if b]
    s.text(x, y, f'How the {d["scheduled"]} schedules ended', size=13, weight=600)
    top = y + 20
    peak = max(v for _, v, _ in rows)
    label_w = 178
    bar_x = x + label_w
    scale = (w - label_w - 36) / peak
    rh = 24
    for i, (name, v, col) in enumerate(rows):
        yy = top + i * (rh + 10)
        s.text(bar_x - 8, yy + 16, name, size=11, fill=t['muted'], anchor='end')
        s.hbar(bar_x, yy, max(v * scale, 3), rh, col)
        s.text(bar_x + max(v * scale, 3) + 6, yy + 16, str(v), size=11, fill=t['text'], tabular=True)


def hidden(s, d, x, y, w, h):
    t = s.t
    fr = d['hidden_fraction']
    bins = [('none to a quarter', sum(1 for f in fr if f < 0.25)),
            ('a quarter to half', sum(1 for f in fr if 0.25 <= f < 0.5)),
            ('half to three quarters', sum(1 for f in fr if 0.5 <= f < 0.75)),
            ('three quarters to all', sum(1 for f in fr if f >= 0.75))]
    s.text(x, y, 'Share of a compaction hidden by its task', size=13, weight=600)
    top = y + 20
    peak = max(v for _, v in bins)
    label_w = 150
    bar_x = x + label_w
    scale = (w - label_w - 36) / peak
    rh = 24
    for i, (name, v) in enumerate(bins):
        yy = top + i * (rh + 10)
        s.text(bar_x - 8, yy + 16, name, size=11, fill=t['muted'], anchor='end')
        s.hbar(bar_x, yy, max(v * scale, 3), rh, t['early'])
        s.text(bar_x + max(v * scale, 3) + 6, yy + 16, str(v), size=11, fill=t['text'], tabular=True)
    s.text(x, top + 4 * (rh + 10) + 10, f'{d["time"]["fully_hidden"]} of {len(fr)} fully hidden; short tasks cap the rest.',
           size=11, fill=t['faint'])


def draw(d, theme):
    t = THEMES[theme]
    s = Svg(t)
    tm = d['time']
    H = 668
    pad = 28
    # Header
    s.text(pad, 44, 'early-compact in practice', size=22, weight=600)
    s.text(pad, 66,
           f'{d["scheduled"]} compactions scheduled across {d["projects"]} projects, {d["first"][:10]} to {d["last"][:10]}. '
           f'Measured from Claude Code transcripts by scripts/usage-report.py.',
           size=12, fill=t['muted'])
    # Hero + tiles
    y0 = 92
    hero_w = 250
    gap = 12
    hero_h = 2 * 96 + gap
    s.rect(pad, y0, hero_w, hero_h, t['early'], r=6)
    s.text(pad + 16, y0 + 78, fmt_min(tm['net']), size=56, weight=700, fill='#ffffff', tabular=True)
    s.text(pad + 16, y0 + 106, 'of waiting removed, net', size=14, fill='#ffffff', opacity=0.9)
    s.text(pad + 16, y0 + 146, f'{tm["net"] / d["compacted"]:.0f}s per early compaction', size=12, fill='#ffffff', opacity=0.8)
    s.text(pad + 16, y0 + 164, f'{fmt_min(tm["replaced"])} of auto-compaction replaced', size=12, fill='#ffffff', opacity=0.8)
    s.text(pad + 16, y0 + 182, f'{fmt_min(tm["exposed"])} still waited on', size=12, fill='#ffffff', opacity=0.8)
    tiles = [
        (f'{d["compacted"]} of {d["scheduled"]}', 'schedules compacted while waiting',
         f'{d["compacted"] / d["scheduled"]:.0%}; {d["doubles"]} double compactions'),
        (f'{tm["hidden"] / tm["total"]:.0%}', 'of compaction time hidden behind a task',
         f'{fmt_min(tm["hidden"])} of {fmt_min(tm["total"])}'),
        (f'{d["early"]["dur"]:.0f}s vs {d["auto"]["dur"]:.0f}s', 'median early vs auto-compaction',
         f'early {k(d["early"]["pre"])} to {k(d["early"]["post"])} tokens, auto {k(d["auto"]["pre"])} to {k(d["auto"]["post"])}'),
        (f'{tm["equiv_med"]:.2f}', 'auto cycles replaced per early compaction',
         f'{d["early"]["count"]} early vs {d["auto_mod"]["count"]} auto in the same sessions'),
    ]
    tw = (W - 2 * pad - hero_w - gap * 2) / 2
    for i, (v, l, n) in enumerate(tiles):
        tile(s, pad + hero_w + gap + (i % 2) * (tw + gap), y0 + (i // 2) * (96 + gap), tw, v, l, n)
    # Panels
    y1 = y0 + hero_h + 44
    pw = (W - 2 * pad - 2 * 24) / 3
    by_day(s, d, pad, y1, pw, 260)
    outcomes(s, d, pad + pw + 24, y1, pw, 260)
    hidden(s, d, pad + 2 * (pw + 24), y1, pw, 260)
    # Footer
    s.text(pad, H - 36,
           'Net saving = auto-compaction time each early compaction replaces (its freed tokens as a fraction of a median auto cycle, '
           'times the median auto duration)',
           size=10.5, fill=t['faint'])
    s.text(pad, H - 21,
           f'minus compaction time nobody was waiting on anyway. {d["reminders"]} threshold reminders were sent; {d["missed"]} slow '
           f'foreground commands were missed chances. Generated {d["generated"][:10]}.',
           size=10.5, fill=t['faint'])
    return s.render(H)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('summary', type=Path, help='--summary output of usage-report.py')
    ap.add_argument('--out', type=Path, default=Path(__file__).resolve().parent.parent / 'docs',
                    help='directory for usage-light.svg and usage-dark.svg')
    args = ap.parse_args()
    d = json.loads(args.summary.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        (args.out / f'usage-{theme}.svg').write_text(draw(d, theme))
        print(args.out / f'usage-{theme}.svg')


if __name__ == '__main__':
    main()
