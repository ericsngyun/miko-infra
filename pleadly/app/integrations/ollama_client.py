"""
Ollama API client for chat completions.

Supports qwen3:30b-a3b (primary) and qwen2.5:1.5b (classifier/grader).
Uses httpx for HTTP calls with retry logic and timeout handling.
"""

from __future__ import annotations

import asyncio
import os
import json
import logging
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("pleadly.ollama")

# Model aliases
MODEL_PRIMARY = "qwen3:30b-a3b"
MODEL_CLASSIFIER = "qwen3:1.7b"

# Default Ollama API endpoint
DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# Retry configuration
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds
RETRY_MAX_DELAY = 30.0  # seconds


class OllamaError(Exception):
    """Raised when Ollama API returns an error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OllamaClient:
    """
    Async wrapper around the Ollama HTTP API.

    Usage:
        client = OllamaClient()
        result = await client.chat("Summarize this document", model=MODEL_PRIMARY)
    """

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        default_timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_timeout = default_timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily initialize the httpx async client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.default_timeout, connect=10.0),
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        """
        Check if Ollama is reachable and responding.

        Returns:
            True if Ollama is healthy, False otherwise.
        """
        try:
            client = await self._get_client()
            response = await client.get("/api/tags", timeout=5.0)
            return response.status_code == 200
        except (httpx.HTTPError, Exception):
            return False

    async def list_models(self) -> list[dict[str, Any]]:
        """List available models on the Ollama instance."""
        client = await self._get_client()
        response = await client.get("/api/tags")
        response.raise_for_status()
        data = response.json()
        return data.get("models", [])

    async def chat(
        self,
        prompt: str,
        *,
        model: str = MODEL_PRIMARY,
        system: str | None = None,
        json_mode: bool = False,
        timeout: float | None = None,
        temperature: float = 0.1,
        max_retries: int = MAX_RETRIES,
    ) -> str:
        """
        Send a chat completion request to Ollama.

        Args:
            prompt: The user message.
            model: Model name (default: qwen3:30b-a3b).
            system: Optional system prompt.
            json_mode: If True, request JSON output format.
            timeout: Per-request timeout in seconds (overrides default).
            temperature: Sampling temperature.
            max_retries: Number of retries on transient failures.

        Returns:
            The assistant's response text.

        Raises:
            OllamaError: If the request fails after all retries.
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        request_body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }
        if json_mode:
            request_body["format"] = "json"

        request_timeout = timeout or self.default_timeout
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                client = await self._get_client()
                response = await client.post(
                    "/api/chat",
                    json=request_body,
                    timeout=request_timeout,
                )

                if response.status_code != 200:
                    error_text = response.text
                    # Don't log error_text as it may contain PII echoed back
                    raise OllamaError(
                        f"Ollama returned status {response.status_code}",
                        status_code=response.status_code,
                    )

                data = response.json()
                content = data.get("message", {}).get("content", "")
                return content

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    delay = min(
                        RETRY_BASE_DELAY * (2**attempt),
                        RETRY_MAX_DELAY,
                    )
                    logger.warning(
                        "Ollama request attempt %d/%d failed (timeout/connect), "
                        "retrying in %.1fs",
                        attempt + 1,
                        max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
            except OllamaError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    delay = min(
                        RETRY_BASE_DELAY * (2**attempt),
                        RETRY_MAX_DELAY,
                    )
                    logger.warning(
                        "Ollama request attempt %d/%d failed, retrying in %.1fs",
                        attempt + 1,
                        max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)

        raise OllamaError(
            f"Ollama request failed after {max_retries} attempts: {last_error}"
        )

    async def chat_json(
        self,
        prompt: str,
        *,
        model: str = MODEL_PRIMARY,
        system: str | None = None,
        timeout: float | None = None,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """
        Send a chat request and parse the response as JSON.

        Convenience wrapper around chat() with json_mode=True.

        Returns:
            Parsed JSON dictionary.

        Raises:
            OllamaError: If the request fails.
            json.JSONDecodeError: If the response is not valid JSON.
        """
        raw = await self.chat(
            prompt,
            model=model,
            system=system,
            json_mode=True,
            timeout=timeout,
            temperature=temperature,
        )
        return json.loads(raw)

    async def chat_stream(
        self,
        prompt: str,
        *,
        model: str = MODEL_PRIMARY,
        system: str | None = None,
        timeout: float | None = None,
        temperature: float = 0.1,
    ) -> AsyncIterator[str]:
        """
        Send a streaming chat completion request to Ollama.

        Yields:
            Content tokens as they arrive.

        Raises:
            OllamaError: If the request fails.
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        request_body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
            },
        }

        request_timeout = timeout or self.default_timeout

        client = await self._get_client()
        async with client.stream(
            "POST",
            "/api/chat",
            json=request_body,
            timeout=request_timeout,
        ) as response:
            if response.status_code != 200:
                raise OllamaError(
                    f"Ollama returned status {response.status_code}",
                    status_code=response.status_code,
                )

            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue
