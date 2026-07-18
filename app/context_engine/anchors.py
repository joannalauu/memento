"""Anchor extraction: mechanical parse of a diff into file/symbol handles.

Anchors are the shared vocabulary between code and memories — memories record
the files and symbols they govern (`memoryIndex.anchors`), so extracting the
same identifiers from a new diff is what makes "what do we already know about
this change?" answerable. Like the transcript normalizer, this is a filter
driven by structure, not meaning: no LLM, no tree-sitter (a future upgrade
behind the same signature), deterministic output, malformed input yields
partial/empty anchors and never raises.

Two input formats are accepted, line-by-line (no state machine that can wedge):

- standard `git diff` unified output: files from `diff --git a/X b/Y`,
  `+++ b/...`, `--- a/...` and `rename from/to` lines (`/dev/null` skipped,
  both sides of a rename kept so pre-rename memories still match);
- `GitHubToolset.get_pr_diff` output (app/github/tools.py): per-file
  `--- {path} ({status}, +N/-M)` header lines followed by bare `@@` hunks —
  GitHub's `files[].patch` carries no `diff --git`/`+++`/`---` lines.

Symbols come from the `@@ ... @@ <context>` hunk-header trailer (git prints
the enclosing definition there) and from added/removed definition lines
(`def`/`class`/`function`/`const x = () =>`). Known accepted misses:
decorated one-line defs whose decorator is the only changed line, symbols
that appear only in unchanged mid-hunk context, and C-like languages.
"""

import re

from app.backboard.models import Anchors

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
# get_pr_diff header: `--- app/foo.py (modified, +10/-2)` — must be tried
# before the plain `---` rule or the status suffix leaks into the filename.
_GH_FILE_RE = re.compile(r"^--- (.+?) \(\w+, \+\d+/-\d+\)$")
_OLD_FILE_RE = re.compile(r"^--- (?:a/)?(.+)$")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")
_RENAME_RE = re.compile(r"^rename (?:from|to) (.+)$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@ ?(.*)$")

# Definition patterns applied to hunk-header context and to +/- line payloads.
_SYMBOL_RES = (
    re.compile(r"\b(?:async\s+)?def\s+([A-Za-z_]\w*)"),  # Python def
    re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)"),  # Python/JS/TS class
    re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)"),  # JS/TS function
    re.compile(  # const/let/var bound to a function or arrow expression
        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
        r"(?:async\s+)?(?:\(|function\b|[A-Za-z_$][\w$]*\s*=>)"
    ),
)


def _strip_timestamp(path: str) -> str:
    """`+++ b/foo.py\t2026-07-18 10:00:00` → `foo.py` (diff -u style)."""
    return path.split("\t")[0].strip()


def _symbols_in(text: str) -> list[str]:
    found: list[str] = []
    for pattern in _SYMBOL_RES:
        if m := pattern.search(text):
            found.append(m.group(1))
    return found


def extract_anchors(diff: str, *, repo: str) -> Anchors:
    """Best-effort parse of a diff into Anchors (first-seen order, deduped).

    Deterministic and I/O-free. `repo` is required because `Anchors.repo` is —
    fabricating it here would silently break repo-scoped retrieval downstream.
    """
    if not isinstance(diff, str):
        diff = ""
    files: dict[str, None] = {}
    symbols: dict[str, None] = {}

    def add_file(path: str) -> None:
        path = _strip_timestamp(path)
        if path and path != "/dev/null":
            files.setdefault(path)

    for line in diff.splitlines():
        if m := _DIFF_GIT_RE.match(line):
            old, new = m.group(1), m.group(2)
            add_file(new)
            if old != new:  # rename: old path keeps matching pre-rename memories
                add_file(old)
            continue
        if m := _GH_FILE_RE.match(line):
            add_file(m.group(1))
            continue
        if line.startswith("+++ "):
            if m := _NEW_FILE_RE.match(line):
                add_file(m.group(1))
            continue
        if line.startswith("--- "):
            if m := _OLD_FILE_RE.match(line):
                add_file(m.group(1))  # covers pure deletions (+++ is /dev/null)
            continue
        if m := _RENAME_RE.match(line):
            add_file(m.group(1))
            continue
        if m := _HUNK_RE.match(line):
            for symbol in _symbols_in(m.group(1)):
                symbols.setdefault(symbol)
            continue
        if line[:1] in ("+", "-"):
            for symbol in _symbols_in(line[1:]):
                symbols.setdefault(symbol)

    return Anchors(repo=repo, files=list(files), symbols=list(symbols))
