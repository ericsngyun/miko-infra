"""
Async job submission endpoint.

POST /queue — submit a job for async processing.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from fastapi import APIRouter

from models.payloads import QueuePayload, QueueResult

router = APIRouter()


@router.post("/queue", response_model=QueueResult)
async def submit_job(payload: QueuePayload) -> QueueResult:
    """
    Submit a job for asynchronous processing.

    Enqueues the job in Redis and returns a job ID for status tracking.

    Args:
        payload: QueuePayload with job type, payload, and organization ID.

    Returns:
        QueueResult with job ID and queued status.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")
