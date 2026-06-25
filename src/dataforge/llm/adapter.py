"""LlmAdapter — the single interface DataForge uses for all LLM calls.

Only two operations are ever needed:
  1. complete()        — plain text in, plain text out. Used by HclGenerator to
                         polish the Terraform skeleton with a Claude/GPT review pass.
  2. extract_json()    — structured output using tool/function calling. Used by
                         IntentParser to turn a NL description into a FlowGraph dict.

Adding a new provider means implementing these two methods and registering the
provider name in build_adapter(). Nothing else changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from dataforge.config import Settings


class LlmAdapter(ABC):
    """Abstract base — all LLM providers implement these two methods."""

    @abstractmethod
    def complete(self, system: str, messages: list[dict]) -> str:
        """Send a chat conversation and return the assistant's text reply.

        Args:
            system:   The system prompt (instructions for the model).
            messages: List of {"role": "user"|"assistant", "content": str} dicts.

        Returns:
            The model's reply as a plain string.
        """
        ...

    @abstractmethod
    def extract_json(
        self,
        system: str,
        messages: list[dict],
        schema: dict,
        tool_name: str,
    ) -> dict:
        """Force the model to output a JSON object matching `schema`.

        Uses tool/function calling so the model MUST return structured JSON
        rather than free-form text. Both Anthropic and OpenAI support this.

        Args:
            system:    System prompt describing the extraction task.
            messages:  Conversation history (same format as complete()).
            schema:    A JSON Schema dict describing the required output shape.
            tool_name: Name of the tool/function the model should call.

        Returns:
            A dict that conforms to `schema` (not yet validated by Pydantic).

        Raises:
            ValueError: If the model doesn't call the tool / returns no JSON.
        """
        ...


def build_adapter(settings: Settings) -> LlmAdapter:
    """Create the right LlmAdapter for the configured provider.

    Reads settings.llm_provider and constructs the matching adapter.
    All provider-specific config (API keys, model names, token limits,
    custom endpoints) lives in Settings and is passed into the adapter here.

    Supported providers:
        anthropic              — Anthropic Claude (default)
        openai                 — OpenAI GPT-4o / GPT-4-turbo
        azure_openai           — Azure OpenAI (same SDK, different base URL)
        groq                   — Groq (ultra-fast inference, OpenAI-compatible)
        ollama                 — Local Ollama models (OpenAI-compatible at localhost)
        mistral                — Mistral AI (OpenAI-compatible)
        together               — Together AI (OpenAI-compatible)

    Raises:
        ValueError: If the required API key is missing for the chosen provider.
        ValueError: If an unknown provider name is given.
    """
    # Import here to avoid circular imports; both modules import Settings.
    from dataforge.llm.anthropic_adapter import AnthropicAdapter
    from dataforge.llm.openai_adapter import OpenAiAdapter

    provider = settings.llm_provider.lower()

    # ── Anthropic (default) ───────────────────────────────────────────────────
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError(
                "LLM_PROVIDER=anthropic but DATAFORGE_ANTHROPIC_API_KEY is not set.\n"
                "  • Set it in your environment: export DATAFORGE_ANTHROPIC_API_KEY=sk-ant-...\n"
                "  • Or switch providers:        export DATAFORGE_LLM_PROVIDER=openai"
            )
        return AnthropicAdapter(settings)

    # ── OpenAI-compatible providers (openai, groq, ollama, mistral, etc.) ────
    _OPENAI_COMPATIBLE = {"openai", "azure_openai", "groq", "ollama", "mistral", "together"}
    if provider in _OPENAI_COMPATIBLE:
        if not settings.openai_api_key:
            raise ValueError(
                f"LLM_PROVIDER={provider} but DATAFORGE_OPENAI_API_KEY is not set.\n"
                "  • Set it:  export DATAFORGE_OPENAI_API_KEY=sk-...\n"
                "  • Ollama needs any non-empty string, e.g. DATAFORGE_OPENAI_API_KEY=ollama"
            )
        return OpenAiAdapter(settings)

    raise ValueError(
        f"Unknown LLM provider: {provider!r}\n"
        f"Valid options: anthropic, openai, azure_openai, groq, ollama, mistral, together"
    )
