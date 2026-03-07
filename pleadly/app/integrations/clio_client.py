"""
Clio API wrapper — OAuth token management and matter read/write operations.

Handles token refresh, encrypted token storage, and CRUD operations against
the Clio API for matters, documents, contacts, and tasks.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from typing import Any


class ClioClient:
    """
    Async client for the Clio Manage API.

    Handles OAuth token lifecycle (refresh, encrypt/decrypt) and
    provides methods for common Clio operations.
    """

    def __init__(
        self,
        *,
        organization_id: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        """
        Initialize the Clio client.

        Args:
            organization_id: Pleadly organization ID for token lookup.
            client_id: Clio OAuth client ID.
            client_secret: Clio OAuth client secret.
            redirect_uri: OAuth callback URL.
        """
        raise NotImplementedError("TODO: implement in Stage 1")

    async def refresh_token(self) -> str:
        """
        Refresh the OAuth access token using the stored refresh token.

        Returns:
            The new access token.

        Raises:
            NotImplementedError: This is a stub.
        """
        raise NotImplementedError("TODO: implement in Stage 1")

    async def get_matter(self, matter_id: str) -> dict[str, Any]:
        """
        Fetch a matter by ID from Clio.

        Args:
            matter_id: The Clio matter ID.

        Returns:
            Matter data dict.

        Raises:
            NotImplementedError: This is a stub.
        """
        raise NotImplementedError("TODO: implement in Stage 1")

    async def list_matters(
        self,
        *,
        status: str = "open",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        List matters from Clio with pagination.

        Args:
            status: Filter by matter status.
            limit: Number of results per page.
            offset: Pagination offset.

        Returns:
            List of matter dicts.

        Raises:
            NotImplementedError: This is a stub.
        """
        raise NotImplementedError("TODO: implement in Stage 1")

    async def upload_document(
        self,
        *,
        matter_id: str,
        file_name: str,
        content: bytes,
        content_type: str = "application/pdf",
    ) -> dict[str, Any]:
        """
        Upload a document to a Clio matter.

        Args:
            matter_id: The Clio matter ID.
            file_name: Name for the uploaded file.
            content: File content bytes.
            content_type: MIME type of the file.

        Returns:
            Clio document metadata dict.

        Raises:
            NotImplementedError: This is a stub.
        """
        raise NotImplementedError("TODO: implement in Stage 1")

    async def create_task(
        self,
        *,
        matter_id: str,
        name: str,
        due_date: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a task on a Clio matter.

        Args:
            matter_id: The Clio matter ID.
            name: Task name.
            due_date: Optional due date (ISO format).
            description: Optional task description.

        Returns:
            Clio task metadata dict.

        Raises:
            NotImplementedError: This is a stub.
        """
        raise NotImplementedError("TODO: implement in Stage 1")
