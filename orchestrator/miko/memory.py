from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
import httpx

logger = logging.getLogger("miko.memory")

COLLECTION = "miko_memory"
EMBEDDING_DIM = 768
EMBED_MODEL = "nomic-embed-text"


def _get_client() -> QdrantClient:
    return QdrantClient(
        host="awaas-qdrant",
        port=6333,
        api_key=os.getenv("QDRANT_API_KEY", ""),
        https=False,
        prefer_grpc=False,
        check_compatibility=False,
    )


def _ensure_collection(client: QdrantClient) -> None:
    cols = [c.name for c in client.get_collections().collections]
    if COLLECTION not in cols:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        logger.info("Created collection %s", COLLECTION)


def _embed(text: str, ollama_url: str) -> list[float]:
    resp = httpx.post(
        f"{ollama_url}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def search_memories(query: str, user_id: str, ollama_url: str, limit: int = 5) -> list[dict[str, Any]]:
    try:
        client = _get_client()
        _ensure_collection(client)
        vec = _embed(query, ollama_url)
        results = client.search(
            collection_name=COLLECTION,
            query_vector=vec,
            limit=limit,
            query_filter={"must": [{"key": "user_id", "match": {"value": user_id}}]},
            with_payload=True,
        )
        return [{"text": r.payload.get("text", ""), "score": r.score} for r in results]
    except Exception as exc:
        logger.warning("Memory search failed: %s", exc)
        return []


def store_memory(user_id: str, user_msg: str, assistant_msg: str, ollama_url: str) -> None:
    try:
        client = _get_client()
        _ensure_collection(client)
        text = f"User: {user_msg}\nMiko: {assistant_msg}"
        vec = _embed(text, ollama_url)
        client.upsert(
            collection_name=COLLECTION,
            points=[PointStruct(
                id=str(uuid.uuid4()),
                vector=vec,
                payload={
                    "user_id": user_id,
                    "text": text,
                    "ts": int(time.time()),
                },
            )],
        )
    except Exception as exc:
        logger.warning("Memory store failed: %s", exc)
