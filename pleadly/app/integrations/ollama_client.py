"""
llama-server API client for chat completions.

Targets Lemonade llamacpp-rocm (b1195+) running on port 11435.
Uses /v1/chat/completions (OpenAI-compatible) wire format.
Supports qwen3.5-35b-a3b (primary) and qwen3.5-2b (classifier).
"""

from __future__ import annotations

import asyncio
import os
import json
import logging
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("pleadly.ollama")

# Model aliases — updated for Qwen3.5 stack
# llama-server ignores the model field, these are labels only
MODEL_PRIMARY    = "qwen3.5-35b-a3b"
MODEL_CLASSIFIER = "qwen3.5-2b"

# llama-server base URL (v1 prefix for OpenAI-compat)
_RAW_URL = os.getenv("OLLAMA_URL", "http://localhost:11435")
DEFAULT_BASE_URL = _RAW_URL.rstrip("/")

# Retry configuration
MAX_RETRIES     = 3
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY  = 30.0


class OllamaError(Exception):
    """Raised when llama-server returns an error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OllamaClient:
    """
    Async wrapper around llama-server's OpenAI-compatible HTTP API.

    Usage:
        client = OllamaClient()
        result = await client.chat("Summarize this document", model=MODEL_PRIMARY)
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        default_timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_timeout = default_timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.default_timeout, connect=10.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        """Check if llama-server is reachable."""
        try:
            client = await self._get_client()
            response = await client.get("/health", timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[dict[str, Any]]:
        """List models via OpenAI-compat endpoint."""
        try:
            client = await self._get_client()
            response = await client.get("/v1/models")
            if response.status_code == 200:
                return response.json().get("data", [])
        except Exception:
            pass
        return []

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        """
        Extract clean content from /v1/chat/completions response.
        reasoning_content (thinking tokens) is intentionally discarded —
        Pleadly pipeline only needs the final output.
        """
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as exc:
            raise OllamaError(f"Unexpected response structure: {exc}")

    async def chat(
        self,
        prompt: str,
        *,
        model: str = MODEL_PRIMARY,
        system: str | None = None,
        json_mode: bool = False,
        timeout: float | None = None,
        temperature: float = 0.6,
        max_retries: int = MAX_RETRIES,
        think: bool = False,
    ) -> str:
        """
        Send a chat completion request to llama-server.

        Args:
            prompt: The user message.
            model: Model label (informational only — llama-server ignores it).
            system: Optional system prompt.
            think: If True, enables Qwen3.5 reasoning mode for this request.
                   Default False (thinking disabled globally at server level).
                   Set True only for Legal Planner weakness analysis pass.
            json_mode: If True, request JSON output format.
            timeout: Per-request timeout in seconds.
            temperature: Sampling temperature (default 0.6 per Qwen3 recommendation).
            max_retries: Retry count on transient failures.

        Returns:
            The assistant's response text (thinking tokens stripped).

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
            "temperature": temperature,
        }
        if json_mode:
            request_body["response_format"] = {"type": "json_object"}
        if think:
            request_body["chat_template_kwargs"] = {"enable_thinking": True}

        request_timeout = timeout or self.default_timeout
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                client = await self._get_client()
                response = await client.post(
                    "/v1/chat/completions",
                    json=request_body,
                    timeout=request_timeout,
                )

                if response.status_code != 200:
                    raise OllamaError(
                        f"llama-server returned status {response.status_code}: "
                        f"{response.text[:200]}",
                        status_code=response.status_code,
                    )

                return self._extract_content(response.json())

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    delay = min(RETRY_BASE_DELAY * (2**attempt), RETRY_MAX_DELAY)
                    logger.warning(
                        "llama-server request attempt %d/%d failed (timeout/connect), "
                        "retrying in %.1fs: %s",
                        attempt + 1, max_retries, delay, exc,
                    )
                    await asyncio.sleep(delay)
            except OllamaError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    delay = min(RETRY_BASE_DELAY * (2**attempt), RETRY_MAX_DELAY)
                    logger.warning(
                        "llama-server request attempt %d/%d failed, retrying in %.1fs: %s",
                        attempt + 1, max_retries, delay, exc,
                    )
                    await asyncio.sleep(delay)

        raise OllamaError(
            f"llama-server request failed after {max_retries} attempts: {last_error}"
        )

    async def chat_json(
        self,
        prompt: str,
        *,
        model: str = MODEL_PRIMARY,
        system: str | None = None,
        timeout: float | None = None,
        temperature: float = 0.6,
    ) -> dict[str, Any]:
        """
        Send a chat request and parse the response as JSON.

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
        # Strip markdown code fences if model wraps JSON despite json_mode
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```", 2)[1]
            if clean.startswith("json"):
                clean = clean[4:]
            clean = clean.rsplit("```", 1)[0].strip()
        return json.loads(clean)

    async def chat_stream(
        self,
        prompt: str,
        *,
        model: str = MODEL_PRIMARY,
        system: str | None = None,
        timeout: float | None = None,
        temperature: float = 0.6,
    ) -> AsyncIterator[str]:
        """
        Send a streaming chat completion request.

        Yields:
            Content tokens as they arrive (thinking tokens skipped).

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
            "temperature": temperature,
        }

        client = await self._get_client()
        in_thinking = False

        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json=request_body,
            timeout=timeout or self.default_timeout,
        ) as response:
            if response.status_code != 200:
                raise OllamaError(
                    f"llama-server returned status {response.status_code}",
                    status_code=response.status_code,
                )

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    delta = chunk["choices"][0].get("delta", {})

                    # Skip reasoning_content tokens entirely
                    if delta.get("reasoning_content"):
                        continue

                    content = delta.get("content", "")
                    if not content:
                        continue

                    # Guard against thinking tokens leaking into content stream
                    if "<think>" in content:
                        in_thinking = True
                    if in_thinking:
                        if "</think>" in content:
                            in_thinking = False
                        continue

                    yield content
                except (json.JSONDecodeError, KeyError):
                    continue
