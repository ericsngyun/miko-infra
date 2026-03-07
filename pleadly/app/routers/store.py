"""
Content storage endpoint.

POST /store — write content to the Intelligence Plane's PostgreSQL database.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from fastapi import APIRouter

from models.payloads import StorePayload

router = APIRouter()


@router.post("/store")
async def store_content(payload: StorePayload) -> dict[str, bool]:
    """
    Write content to the Intelligence Plane's PostgreSQL database.

    Args:
        payload: StorePayload with table name, data, and organization ID.

    Returns:
        Dict with ok=True on success.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")
