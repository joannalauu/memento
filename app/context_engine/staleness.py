"""Staleness check: has the code a memory describes moved on since it was written?

One question, one function. `staleness_check` compares the ``commitSha`` stamped
on a memory at distillation time against the current state of the memory's
anchored files and returns a three-way verdict:

- ``fresh`` — no anchored file has changed since ``memoryCommitSha``. The memory
  still describes current code; safe to present as current.
- ``stale`` — an anchored file has changed AND a newer memory covers the same
  anchors. The memory is historically true but superseded; the newer one is
  current. (Catches supersession even when ``supersededBy`` wasn't recorded.)
- ``gap`` — an anchored file has changed and NOTHING newer covers it. The
  dangerous case: the code moved, nobody recorded why, and this memory is now the
  most recent thing the system knows — one iteration behind. ``gap`` is what lets
  the system say "I'm missing context here" instead of serving stale rationale.

Distinguishing ``stale`` from ``gap`` is a single local ``memoryIndex`` query
(any active memory on the same anchors created later), so it costs nothing beyond
the GitHub history reads. Movement is judged on anchored *files* only — symbols
aren't history-diffable. The function reads ``memoryIndex`` and calls the repo's
`RepoHistory` (T2.4); it writes nothing.

Undeterminable movement — the memory has no ``commitSha``, no file anchors, its
base commit is gone, or every history read failed — never returns ``fresh``:
without positive evidence the memory can't be vouched current, so it degrades to
``stale`` when something newer exists, else ``gap``.
"""

from datetime import datetime, timezone

from app.backboard.models import Anchors, MemoryIndex
from app.context_engine.schemas import StalenessVerdict
from app.github.client import GitHubError
from app.github.history import RepoHistory


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _newer_memory_exists(
    memory: MemoryIndex, files: list[str], symbols: list[str]
) -> bool:
    """Is there an active memory on the same repo + anchors created after this
    one? Empty anchors match nothing, so a symbol-/file-less memory is never
    considered covered."""
    if not files and not symbols:
        return False
    newer = await MemoryIndex.find_one(
        {
            "repoId": memory.repoId,
            "deletedAt": None,
            "_id": {"$ne": memory.id},
            "createdAt": {"$gt": memory.createdAt},
            "$or": [
                {"anchors.files": {"$in": files}},
                {"anchors.symbols": {"$in": symbols}},
            ],
        }
    )
    return newer is not None


async def _changed_since(
    history: RepoHistory,
    files: list[str],
    *,
    since: datetime,
    ref: str | None,
) -> tuple[list[str], set[str], bool]:
    """Per-file history walk. Returns (changed files, distinct commit shas that
    touched them, complete) — ``complete`` is False if any file's history read
    failed, so the caller knows the "nothing changed" answer is only partial."""
    changed: list[str] = []
    shas: set[str] = set()
    complete = True
    for path in files:
        try:
            path_shas = await history.commits_touching_path_since(
                path, since=since, ref=ref
            )
        except GitHubError:
            complete = False
            continue
        if path_shas:
            changed.append(path)
            shas.update(path_shas)
    return changed, shas, complete


async def staleness_check(
    memory: MemoryIndex,
    *,
    history: RepoHistory,
    anchors: Anchors | None = None,
    ref: str | None = None,
) -> StalenessVerdict:
    """Judge whether ``memory`` still describes current code.

    ``anchors`` overrides the memory's own anchors (e.g. to check against a
    diff's anchors); ``ref`` overrides the branch checked (default: the repo's
    default branch). See the module docstring for the three-way contract."""
    anchors = anchors or memory.anchors
    # De-dupe, preserve order — anchors are first-seen-ordered upstream.
    files = list(dict.fromkeys(anchors.files))
    symbols = list(dict.fromkeys(anchors.symbols))
    checked_at = _now_iso()
    memory_sha = memory.commitSha or ""

    newer = await _newer_memory_exists(memory, files, symbols)

    def verdict(
        status: str, changed: list[str], commits_since: int | None
    ) -> StalenessVerdict:
        return StalenessVerdict(
            status=status,
            memoryCommitSha=memory_sha,
            currentShaCheckedAt=checked_at,
            changedFiles=changed,
            commitsSince=commits_since,
            newerMemoryExists=newer,
        )

    # Undeterminable up front: no baseline sha or no file anchors to diff.
    if not memory_sha or not files:
        return verdict("stale" if newer else "gap", [], None)

    # Resolve the baseline commit's date; a missing/GC'd base is undeterminable.
    try:
        base_date = await history.commit_date(memory_sha)
    except GitHubError:
        base_date = None
    if base_date is None:
        return verdict("stale" if newer else "gap", [], None)

    changed, shas, complete = await _changed_since(
        history, files, since=base_date, ref=ref
    )
    if changed:
        return verdict("stale" if newer else "gap", changed, len(shas))
    if complete:
        # Positively confirmed: nothing anchored moved.
        return verdict("fresh", [], 0)
    # Nothing changed among the files we *could* read, but some reads failed —
    # can't claim fresh without full coverage.
    return verdict("stale" if newer else "gap", [], None)
