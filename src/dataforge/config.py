"""Runtime settings — loaded from environment variables or a .env file.

Every setting below can be overridden by an environment variable named
DATAFORGE_<FIELD_NAME_UPPERCASE>. For example:
    export DATAFORGE_LLM_PROVIDER=openai
    export DATAFORGE_OPENAI_API_KEY=sk-...

A .env file in the working directory is loaded automatically.

Quick-start (Anthropic — default):
    DATAFORGE_ANTHROPIC_API_KEY=sk-ant-...

Quick-start (OpenAI):
    DATAFORGE_LLM_PROVIDER=openai
    DATAFORGE_OPENAI_API_KEY=sk-...

Quick-start (Groq — free tier, very fast):
    DATAFORGE_LLM_PROVIDER=groq
    DATAFORGE_OPENAI_API_KEY=gsk_...
    DATAFORGE_OPENAI_BASE_URL=https://api.groq.com/openai/v1
    DATAFORGE_OPENAI_PARSE_MODEL=llama-3.3-70b-versatile

Quick-start (Ollama — fully local, no API key needed):
    DATAFORGE_LLM_PROVIDER=ollama
    DATAFORGE_OPENAI_API_KEY=ollama        # any non-empty string
    DATAFORGE_OPENAI_BASE_URL=http://localhost:11434/v1
    DATAFORGE_OPENAI_PARSE_MODEL=llama3.2
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATAFORGE_",     # All env vars are prefixed DATAFORGE_
        env_file=".env",              # Load .env in the working directory
        env_file_encoding="utf-8",
        extra="ignore",               # Ignore unknown env vars silently
    )

    # ── Provider selection ────────────────────────────────────────────────────
    # Which LLM backend to use. Valid values:
    #   anthropic   — Anthropic Claude (Haiku for parsing, Sonnet for polish)
    #   openai      — OpenAI GPT models
    #   azure_openai— Azure-hosted OpenAI models
    #   groq        — Groq cloud (fast, OpenAI-compatible)
    #   ollama      — Local Ollama models (no API key, runs on your machine)
    #   mistral     — Mistral AI (OpenAI-compatible)
    #   together    — Together AI (OpenAI-compatible)
    llm_provider: str = "anthropic"

    # ── Anthropic credentials ─────────────────────────────────────────────────
    # Required when llm_provider="anthropic". Get a key at console.anthropic.com.
    # SecretStr means the value won't be printed in logs or tracebacks.
    anthropic_api_key: SecretStr | None = None

    # Which Claude models to use for each task.
    # parse_model:    Fast model for NL → FlowGraph extraction (cheaper).
    # generate_model: Best-quality model for Terraform polish pass (slower).
    parse_model: str = "claude-haiku-4-5-20251001"
    generate_model: str = "claude-sonnet-4-6"

    # ── OpenAI-compatible credentials ─────────────────────────────────────────
    # Required when llm_provider is openai/groq/ollama/mistral/etc.
    openai_api_key: SecretStr | None = None

    # Optional custom endpoint. Leave unset for OpenAI. Examples:
    #   Groq:    https://api.groq.com/openai/v1
    #   Ollama:  http://localhost:11434/v1
    #   Azure:   https://<resource>.openai.azure.com/
    openai_base_url: str | None = None

    # Which model to use for each task on OpenAI-compatible providers.
    # gpt-4o is the default — works for both tasks.
    openai_parse_model: str = "gpt-4o"
    openai_generate_model: str = "gpt-4o"

    # ── Shared limits ─────────────────────────────────────────────────────────
    # Maximum output tokens for each operation. Increase if you get truncated output.
    max_tokens_parse: int = 2048      # FlowGraph extraction rarely exceeds ~1k tokens
    max_tokens_generate: int = 8192   # Terraform polish needs more room

    # Where to write generated files when using `dataforge generate`.
    output_dir: Path = Path("./output")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (constructed once, cached forever).

    Uses lru_cache so settings are only loaded from the environment once
    per process. If you need to reload settings in tests, call
    get_settings.cache_clear() first.
    """
    return Settings()  # type: ignore[call-arg]
