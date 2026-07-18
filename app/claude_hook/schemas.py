from typing import Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict


class AgentSessionIngestAccepted(BaseModel):
    """202 response for a stored (or re-ingested) agent session. Minimal on
    purpose — the hook client only checks the status code, never the body."""

    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId
    sessionId: str
    status: Literal["stored"]
