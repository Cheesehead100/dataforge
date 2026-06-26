"""System prompt and message builders for the LLM-powered intent parser.

This module owns the text that instructs the LLM how to extract a FlowGraph
from a natural-language description. The system prompt is intentionally strict:
it lists the exact allowed node types and operations (derived from the enums in
constants.py at import time) so the model never invents unsupported values.

Changes to NodeType or OperationType automatically propagate into the prompt
because the allowed-values lists are built dynamically from the enum members.
"""

from __future__ import annotations

from dataforge.constants import NodeType, OperationType

PARSE_SYSTEM_PROMPT = """\
You are a data-engineering infrastructure expert. Your job is to extract a precise,
structured data flow graph from a user's natural-language description of a data pipeline.

RULES:
- Identify all Azure data resources mentioned: ADF, Databricks, ADLS Gen2, Fabric Lakehouse,
  Key Vault, SQL Managed Instance, Event Hub, Blob Storage.
- Determine the operation on each directed edge:
    read        → source node reads data FROM target
    write       → source node writes data TO target
    trigger     → source node orchestrates/triggers target (ADF → Databricks, etc.)
    secret_get  → source node reads a secret FROM a Key Vault
    connect     → data-plane database connection (SQL MI only)
    stream      → real-time event streaming (Event Hub)
- Node ids must be lowercase snake_case, unique, and descriptive (e.g. "raw_adls", "adf_pipeline").
- Resource names should be short, meaningful labels the user can recognise.
- Include the original_prompt verbatim in metadata.

ALLOWED node types: """ + ", ".join(t.value for t in NodeType) + """
ALLOWED operations: """ + ", ".join(o.value for o in OperationType) + """

Respond ONLY via the extract_flow_graph tool call. Do not write any prose."""


def build_parse_messages(description: str) -> list[dict]:
    return [
        {"role": "user", "content": description},
    ]
