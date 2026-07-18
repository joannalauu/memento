from typing import get_args

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_current_user
from app.graph.crud import get_graph_cached, get_node_detail
from app.graph.schemas import GraphPayload, NodeDetail, NodeType
from app.orgs.crud import get_org
from app.orgs.models import Org, User

router = APIRouter()

_NODE_TYPES: frozenset[str] = frozenset(get_args(NodeType))


@router.get("/{org_id}/graph", response_model=GraphPayload)
async def get_org_graph_endpoint(
    org_id: PydanticObjectId,
    repo: str | None = None,
    feature: str | None = None,
    types: str | None = None,
    user: User = Depends(get_current_user),
) -> GraphPayload:
    """The org's knowledge graph as {nodes, links} for react-force-graph.

    Optional filters: ?repo= (anchors.repo), ?feature=, and ?types= (comma-
    separated node types, e.g. `decision,feature` for a files-hidden
    overview). Cached ~60s per scope. Only a member of the org may view it.
    """
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_member = any(m.userId == user.id for m in org.members)
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this org",
        )

    type_set: frozenset[NodeType] | None = None
    if types is not None:
        requested = {t.strip() for t in types.split(",") if t.strip()}
        unknown = requested - _NODE_TYPES
        if unknown or not requested:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unknown node type(s): {', '.join(sorted(unknown)) or '(none)'}; "
                    f"valid types: {', '.join(sorted(_NODE_TYPES))}"
                ),
            )
        type_set = frozenset(requested)  # type: ignore[arg-type]

    return await get_graph_cached(org_id, repo=repo, feature=feature, types=type_set)


@router.get("/{org_id}/graph/nodes/{node_id:path}", response_model=NodeDetail)
async def get_graph_node_detail_endpoint(
    org_id: PydanticObjectId,
    node_id: str,
    user: User = Depends(get_current_user),
) -> NodeDetail:
    """Detail for one clicked node. Decision nodes return the full snapshot +
    PR link / author / date; other node types return the decisions they connect
    to, so a click becomes a graph hop. Only a member of the org may view it.

    `node_id` uses the `:path` converter because file/pr/feature ids embed
    slash-bearing repo names and paths (e.g. `file:owner/name:src/app.py`).
    """
    org: Org = await get_org(org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
        )
    is_member = any(m.userId == user.id for m in org.members)
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this org",
        )

    detail = await get_node_detail(org_id, node_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Node not found"
        )
    return detail
