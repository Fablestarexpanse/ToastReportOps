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
    llm_model: str = "qwen3.5:9b"  # official library tag; pinned in model_manifest.json
    llm_api_key: str = "unused-for-local"
    llm_timeout_s: float = 300.0
    # qwen3 and similar reasoning models default to slow "thinking"; the soft
    # switch below is appended to the system prompt to skip it for extraction.
    llm_no_think_token: str = "/no_think"
    # ...but that blanket suppression was chosen for EXTRACTION and then
    # inherited by every call. Extraction is transcription — "pull the acreage
    # out of this sentence" — and reasoning there is pure latency on the hottest
    # path in the system. The JUDGMENT stages are different: watch classification
    # (new fire or old news?), activity checks (is this fire still burning?) and
    # auto-triage (does this fact belong to that incident?) all weigh conflicting
    # evidence, which is what reasoning is actually for.
    #
    # Set false to force reasoning off everywhere — if a model ignores /no_think,
    # or if judgment latency starts backing the job queue up.
    llm_reasoning_on_judgment: bool = True

    # embeddings
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768

    # operator API
    api_bind: str = "127.0.0.1"
    api_port: int = 8400
    # extra hostnames accepted in Host/Origin besides loopback (comma-separated);
    # `api` is the compose service name
    api_allowed_hosts: str = "api"

    # per-service health endpoint (workers/scheduler); 0 disables
    health_port: int = 0

    # worker
    worker_poll_interval_s: float = 1.0
    job_backoff_base_s: float = 5.0

    # active research layer (Local Deep Research)
    ldr_url: str = "http://ldr:5000"
    ldr_username: str = "toast-operator"
    ldr_password: str = "change-me-local-research"

    # ---- external service credentials ----
    # All optional. A capability is active only when its credentials exist.
    # Operator-entered values (OPTIONS → API KEYS) override these at runtime;
    # see toast/core/credentials.py.
    firms_map_key: str = ""
    airnow_api_key: str = ""
    # local OpenAI-compatible Whisper endpoint (faster-whisper-server / speaches);
    # empty = radio calls stay visibly 'pending', never fabricated
    whisper_url: str = ""
    whisper_model: str = "Systran/faster-whisper-small"

    # Telegram: one bot serves two rooms — a public channel we PUBLISH to, and
    # a private ops chat that receives alerts and can approve drafts. The ops
    # chat id is the auth boundary: commands from any other chat are ignored.
    telegram_bot_token: str = ""
    telegram_channel_id: str = ""
    telegram_ops_chat_id: str = ""
    bluesky_handle: str = ""
    bluesky_app_password: str = ""
    x_api_key: str = ""
    x_api_secret: str = ""
    x_access_token: str = ""
    x_access_secret: str = ""
    fb_page_id: str = ""
    fb_page_token: str = ""

    # offsite backups (Backblaze B2) — snapshots stay local-only until these
    # are set; see docs/BACKUP-RESTORE.md
    b2_key_id: str = ""
    b2_application_key: str = ""
    b2_bucket: str = ""

    # logging
    log_level: str = "INFO"

    @property
    def raw_dir(self) -> Path:
        return self.toast_data_dir / "raw"

    @property
    def site_dir(self) -> Path:
        return self.toast_data_dir / "site"


def load_settings() -> Settings:
    return Settings()
