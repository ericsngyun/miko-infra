"""
Custom Mem0 embedder that routes to llama-server's OpenAI-compatible
/v1/embeddings endpoint. Bypasses the Ollama provider requirement
while keeping Mem0's fact extraction and dedup pipeline intact.
"""
import os
import httpx
from mem0.embeddings.base import EmbeddingBase

LLAMA_SERVER_URL = os.getenv("LLAMA_SERVER_URL", "http://100.73.88.88:11435")
EMBED_MODEL = "nomic-embed-text"
EMBED_DIMS = 768


class LlamaServerEmbedder(EmbeddingBase):
    def __init__(self, config=None):
        super().__init__(config)
        self.base_url = LLAMA_SERVER_URL
        self.dims = EMBED_DIMS

    def embed(self, text: str) -> list[float]:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{self.base_url}/v1/embeddings",
                json={"model": EMBED_MODEL, "input": text},
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]

    @property
    def dims(self) -> int:
        return EMBED_DIMS

    @dims.setter
    def dims(self, value):
        self._dims = value
