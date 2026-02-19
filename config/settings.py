"""
Centralized settings reader for Cloud Cost Optimizer.

Reads all configuration from environment variables (loaded from .env file).
Import the `settings` singleton from anywhere in the project:

    from config.settings import settings
    print(settings.INFLUX_URL)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


def _env(key: str, default: str = "") -> str:
    """Read an environment variable with an optional default."""
    return os.getenv(key, default)


@dataclass(frozen=True)
class Settings:
    """Immutable application settings populated from environment variables."""

    # ── AWS ─────────────────────────────────────────────
    AWS_ACCESS_KEY_ID: str = field(default_factory=lambda: _env("AWS_ACCESS_KEY_ID"))
    AWS_SECRET_ACCESS_KEY: str = field(default_factory=lambda: _env("AWS_SECRET_ACCESS_KEY"))
    AWS_DEFAULT_REGION: str = field(default_factory=lambda: _env("AWS_DEFAULT_REGION", "us-east-1"))

    # ── InfluxDB ────────────────────────────────────────
    INFLUX_URL: str = field(default_factory=lambda: _env("INFLUX_URL", "http://localhost:8086"))
    INFLUX_TOKEN: str = field(default_factory=lambda: _env("INFLUX_TOKEN"))
    INFLUX_ORG: str = field(default_factory=lambda: _env("INFLUX_ORG"))
    INFLUX_BUCKET: str = field(default_factory=lambda: _env("INFLUX_BUCKET", "cloud-costs"))

    # ── Pinecone ────────────────────────────────────────
    PINECONE_API_KEY: str = field(default_factory=lambda: _env("PINECONE_API_KEY"))
    PINECONE_ENVIRONMENT: str = field(default_factory=lambda: _env("PINECONE_ENVIRONMENT", "us-east-1-aws"))
    PINECONE_INDEX_NAME: str = field(default_factory=lambda: _env("PINECONE_INDEX_NAME", "cost-optimization"))

    # ── Anthropic (Claude) ──────────────────────────────
    ANTHROPIC_API_KEY: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))

    # ── GitHub ──────────────────────────────────────────
    GITHUB_TOKEN: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    GITHUB_REPO: str = field(default_factory=lambda: _env("GITHUB_REPO"))

    # ── Slack ───────────────────────────────────────────
    SLACK_WEBHOOK_URL: str = field(default_factory=lambda: _env("SLACK_WEBHOOK_URL"))


# Singleton — import this everywhere
settings = Settings()
