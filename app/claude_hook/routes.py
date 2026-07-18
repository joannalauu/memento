# NOTE: the ingest endpoint is not built yet (out of scope for now).
#
# When implemented, `POST /ingest/agent-sessions` MUST UPSERT on sessionId,
# not insert-once. The @memento/hook client sends the full transcript on every
# SessionEnd; a resumed session re-fires with a longer transcript and the same
# sessionId. On a match, replace transcriptRef (garbage-collect the old blob),
# reset status -> "stored", clear normalizedRef, re-set expiresAt (+14d), and
# stamp updatedAt. See app/claude_hook/models.py::AgentSession.
#
# Auth: resolve userId + orgId by hashing the Bearer key and looking it up in
# app/api_auth/models.py::ApiKey (keyHash). repoId is resolved from X-Git-Remote.
