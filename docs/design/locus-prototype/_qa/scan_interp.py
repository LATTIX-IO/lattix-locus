"""List template-literal interpolations that are not passed through an escaping or encoding helper.
Usage: python _qa/scan_interp.py [files...]  (defaults to every page plus locus.js)"""
import io
import os
import re
import sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAFE = re.compile(r"^\s*(esc|H\.esc|D\.esc|e|enc|encodeURIComponent|num|H\.assignee|H\.taskChip|H\.runChip|Locus\.icon|icon|H\.hm|H\.ago|H\.left|H\.elapsed|D\.hm|D\.ago|D\.left|dlabel|dcls|decWord|sessLine|fmtMoney|impactHtml|av|T|String\(i \+ 1\)|i \+ 1|i|n|pct)\b")
NUMERIC = re.compile(r"(\.length|\.toFixed\(\d\)|Math\.\w+\(|^\s*\+?\(?[\d.]+|\bcount\b|\bpct\b|padStart)")
def interps(src):
    out, i = [], 0
    while True:
        j = src.find('${', i)
        if j < 0: return out  # noqa: E701, E702
        depth, k = 1, j + 2
        while k < len(src) and depth:
            c = src[k]
            if c == '{': depth += 1  # noqa: E701, E702
            elif c == '}': depth -= 1  # noqa: E701, E702
            elif c == '`':  # skip nested template
                k2 = k + 1
                while k2 < len(src) and src[k2] != '`':
                    if src[k2] == '\\': k2 += 1  # noqa: E701, E702
                    k2 += 1
                k = k2
            k += 1
        out.append((src.count('\n', 0, j) + 1, src[j + 2:k - 1])); i = j + 2  # noqa: E701, E702
files = sys.argv[1:] or sorted([f for f in os.listdir(ROOT) if f.endswith('.html') and f != 'prototype-tests.html'] + ['locus.js'])
total = 0
for f in files:
    src = io.open(os.path.join(ROOT, f), encoding='utf-8').read()
    for ln, ex in interps(src):
        e = ex.strip()
        if SAFE.match(e) or NUMERIC.search(e) or e.startswith(('`', "'", '"')) or '?' in e and '`' in e: continue  # noqa: E701, E702
        if re.match(r"^[\w.]+\.map\(", e) or '.join(' in e and '=>' in e: continue  # mapped blocks: their own interpolations are scanned  # noqa: E701, E702
        total += 1; print(f"{f}:{ln}: ${{{e[:110]}}}")  # noqa: E701, E702
print('raw interpolations:', total)
