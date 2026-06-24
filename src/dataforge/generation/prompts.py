"""Prompt builders for the Sonnet HCL polish pass."""

from __future__ import annotations

from dataforge.models.flow_graph import FlowGraph

GENERATE_SYSTEM_PROMPT = """\
You are a senior Azure Terraform engineer. Your job is to complete and improve Terraform HCL
skeleton code generated for an Azure data engineering stack.

RULES:
- Keep ALL resource block names, resource types, and variable references EXACTLY as provided.
- Fill in any placeholder values (marked TODO) using sensible, production-ready defaults.
- Apply Azure naming conventions: lowercase, hyphen-separated, with environment suffix.
- Add meaningful descriptions to variables.
- Do NOT modify the rbac.tf file — it is already complete and correct.
- Do NOT add resources that were not in the skeleton.
- Output only valid HCL — no markdown fences, no prose.
- Prefer azurerm provider >= 4.x idioms.
- Always set public_network_access_enabled = false on storage accounts.
- Always set enable_rbac_authorization = true on key vaults.
- Use customer_managed_key_enabled = true for Databricks workspaces when data_sensitivity is confidential or restricted."""


def build_generate_messages(skeleton: str, graph: FlowGraph) -> list[dict]:
    return [
        {
            "role": "user",
            "content": (
                f"Complete the following Terraform HCL skeleton for a "
                f"'{graph.metadata.environment}' environment data pipeline.\n\n"
                f"Data sensitivity: {graph.metadata.data_sensitivity}\n"
                f"Azure region: {graph.metadata.location}\n"
                f"Resource group: {graph.metadata.resource_group}\n\n"
                f"```hcl\n{skeleton}\n```\n\n"
                "Return only the completed HCL. Do not change resource names."
            ),
        }
    ]
