from app.context_engine.anchors import extract_anchors

REPO = "acme/api-server"


def test_extract_anchors_files_from_headers():
    diff = (
        "diff --git a/app/rate_limit.py b/app/rate_limit.py\n"
        "--- a/app/rate_limit.py\n"
        "+++ b/app/rate_limit.py\n"
        "@@ -1,3 +1,4 @@\n"
        "+import asyncio\n"
        "diff --git a/app/session.py b/app/session.py\n"
        "--- a/app/session.py\n"
        "+++ b/app/session.py\n"
        "@@ -10,2 +10,3 @@\n"
        "+    pass\n"
    )
    anchors = extract_anchors(diff, repo=REPO)
    assert anchors.repo == REPO
    assert anchors.files == ["app/rate_limit.py", "app/session.py"]


def test_extract_anchors_rename_and_devnull():
    diff = (
        "diff --git a/app/old_name.py b/app/new_name.py\n"
        "rename from app/old_name.py\n"
        "rename to app/new_name.py\n"
        "diff --git a/app/created.py b/app/created.py\n"
        "--- /dev/null\n"
        "+++ b/app/created.py\t2026-07-18 10:00:00\n"
        "diff --git a/app/deleted.py b/app/deleted.py\n"
        "--- a/app/deleted.py\n"
        "+++ /dev/null\n"
    )
    anchors = extract_anchors(diff, repo=REPO)
    # rename yields both paths; /dev/null never appears; timestamp tab stripped
    assert anchors.files == [
        "app/new_name.py",
        "app/old_name.py",
        "app/created.py",
        "app/deleted.py",
    ]


def test_extract_anchors_symbols_from_hunks_and_def_lines():
    diff = (
        "diff --git a/app/limits.py b/app/limits.py\n"
        "--- a/app/limits.py\n"
        "+++ b/app/limits.py\n"
        "@@ -40,6 +40,8 @@ def handler(self):\n"
        "+class RateLimiter:\n"
        "+    async def acquire(self):\n"
        "-function legacyInit() {\n"
        "+const fetchUser = async () => {\n"
        "+ x = 1\n"
        "+    return count\n"
    )
    anchors = extract_anchors(diff, repo=REPO)
    assert anchors.symbols == [
        "handler",
        "RateLimiter",
        "acquire",
        "legacyInit",
        "fetchUser",
    ]


def test_extract_anchors_github_pr_diff_format():
    # GitHubToolset.get_pr_diff output: status header + bare patch hunks,
    # no diff --git / +++ / --- lines at all.
    diff = (
        "--- app/limits.py (modified, +10/-2)\n"
        "@@ -40,6 +40,8 @@ def handler(self):\n"
        "+class RateLimiter:\n"
        "--- app/new_file.py (added, +30/-0)\n"
        "@@ -0,0 +1,30 @@\n"
        "+def create_session():\n"
        "--- assets/logo.png (modified, +0/-0)\n"
        "[no patch — binary or too large]\n"
    )
    anchors = extract_anchors(diff, repo=REPO)
    # the status suffix must never leak into a filename
    assert anchors.files == ["app/limits.py", "app/new_file.py", "assets/logo.png"]
    assert anchors.symbols == ["handler", "RateLimiter", "create_session"]


def test_extract_anchors_malformed_never_raises():
    for garbage in ("", "not a diff at all\n\x00\xff junk", "@@ truncated", None):
        anchors = extract_anchors(garbage, repo=REPO)  # type: ignore[arg-type]
        assert anchors.repo == REPO
        assert anchors.files == []
        assert anchors.symbols == []


def test_extract_anchors_dedup_preserves_order():
    diff = (
        "diff --git a/app/limits.py b/app/limits.py\n"
        "--- a/app/limits.py\n"
        "+++ b/app/limits.py\n"
        "@@ -40,6 +40,8 @@ def acquire(self):\n"
        "+def acquire(self):\n"
        "@@ -80,2 +82,3 @@ def acquire(self):\n"
        "+    pass\n"
    )
    anchors = extract_anchors(diff, repo=REPO)
    assert anchors.files == ["app/limits.py"]
    assert anchors.symbols == ["acquire"]
