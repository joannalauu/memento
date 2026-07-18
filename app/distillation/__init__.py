"""Session↔PR matching + distillation.

Consumes the PipelineJobs the GitHub webhook enqueues for merged PRs, matches
banked agentSessions by repo + head branch, and turns transcripts + PR context
+ prior memories into structured decision records for T3.3 to persist.
"""

from app.distillation.distill import distill
from app.distillation.matching import match_sessions
from app.distillation.pipeline import run_pipeline_job
from app.distillation.schemas import (
    DistillationOutput,
    DistillationResult,
    StaleMemoryFlag,
)
from app.distillation.worker import worker_enabled, worker_loop

__all__ = [
    "DistillationOutput",
    "DistillationResult",
    "StaleMemoryFlag",
    "distill",
    "match_sessions",
    "run_pipeline_job",
    "worker_enabled",
    "worker_loop",
]
