"""Converts natural-language pipeline descriptions into FlowGraph via Claude Haiku."""

from __future__ import annotations

import json
import logging

import anthropic
from pydantic import ValidationError

from dataforge.config import Settings
from dataforge.models.flow_graph import FlowGraph
from dataforge.parsing.graph_validator import GraphValidationError, validate_graph
from dataforge.parsing.prompts import PARSE_SYSTEM_PROMPT, build_parse_messages

logger = logging.getLogger(__name__)


class ParseError(Exception):
    pass


class IntentParser:
    """Uses Claude Haiku with forced tool-use to extract a FlowGraph from natural language."""

    MAX_RETRIES = 2

    def __init__(self, client: anthropic.Anthropic, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def parse(self, description: str, metadata_overrides: dict | None = None) -> FlowGraph:
        """Parse a natural-language description into a validated FlowGraph.

        Raises ParseError if the LLM fails to produce a valid graph after retries.
        """
        schema = FlowGraph.model_json_schema()
        tool_def: anthropic.types.ToolParam = {
            "name": "extract_flow_graph",
            "description": "Output the structured data flow graph extracted from the description.",
            "input_schema": schema,
        }

        messages = build_parse_messages(description)
        last_error: Exception | None = None

        for attempt in range(self.MAX_RETRIES):
            try:
                raw = self._call_haiku(messages, tool_def)
                graph = self._parse_tool_result(raw, description, metadata_overrides)
                validate_graph(graph)
                logger.debug("Parsed FlowGraph with %d nodes, %d edges", len(graph.nodes), len(graph.edges))
                return graph
            except (ValidationError, GraphValidationError, ParseError) as exc:
                last_error = exc
                logger.warning("Parse attempt %d failed: %s — retrying with feedback", attempt + 1, exc)
                messages = self._feedback_messages(messages, str(exc))

        raise ParseError(
            f"Failed to parse a valid FlowGraph after {self.MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        ) from last_error

    def _call_haiku(self, messages: list[dict], tool_def: anthropic.types.ToolParam) -> dict:
        response = self._client.messages.create(
            model=self._settings.parse_model,
            max_tokens=self._settings.max_tokens_parse,
            system=PARSE_SYSTEM_PROMPT,
            tools=[tool_def],
            tool_choice={"type": "any"},
            messages=messages,
        )

        tool_use_block = next(
            (b for b in response.content if b.type == "tool_use"),
            None,
        )
        if tool_use_block is None:
            raise ParseError("Haiku did not call the extract_flow_graph tool.")

        return tool_use_block.input  # type: ignore[return-value]

    def _parse_tool_result(
        self,
        raw: dict,
        original_prompt: str,
        overrides: dict | None,
    ) -> FlowGraph:
        # Inject original_prompt into metadata if missing
        if "metadata" not in raw:
            raw["metadata"] = {}
        if not raw["metadata"].get("original_prompt"):
            raw["metadata"]["original_prompt"] = original_prompt

        if overrides:
            raw["metadata"].update(overrides)

        try:
            return FlowGraph.model_validate(raw)
        except ValidationError as exc:
            raise ParseError(f"LLM output failed schema validation: {exc}") from exc

    @staticmethod
    def _feedback_messages(messages: list[dict], error: str) -> list[dict]:
        return messages + [
            {
                "role": "assistant",
                "content": "I attempted to extract the flow graph but the output was invalid.",
            },
            {
                "role": "user",
                "content": (
                    f"Your previous output failed validation with this error:\n{error}\n\n"
                    "Please fix the issues and call extract_flow_graph again with a valid graph."
                ),
            },
        ]
