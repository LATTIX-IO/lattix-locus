"""Wrap raw interpolations in HTML-producing template literals with an encoder chosen by context.
URL query values → ENC(...); everything else → ESC(...). Skips values already encoded, numeric
expressions, and ternaries whose literals carry markup or quotes (printed for manual review).
Usage: python _qa/harden.py <file> <ESC> <ENC> [--dry]     e.g. python _qa/harden.py task.html H.esc H.enc"""
import io
import re
import sys
path, ESC, ENC = sys.argv[1], sys.argv[2], sys.argv[3]
dry = '--dry' in sys.argv
src = io.open(path, encoding='utf-8').read()
SAFE = re.compile(r"^\s*(esc|H\.esc|D\.esc|e|enc|H\.enc|D\.enc|encodeURIComponent|num|H\.assignee|H\.taskChip|H\.runChip|Locus\.icon|icon|H\.hm|H\.ago|H\.left|H\.elapsed|D\.hm|D\.ago|D\.left|dlabel|dcls|decWord|sessLine|impactHtml|av|opt|primary|D|days|inboxHtml|decHtml|decFootHtml|acctHtml|row|mark)\(|^\s*(primary|banner|ctl\.join\(''\))\s*$")
NUMERIC = re.compile(r"^\s*([\w.]+\.length|[\w.()]+\.toFixed\(\d\)|Math\.\w+\(.*\)|[\d.]+|i \+ 1|i|n|pct|k|String\(i \+ 1\)\.padStart\(2, '0'\)|[\w.]+ \? 'checked' : ''|[\w.]+ \? 'selected' : ''|[\w. =!'()\[\]]+ \? '(checked|selected|is-sel|disabled)' : '')\s*$")
SIMPLE = re.compile(r"^[\w$.\[\]()', :|-]+$")
QUOTED_TERNARY = re.compile(r"^[^`<>\"]*\?[^`<>\"]*:[^`<>\"]*$")  # ternary with no markup and no double quotes

def skip_string(i):
    q = src[i]; i += 1  # noqa: E701, E702
    while src[i] != q:
        if src[i] == '\\': i += 1  # noqa: E701, E702
        i += 1
    return i + 1

items = []  # (start, end, expr, static_before, is_html)
def template(i):
    """i is just after an opening backtick; returns index after the closing one."""
    static, mine = [], []
    while True:
        c = src[i]
        if c == '\\': static.append(src[i:i + 2]); i += 2; continue  # noqa: E701, E702
        if c == '`': break  # noqa: E701, E702
        if src.startswith('${', i):
            j, d = i + 2, 1
            while d:
                ch = src[j]
                if ch == '`': j = template(j + 1); continue  # noqa: E701, E702
                if ch in '\'"': j = skip_string(j); continue  # noqa: E701, E702
                if ch == '{': d += 1  # noqa: E701, E702
                elif ch == '}': d -= 1  # noqa: E701, E702
                j += 1
            mine.append((i, j, src[i + 2:j - 1], ''.join(static))); static.append('\x00'); i = j; continue  # noqa: E701, E702
        static.append(c); i += 1  # noqa: E701, E702
    html = '<' in ''.join(static)
    items.extend((a, b, ex, bf, html) for a, b, ex, bf in mine)
    return i + 1

def skip_regex(i):
    i += 1; cls = False  # noqa: E701, E702
    while True:
        ch = src[i]
        if ch == chr(92): i += 2; continue  # noqa: E701, E702
        if ch == "[": cls = True  # noqa: E701, E702
        elif ch == "]": cls = False  # noqa: E701, E702
        elif ch == "/" and not cls: break  # noqa: E701, E702
        elif ch == chr(10): return i  # noqa: E701, E702
        i += 1
    i += 1
    while src[i].isalpha(): i += 1  # noqa: E701, E702
    return i

def parse(i, end):
    while i < end:
        c = src[i]
        if src.startswith("//", i) and src[i - 1] in " ;{(," + chr(10):
            i = src.find(chr(10), i); i = end if i < 0 else i; continue  # noqa: E701, E702
        if src.startswith("/*", i): i = src.find("*/", i) + 2; continue  # noqa: E701, E702
        if c == "/":
            k = i - 1
            while k > 0 and src[k] in " " + chr(9): k -= 1  # noqa: E701, E702
            if src[k] in "(,=:[!&|?{};" + chr(10) or src[k - 5:k + 1] == "return": i = skip_regex(i); continue  # noqa: E701, E702
        if c in chr(39) + chr(34): i = skip_string(i); continue  # noqa: E701, E702
        if c == chr(96): i = template(i + 1); continue  # noqa: E701, E702
        i += 1

import re as _re
if path.endswith('.js'): parse(0, len(src))  # noqa: E701, E702
else:
    # This scanner is a local developer aid, not an HTML sanitization boundary.
    # Locate script sections case-insensitively without a blacklist regexp.
    lowered = src.lower()
    cursor = 0
    while cursor < len(src):
        tag = lowered.find("<script", cursor)
        if tag < 0:
            break
        opening_end = src.find(">", tag)
        if opening_end < 0:
            break
        ending = lowered.find("</script", opening_end + 1)
        if ending < 0:
            break
        parse(opening_end + 1, ending)
        cursor = ending + len("</script")
edits, manual = [], []
for a, b, ex, before, html in items:
    e = ex.strip()
    if not html or SAFE.match(e) or NUMERIC.match(e) or e.startswith(("'", '`')): continue  # noqa: E701, E702
    tail = before.split('\x00')[-1] if '\x00' in before else before
    in_url = re.search(r'(href|src|action)="[^"]*$', tail) is not None
    query_val = in_url and re.search(r'[?&=]$', tail) is not None
    if SIMPLE.match(e) and '?' not in e:
        fn = ENC if query_val else ESC
    elif QUOTED_TERNARY.match(e) and not in_url:
        fn = ESC
    else:
        manual.append((src.count('\n', 0, a) + 1, e[:120])); continue  # noqa: E701, E702
    edits.append((a, b, '${' + fn + '(' + e + ')}'))
for a, b, rep in sorted(edits, reverse=True):
    src = src[:a] + rep + src[b:]
if not dry: io.open(path, 'w', encoding='utf-8', newline='\n').write(src)  # noqa: E701, E702
print(f'{path}: wrapped {len(edits)}, manual review {len(manual)}')
for ln, e in manual: print(f'  {path}:{ln}: ${{{e}}}')  # noqa: E701, E702
