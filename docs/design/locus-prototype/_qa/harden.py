"""Wrap raw interpolations in HTML-producing template literals with an encoder chosen by context.
URL query values → ENC(...); everything else → ESC(...). Skips values already encoded, numeric
expressions, and ternaries whose literals carry markup or quotes (printed for manual review).
Usage: python _qa/harden.py <file> <ESC> <ENC> [--dry]     e.g. python _qa/harden.py task.html H.esc H.enc"""
import io, re, sys
path, ESC, ENC = sys.argv[1], sys.argv[2], sys.argv[3]
dry = '--dry' in sys.argv
src = io.open(path, encoding='utf-8').read()
SAFE = re.compile(r"^\s*(esc|H\.esc|D\.esc|e|enc|H\.enc|D\.enc|encodeURIComponent|num|H\.assignee|H\.taskChip|H\.runChip|Locus\.icon|icon|H\.hm|H\.ago|H\.left|H\.elapsed|D\.hm|D\.ago|D\.left|dlabel|dcls|decWord|sessLine|impactHtml|av|opt|primary|D|days|inboxHtml|decHtml|decFootHtml|acctHtml|row|mark)\(|^\s*(primary|banner|ctl\.join\(''\))\s*$")
NUMERIC = re.compile(r"^\s*([\w.]+\.length|[\w.()]+\.toFixed\(\d\)|Math\.\w+\(.*\)|[\d.]+|i \+ 1|i|n|pct|k|String\(i \+ 1\)\.padStart\(2, '0'\)|[\w.]+ \? 'checked' : ''|[\w.]+ \? 'selected' : ''|[\w. =!'()\[\]]+ \? '(checked|selected|is-sel|disabled)' : '')\s*$")
SIMPLE = re.compile(r"^[\w$.\[\]()', :|-]+$")
QUOTED_TERNARY = re.compile(r"^[^`<>\"]*\?[^`<>\"]*:[^`<>\"]*$")  # ternary with no markup and no double quotes

def skip_string(i):
    q = src[i]; i += 1
    while src[i] != q:
        if src[i] == '\\': i += 1
        i += 1
    return i + 1

items = []  # (start, end, expr, static_before, is_html)
def template(i):
    """i is just after an opening backtick; returns index after the closing one."""
    static, mine = [], []
    while True:
        c = src[i]
        if c == '\\': static.append(src[i:i + 2]); i += 2; continue
        if c == '`': break
        if src.startswith('${', i):
            j, d = i + 2, 1
            while d:
                ch = src[j]
                if ch == '`': j = template(j + 1); continue
                if ch in '\'"': j = skip_string(j); continue
                if ch == '{': d += 1
                elif ch == '}': d -= 1
                j += 1
            mine.append((i, j, src[i + 2:j - 1], ''.join(static))); static.append('\x00'); i = j; continue
        static.append(c); i += 1
    html = '<' in ''.join(static)
    items.extend((a, b, ex, bf, html) for a, b, ex, bf in mine)
    return i + 1

def skip_regex(i):
    i += 1; cls = False
    while True:
        ch = src[i]
        if ch == chr(92): i += 2; continue
        if ch == "[": cls = True
        elif ch == "]": cls = False
        elif ch == "/" and not cls: break
        elif ch == chr(10): return i
        i += 1
    i += 1
    while src[i].isalpha(): i += 1
    return i

def parse(i, end):
    while i < end:
        c = src[i]
        if src.startswith("//", i) and src[i - 1] in " ;{(," + chr(10):
            i = src.find(chr(10), i); i = end if i < 0 else i; continue
        if src.startswith("/*", i): i = src.find("*/", i) + 2; continue
        if c == "/":
            k = i - 1
            while k > 0 and src[k] in " " + chr(9): k -= 1
            if src[k] in "(,=:[!&|?{};" + chr(10) or src[k - 5:k + 1] == "return": i = skip_regex(i); continue
        if c in chr(39) + chr(34): i = skip_string(i); continue
        if c == chr(96): i = template(i + 1); continue
        i += 1

import re as _re
if path.endswith('.js'): parse(0, len(src))
else:
    for m in _re.finditer(r'<script>(.*?)</script>', src, _re.S): parse(m.start(1), m.end(1))
edits, manual = [], []
for a, b, ex, before, html in items:
    e = ex.strip()
    if not html or SAFE.match(e) or NUMERIC.match(e) or e.startswith(("'", '`')): continue
    tail = before.split('\x00')[-1] if '\x00' in before else before
    in_url = re.search(r'(href|src|action)="[^"]*$', tail) is not None
    query_val = in_url and re.search(r'[?&=]$', tail) is not None
    if SIMPLE.match(e) and '?' not in e:
        fn = ENC if query_val else ESC
    elif QUOTED_TERNARY.match(e) and not in_url:
        fn = ESC
    else:
        manual.append((src.count('\n', 0, a) + 1, e[:120])); continue
    edits.append((a, b, '${' + fn + '(' + e + ')}'))
for a, b, rep in sorted(edits, reverse=True):
    src = src[:a] + rep + src[b:]
if not dry: io.open(path, 'w', encoding='utf-8', newline='\n').write(src)
print(f'{path}: wrapped {len(edits)}, manual review {len(manual)}')
for ln, e in manual: print(f'  {path}:{ln}: ${{{e}}}')
