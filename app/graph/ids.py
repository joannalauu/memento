"""Node identity: the single source of truth for the graph's deterministic ids.

The graph has no node/edge collection — nodes are a projection of memoryIndex,
and their ids ARE the join key (two decisions touching the same file connect
only because both build the identical `file:...` id). Both the aggregation
(`app/graph/crud.py`) and the agent walk (`app/context_engine/graph_tools.py`)
must build and parse these ids identically, so the logic lives here, imported
by both. This module is a leaf — it imports nothing from either caller.

    decision  ->  "dec:<memoryIndex._id>"
    file      ->  "file:<repo>:<path>"      (repo-qualified — org spans repos)
    pr        ->  "pr:<repo>:<prNumber>"
    engineer  ->  "eng:<userId>"
    feature   ->  "feat:<orgId>:<featureName>"
"""

from typing import NamedTuple

from app.graph.schemas import NodeType

LABEL_MAX = 60


def short_label(content: str) -> str:
    """First line of the content snapshot, truncated for canvas labels."""
    stripped = content.strip()
    first = stripped.splitlines()[0] if stripped else "(empty)"
    return first if len(first) <= LABEL_MAX else first[: LABEL_MAX - 1] + "…"


def decision_id(memory_id: object) -> str:
    return f"dec:{memory_id}"


def file_id(repo: str, path: str) -> str:
    return f"file:{repo}:{path}"


def pr_id(repo: str, pr_number: int) -> str:
    return f"pr:{repo}:{pr_number}"


def engineer_id(user_id: object) -> str:
    return f"eng:{user_id}"


def feature_id(org_id: object, name: str) -> str:
    return f"feat:{org_id}:{name}"


class ParsedNodeId(NamedTuple):
    """A node id split into its type and the fields packed into it.

    - decision: rest = memoryIndex id
    - file:     repo, rest = path
    - pr:       repo, rest = pr number (str; caller parses to int)
    - engineer: rest = user id
    - feature:  repo = org id, rest = feature name
    """

    type: NodeType
    repo: str | None
    rest: str


_PREFIX_TO_TYPE: dict[str, NodeType] = {
    "dec": "decision",
    "file": "file",
    "pr": "pr",
    "eng": "engineer",
    "feat": "feature",
}


def parse_node_id(node_id: str) -> ParsedNodeId:
    """Split a node id into its parts, inverse of the builders above.

    For `file:`/`pr:`/`feat:` the middle segment (repo `owner/name`, org-id hex)
    contains no colon, so one more `split(":", 1)` cleanly separates it from the
    remainder — which may itself contain colons (file paths, feature names).
    Raises ValueError on any id that doesn't match the scheme.
    """
    prefix, sep, remainder = node_id.partition(":")
    node_type = _PREFIX_TO_TYPE.get(prefix)
    if not sep or node_type is None or not remainder:
        raise ValueError(f"malformed node id: {node_id!r}")

    if node_type in ("decision", "engineer"):
        return ParsedNodeId(type=node_type, repo=None, rest=remainder)

    # file / pr / feature: <repo-or-org>:<rest>
    middle, sep2, rest = remainder.partition(":")
    if not sep2 or not middle or not rest:
        raise ValueError(f"malformed node id: {node_id!r}")
    return ParsedNodeId(type=node_type, repo=middle, rest=rest)
