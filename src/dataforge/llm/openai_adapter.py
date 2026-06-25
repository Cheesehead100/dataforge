"""OpenAiAdapter — wraps the OpenAI Python SDK.

One SDK, many providers. The OpenAI SDK supports custom base URLs, so the
same adapter covers:

    Provider         DATAFORGE_OPENAI_BASE_URL (leave empty for OpenAI)
    ──────────────   ─────────────────────────────────────────────────────
    OpenAI           (default — no base URL needed)
    Azure OpenAI     https://<resource>.openai.azure.com/
    Groq             https://api.groq.com/openai/v1
    Mistral AI       https://api.mistral.ai/v1
    Together AI      https://api.together.xyz/v1
    Ollama (local)   http://localhost:11434/v1

For Ollama, set DATAFORGE_OPENAI_API_KEY=ollama (any non-empty string works).

Tool/function calling works the same way across all providers that support it.
For providers that don't support tool calling, `extract_json` will raise —
fall back to a model that does (e.g. llama3-70b on Groq supports tools).
"""

from __future__ import annotations

import json
import logging

from dataforge.config import Settings
from dataforge.llm.adapter import LlmAdapter

logger = logging.getLogger(__name__)


class OpenAiAdapter(LlmAdapter):
    """LlmAdapter implementation backed by any OpenAI-compatible endpoint."""

    def __init__(self, settings: Settings) -> None:
        # Lazy import: openai is an optional dependency (pip install dataforge[openai]).
        # This means the core DataForge library doesn't require openai to be installed
        # unless the user actually chooses an OpenAI-compatible provider.
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "The openai package is required for non-Anthropic providers.\n"
                "Install it with:  pip install 'dataforge[openai]'"
            ) from exc

        api_key = settings.openai_api_key.get_secret_value()  # type: ignore[union-attr]

        # Build the OpenAI client. If a custom base_url is set (e.g. for Groq or Ollama),
        # all requests go to that endpoint instead of api.openai.com.
        client_kwargs: dict = {"api_key": api_key}
        if settings.openai_base_url:
            client_kwargs["base_url"] = settings.openai_base_url

        self._client = OpenAI(**client_kwargs)
        self._parse_model = settings.openai_parse_model
        self._generate_model = settings.openai_generate_model
        self._max_tokens_parse = settings.max_tokens_parse
        self._max_tokens_generate = settings.max_tokens_generate

    # ── Plain text generation ─────────────────────────────────────────────────

    def complete(self, system: str, messages: list[dict]) -> str:
        """Send a chat conversation and return the assistant's reply.

        OpenAI doesn't have a separate 'system' parameter — the system prompt
        is just a message with role='system' prepended to the conversation.
        """
        # Combine system prompt + conversation into a single messages list.
        full_messages = [{"role": "system", "content": system}] + messages

        response = self._client.chat.completions.create(
            model=self._generate_model,
            max_tokens=self._max_tokens_generate,
            messages=full_messages,  # type: ignore[arg-type]
        )
        return response.choices[0].message.content or ""

    # ── Structured JSON extraction ────────────────────────────────────────────

    def extract_json(
        self,
        system: str,
        messages: list[dict],
        schema: dict,
        tool_name: str,
    ) -> dict:
        """Force the model to output JSON matching `schema` via function calling.

        OpenAI function calling format differs from Anthropic's, but the effect
        is the same: the model must produce structured output matching the schema
        rather than free text. The tool is defined as a 'function' inside a
        'tools' list with type='function'.

        tool_choice forces the model to call the specific function every time,
        matching the Anthropic behavior of tool_choice={"type": "any"}.
        """
        # OpenAI wraps the JSON Schema inside a "function" object.
        tool_def = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": f"Extract and output the structured {tool_name} from the text.",
                "parameters": schema,   # Same JSON Schema dict Anthropic uses
            },
        }

        # Force this specific function to be called (not the model's choice).
        tool_choice = {"type": "function", "function": {"name": tool_name}}

        full_messages = [{"role": "system", "content": system}] + messages

        response = self._client.chat.completions.create(
            model=self._parse_model,
            max_tokens=self._max_tokens_parse,
            tools=[tool_def],          # type: ignore[list-item]
            tool_choice=tool_choice,   # type: ignore[arg-type]
            messages=full_messages,    # type: ignore[arg-type]
        )

        # Navigate the response to find the tool call result.
        # choices[0].message.tool_calls is a list; we want the first call.
        choice = response.choices[0]
        if not choice.message.tool_calls:
            raise ValueError(
                f"Model did not call function {tool_name!r}. "
                "This model may not support tool/function calling."
            )

        tool_call = choice.message.tool_calls[0]

        # The function arguments come back as a JSON *string*, not a dict.
        raw = tool_call.function.arguments
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Model returned invalid JSON from {tool_name!r}: {exc}") from exc

        logger.debug("Function call %r returned %d keys", tool_name, len(result))
        return result
