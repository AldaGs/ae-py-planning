"""Crude ES3 sanity check: brace balance and unterminated string literals.

Not a parser. It exists because the only real syntax check for this file is
After Effects itself, and a round trip through AE to find a stray newline in a
string literal is an expensive way to learn that.
"""
import sys

src = open(sys.argv[1], encoding="utf-8").read()

depth = {"{": 0, "(": 0, "[": 0}
pairs = {"}": "{", ")": "(", "]": "["}
i = 0
line = 1
state = None          # None | '"' | "'" | "//" | "/*"
problems = []
while i < len(src):
    c = src[i]
    nxt = src[i + 1] if i + 1 < len(src) else ""
    if c == "\n":
        line += 1
        if state in ('"', "'"):
            problems.append(f"line {line - 1}: newline inside a string literal")
            state = None
        elif state == "//":
            state = None
        i += 1
        continue
    if state == "//" or state == "/*":
        if state == "/*" and c == "*" and nxt == "/":
            state = None
            i += 2
            continue
        i += 1
        continue
    if state in ('"', "'"):
        if c == "\\":
            i += 2
            continue
        if c == state:
            state = None
        i += 1
        continue
    if c == "/" and nxt == "/":
        state = "//"
        i += 2
        continue
    if c == "/" and nxt == "*":
        state = "/*"
        i += 2
        continue
    if c in ('"', "'"):
        state = c
        i += 1
        continue
    if c in depth:
        depth[c] += 1
    elif c in pairs:
        depth[pairs[c]] -= 1
        if depth[pairs[c]] < 0:
            problems.append(f"line {line}: unmatched {c}")
    i += 1

for k, v in depth.items():
    if v:
        problems.append(f"{v} unclosed {k}")
if state:
    problems.append(f"file ends inside {state}")

print("\n".join(problems) if problems else "balanced, no unterminated strings")
