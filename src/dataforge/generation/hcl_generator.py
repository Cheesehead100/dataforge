"""Orchestrates Jinja2 rendering + optional Sonnet polish pass."""

from __future__ import annotations

import logging
from pathlib import Path

import anthropic

from dataforge.config import Settings
from dataforge.generation.prompts import GENERATE_SYSTEM_PROMPT, build_generate_messages
from dataforge.generation.renderer import Renderer
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult, TerraformFile

logger = logging.getLogger(__name__)

# These files are deterministic or non-HCL — never polished by LLM
_SKIP_LLM_FILES = {"rbac.tf", "providers.tf", "variables.tf"}
_SKIP_LLM_EXTENSIONS = {".yml", ".yaml"}


class HclGenerator:
    """Generates Terraform HCL from a FlowGraph + RbacResult.

    Phase 1 pipeline:
      1. Renderer generates deterministic skeleton from Jinja2 templates.
      2. (Optional) Sonnet polishes resource-specific files — NOT rbac.tf.
    """

    def __init__(
        self,
        renderer: Renderer,
        client: anthropic.Anthropic | None,
        settings: Settings | None,
    ) -> None:
        self._renderer = renderer
        self._client = client
        self._settings = settings

    def generate(
        self,
        graph: FlowGraph,
        rbac: RbacResult,
        *,
        llm_polish: bool = True,
    ) -> GenerationResult:
        """Render templates; optionally polish with Sonnet. rbac.tf is never LLM-touched."""
        files = self._renderer.render_all(graph, rbac)
        warnings = list(rbac.warnings)

        if rbac.unresolved:
            for key in rbac.unresolved:
                warnings.append(
                    f"No RBAC rule for {key} — no role assignment generated. "
                    "Review manually or add to the RBAC matrix."
                )

        if llm_polish and self._client is not None:
            files = self._polish_with_sonnet(files, graph)

        return GenerationResult(files=files, warnings=warnings)

    def _polish_with_sonnet(
        self,
        files: list[TerraformFile],
        graph: FlowGraph,
    ) -> list[TerraformFile]:
        polished: list[TerraformFile] = []
        for tf_file in files:
            if tf_file.filename in _SKIP_LLM_FILES or Path(tf_file.filename).suffix in _SKIP_LLM_EXTENSIONS:
                polished.append(tf_file)
                continue

            try:
                improved_content = self._call_sonnet(tf_file.content, graph)
                polished.append(TerraformFile(filename=tf_file.filename, content=improved_content))
                logger.debug("Sonnet polished %s", tf_file.filename)
            except Exception as exc:
                logger.warning("Sonnet polish failed for %s: %s — keeping skeleton", tf_file.filename, exc)
                polished.append(tf_file)

        return polished

    def _call_sonnet(self, skeleton: str, graph: FlowGraph) -> str:
        response = self._client.messages.create(  # type: ignore[union-attr]
            model=self._settings.generate_model,
            max_tokens=self._settings.max_tokens_generate,
            system=GENERATE_SYSTEM_PROMPT,
            messages=build_generate_messages(skeleton, graph),
        )
        return response.content[0].text.strip()
