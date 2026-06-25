"""AnthropicAdapter — wraps the Anthropic Python SDK.

Uses:
  • claude-haiku for fast structured extraction (IntentParser)
  • claude-sonnet for quality-focused text generation (HclGenerator polish)

Tool calling is Anthropic's preferred method for structured JSON extraction
because it forces the model to output a specific schema rather than hoping
the text response is valid JSON.
"""

from __future__ import annotations

import logging

import anthropic

from dataforge.config import Settings
from dataforge.llm.adapter import LlmAdapter

logger = logging.getLogger(__name__)


class AnthropicAdapter(LlmAdapter):
    """LlmAdapter implementation backed by the Anthropic Messages API."""

    def __init__(self, settings: Settings) -> None:
        # Build the Anthropic client once; it's thread-safe and can be reused.
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value()  # type: ignore[union-attr]
        )
        # Model names come from Settings so users can override via env vars.
        self._parse_model = settings.parse_model
        self._generate_model = settings.generate_model
        self._max_tokens_parse = settings.max_tokens_parse
        self._max_tokens_generate = settings.max_tokens_generate

    # ── Plain text generation ─────────────────────────────────────────────────

    def complete(self, system: str, messages: list[dict]) -> str:
        """Send a conversation to Claude and return the reply text.

        Used by HclGenerator to polish the Terraform skeleton — the model
        reads a template-generated .tf file and rewrites it with better
        resource naming, descriptions, and Terraform best practices.
        """
        response = self._client.messages.create(
            model=self._generate_model,
            max_tokens=self._max_tokens_generate,
            system=system,      # Anthropic takes system as a separate param
            messages=messages,
        )
        # response.content is a list; [0] is the assistant's text block.
        return response.content[0].text.strip()

    # ── Structured JSON extraction ────────────────────────────────────────────

    def extract_json(
        self,
        system: str,
        messages: list[dict],
        schema: dict,
        tool_name: str,
    ) -> dict:
        """Force Claude to return JSON matching `schema` via tool calling.

        Anthropic's tool_choice={"type": "any"} guarantees the model calls
        the tool rather than replying with text. The tool's input_schema is
        the JSON Schema for the expected output (in DataForge's case, FlowGraph).

        The returned dict is the raw tool call input — caller must validate it.
        """
        # Define the tool that Claude must call. The input_schema is exactly
        # the Pydantic model's JSON Schema, so the output is always valid shape.
        tool_def: anthropic.types.ToolParam = {
            "name": tool_name,
            "description": f"Output the structured {tool_name} extracted from the description.",
            "input_schema": schema,
        }

        response = self._client.messages.create(
            model=self._parse_model,
            max_tokens=self._max_tokens_parse,
            system=system,
            tools=[tool_def],
            tool_choice={"type": "any"},   # Force a tool call — no free text allowed
            messages=messages,
        )

        # Find the tool_use block in the response content list.
        tool_use_block = next(
            (b for b in response.content if b.type == "tool_use"),
            None,
        )
        if tool_use_block is None:
            raise ValueError(f"Claude did not call the {tool_name!r} tool.")

        logger.debug("Tool call %r succeeded with %d keys", tool_name, len(tool_use_block.input))
        return tool_use_block.input  # type: ignore[return-value]
