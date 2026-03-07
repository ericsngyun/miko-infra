"""
Stage 5: Delivery Adapter — route output to Clio or standalone delivery.

Handles the final delivery of generated documents, either pushing them
to a connected CMS (Clio, Filevine, MyCase) or storing them for
standalone download.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from typing import Any, Literal


async def deliver_document(
    *,
    document_content: str,
    document_type: str,
    case_id: str,
    organization_id: str,
    delivery_mode: Literal["standalone", "clio", "filevine", "mycase"],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Deliver a generated document to the appropriate destination.

    Args:
        document_content: The document text/HTML content.
        document_type: Type of document (demand_letter, discovery, chronology, etc.).
        case_id: The case this document belongs to.
        organization_id: The organization that owns the case.
        delivery_mode: Where to deliver the document.
        metadata: Optional metadata (title, version, etc.).

    Returns:
        Dict with delivery status, document_id, and any CMS-specific IDs.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")


async def push_to_clio(
    *,
    document_content: str,
    matter_id: str,
    document_type: str,
    organization_id: str,
) -> dict[str, Any]:
    """
    Push a document to Clio as a matter document.

    Args:
        document_content: The document content.
        matter_id: Clio matter ID.
        document_type: Type of document.
        organization_id: Organization with Clio integration.

    Returns:
        Dict with Clio document ID and status.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")
