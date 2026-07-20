"""Configuration — environment variables only, no hardcoded paths.

Every service constructs Settings() at startup; .env is read when present
(desktop dev), plain environment otherwise (Docker/server).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # core paths
    toast_data_dir: Path = Path("./data")
    toast_db_path: Path = Path("./data/toast.db")

    # LLM (OpenAI-compatible local endpoint)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen3"
    llm_api_key: str = "unused-for-local"
    llm_timeout_s: float = 300.0
    # qwen3 and similar reasoning models default to slow "thinking"; the soft
    # switch below is appended to the system prompt to skip it for extraction.
    llm_no_think_token: str = "/no_think"

    # embeddings
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768

    # operator API
    api_bind: str = "127.0.0.1"
    api_port: int = 8400

    # per-service health endpoint (workers/scheduler); 0 disables
    health_port: int = 0

    # worker
    worker_poll_interval_s: float = 1.0
    job_backoff_base_s: float = 5.0

    # logging
    log_level: str = "INFO"

    @property
    def raw_dir(self) -> Path:
        return self.toast_data_dir / "raw"


def load_settings() -> Settings:
    return Settings()
