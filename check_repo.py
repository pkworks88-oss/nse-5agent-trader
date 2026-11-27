"""Fail fast if a source file is broken (stray chat/citation text, merge-conflict markers, syntax errors).
   python check_repo.py        -> exit code 1 and a list of problems"""
import pathlib
import py_compile
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
BAD = [(re.compile(r"\[cite:?\s*[\d, ]*\]"), "stray citation marker [cite: ...]"),
       (re.compile(r"^\s*```"), "stray markdown code fence ```"),
       (re.compile(r"^(<<<<<<<|>>>>>>>)( |$)"), "git merge-conflict marker"),
       (re.compile(r"^=======$"), "git merge-conflict marker")]
problems = []
for p in sorted(ROOT.rglob("*.py")):
    if any(part in (".git", "__pycache__", "venv", ".venv") for part in p.parts):
        continue
    for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        for rx, why in BAD:
            if rx.search(line):
                problems.append(f"{p.relative_to(ROOT)}:{n}: {why}: {line.strip()[:70]}")
    try:
        py_compile.compile(str(p), doraise=True)
    except py_compile.PyCompileError as exc:
        problems.append(f"{p.relative_to(ROOT)}: SYNTAX ERROR: {str(exc).strip().splitlines()[-1][:120]}")
if problems:
    print("PROBLEMS FOUND:\n" + "\n".join(problems))
    sys.exit(1)
print("check_repo: all Python files are clean")
