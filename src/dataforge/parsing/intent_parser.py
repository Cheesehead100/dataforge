"""IntentParser — converts natural-language descriptions into a FlowGraph.

How it works:
  1. The user provides a sentence like "ADF reads from SQL MI, transforms in Databricks,
     writes to Fabric Lakehouse."
  2. We send it to an LLM (any provider via LlmAdapter) with a system prompt and
     the FlowGraph JSON Schema as a tool definition.
  3. The LLM is forced to call the tool, producing a JSON dict that matches the
     FlowGraph schema (nodes, edges, metadata).
  4. We validate the dict with Pydantic and run graph-level checks (e.g. no cycles).
  5. On failure we try up to MAX_RETRIES times, sending the error back to the LLM
     so it can self-correct.

The LLM used here is typically a fast/cheap model (Haiku or GPT-4o-mini) because
the task is purely extraction, not reasoning.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from dataforge.llm.adapter import LlmAdapter
from dataforge.models.flow_graph import FlowGraph
from dataforge.parsing.graph_validator import GraphValidationError, validate_graph
from dataforge.parsing.prompts import PARSE_SYSTEM_PROMPT, build_parse_messages

logger = logging.getLogger(__name__)


class ParseError(Exception):
    """Raised when IntentParser cannot produce a valid FlowGraph after all retries."""


class IntentParser:
    """Turns a natural-language pipeline description into a validated FlowGraph.

    This is the LLM-powered path into the system. The YAML path uses
    YamlParser + IntentResolver instead, which requires no LLM call.
    """

    MAX_RETRIES = 2
    # Hard cap on description length — limits prompt-injection surface and accidental API cost.
    MAX_DESCRIPTION_LEN = 2_000

    def __init__(self, adapter: LlmAdapter) -> None:
        self._adapter = adapter

    def parse(self, description: str, metadata_overrides: dict | None = None) -> FlowGraph:
        """Parse a natural-language description into a validated FlowGraph.

        Args:
            description:        The user's NL description of the data pipeline.
            metadata_overrides: Optional dict to merge into graph.metadata
                                (e.g. {"environment": "prod", "location": "westeurope"}).

        Returns:
            A validated FlowGraph ready for rendering and RBAC resolution.

        Raises:
            ParseError: If the LLM fails to produce a valid graph after MAX_RETRIES attempts.
        """
        if len(description) > self.MAX_DESCRIPTION_LEN:
            raise ParseError(
                f"Description too long ({len(description)} chars). "
                f"Keep it under {self.MAX_DESCRIPTION_LEN} characters."
            )

        schema = FlowGraph.model_json_schema()
        messages = build_parse_messages(description)
        last_error: Exception | None = None

        for attempt in range(self.MAX_RETRIES):
            try:
                # Ask the LLM to extract a FlowGraph dict from the description.
                # extract_json() forces the model to call the tool — it MUST return JSON.
                raw = self._adapter.extract_json(
                    system=PARSE_SYSTEM_PROMPT,
                    messages=messages,
                    schema=schema,
                    tool_name="extract_flow_graph",
                )

                # Validate the raw dict against Pydantic + custom graph rules.
                graph = self._validate(raw, description, metadata_overrides)
                logger.debug("Parsed FlowGraph with %d nodes, %d edges", len(graph.nodes), len(graph.edges))
                return graph

            except (ValidationError, GraphValidationError, ParseError, ValueError) as exc:
                last_error = exc
                logger.warning("Parse attempt %d failed: %s — retrying with feedback", attempt + 1, exc)
                # Send the error back so the model can fix its output on the next attempt.
                messages = _append_error_feedback(messages, str(exc))

        raise ParseError(
            f"Failed to parse a valid FlowGraph after {self.MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        ) from last_error

    def _validate(
        self,
        raw: dict,
        original_prompt: str,
        overrides: dict | None,
    ) -> FlowGraph:
        """Validate the raw dict and return a FlowGraph.

        Also:
          - Injects original_prompt into metadata if missing (preserves intent).
          - Merges any overrides (region, env, resource group) into metadata.
        """
        # Ensure metadata exists and record the original prompt for traceability.
        if "metadata" not in raw:
            raw["metadata"] = {}
        if not raw["metadata"].get("original_prompt"):
            raw["metadata"]["original_prompt"] = original_prompt

        # Apply CLI overrides (--env, --region, --resource-group) into the graph metadata.
        if overrides:
            raw["metadata"].update(overrides)

        try:
            # Pydantic validates field types, required fields, and enum values.
            graph = FlowGraph.model_validate(raw)
        except ValidationError as exc:
            raise ParseError(f"LLM output failed schema validation: {exc}") from exc

        # Run additional business rules: no cycles, no orphan edges, etc.
        validate_graph(graph)
        return graph


def _append_error_feedback(messages: list[dict], error: str) -> list[dict]:
    """Add an error feedback turn to the conversation so the model can self-correct.

    We tell the model what it produced was wrong and ask it to try again.
    This simulates the 'retry with feedback' pattern used in agentic loops.
    """
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
