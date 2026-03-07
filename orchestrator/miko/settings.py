from __future__ import annotations
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Postgres
    pg_host: str = "master-postgres"
    pg_port: int = 5432
    pg_user: str = "awaas_master"
    pg_password: str
    pg_database: str = "awaas_master"

    # Auth
    miko_api_key: str

    # Ollama
    ollama_url: str = "http://host-gateway:11434"
    chat_model: str = "qwen3.5:4b"

    # Qdrant (for Mem0)
    qdrant_url: str = "http://awaas-qdrant:6333"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
