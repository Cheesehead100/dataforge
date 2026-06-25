"""HclGenerator — renders Terraform HCL from a FlowGraph, with an optional LLM polish pass.

Two-phase pipeline:
  Phase 1 (always runs): Jinja2 templates generate a deterministic HCL skeleton.
  Phase 2 (optional):    An LLM reads each .tf file and improves it — better
                          naming, descriptions, variable defaults, etc.

The LLM polish pass is skipped for:
  - rbac.tf, providers.tf, variables.tf  — these are deterministic by design
  - .yml / .yaml files                   — CI/CD pipelines, not Terraform HCL
  - When --no-llm-polish is passed       — skeleton-only mode, no API key needed

The polish pass uses the best available model (Sonnet or GPT-4o) because quality
matters more than speed here — it's a one-time generation step, not an inner loop.
"""

from __future__ import annotations

import logging
from pathlib import Path

from dataforge.generation.prompts import GENERATE_SYSTEM_PROMPT, build_generate_messages
from dataforge.generation.renderer import Renderer
from dataforge.llm.adapter import LlmAdapter
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult, TerraformFile

logger = logging.getLogger(__name__)

# Files that should NEVER go through the LLM polish pass.
# - rbac.tf:       RBAC assignments are precise; LLM could change resource addresses.
# - providers.tf:  Provider config is a standard boilerplate.
# - variables.tf:  Variable declarations are generated exactly as needed.
_SKIP_LLM_FILES = {"rbac.tf", "providers.tf", "variables.tf"}

# Non-Terraform file types — the LLM is trained on HCL, not YAML pipelines.
_SKIP_LLM_EXTENSIONS = {".yml", ".yaml"}


class HclGenerator:
    """Renders Terraform HCL from a FlowGraph, with an optional LLM polish step.

    Usage without LLM (YAML path or --no-llm-polish):
        HclGenerator(renderer, adapter=None).generate(graph, rbac, llm_polish=False)

    Usage with LLM polish:
        adapter = build_adapter(settings)
        HclGenerator(renderer, adapter).generate(graph, rbac, llm_polish=True)
    """

    def __init__(
        self,
        renderer: Renderer,
        adapter: LlmAdapter | None,
    ) -> None:
        self._renderer = renderer
        # adapter is None when running in skeleton-only mode (no LLM needed).
        self._adapter = adapter

    def generate(
        self,
        graph: FlowGraph,
        rbac: RbacResult,
        *,
        llm_polish: bool = True,
    ) -> GenerationResult:
        """Render all Terraform files, then optionally polish them with an LLM.

        Args:
            graph:       The FlowGraph describing nodes (services) and edges (data flows).
            rbac:        RBAC assignments to render into rbac.tf.
            llm_polish:  If True and adapter is set, run the LLM polish pass.

        Returns:
            GenerationResult with the list of TerraformFile objects and any warnings.
        """
        # Phase 1: Jinja2 template rendering — always runs, no LLM needed.
        files = self._renderer.render_all(graph, rbac)

        # Collect warnings from RBAC resolution (e.g. unresolvable principals).
        warnings = list(rbac.warnings)
        for key in rbac.unresolved:
            warnings.append(
                f"No RBAC rule for {key} — no role assignment generated. "
                "Review manually or add to the RBAC matrix."
            )

        # Phase 2: LLM polish pass — only if requested and adapter is available.
        if llm_polish and self._adapter is not None:
            files = self._polish_files(files, graph)

        return GenerationResult(files=files, warnings=warnings)

    def _polish_files(
        self,
        files: list[TerraformFile],
        graph: FlowGraph,
    ) -> list[TerraformFile]:
        """Run the LLM polish pass over each eligible .tf file.

        Files listed in _SKIP_LLM_FILES or with non-HCL extensions are passed
        through unchanged. Any file that fails polish is kept as-is (with a warning
        logged), so a single LLM failure doesn't break the whole generation.
        """
        polished: list[TerraformFile] = []

        for tf_file in files:
            # Skip files that shouldn't be touched by the LLM.
            is_excluded_name = tf_file.filename in _SKIP_LLM_FILES
            is_excluded_ext = Path(tf_file.filename).suffix in _SKIP_LLM_EXTENSIONS
            if is_excluded_name or is_excluded_ext:
                polished.append(tf_file)
                continue

            try:
                # Send the skeleton to the LLM and get back improved HCL.
                improved = self._adapter.complete(  # type: ignore[union-attr]
                    system=GENERATE_SYSTEM_PROMPT,
                    messages=build_generate_messages(tf_file.content, graph),
                )
                polished.append(TerraformFile(filename=tf_file.filename, content=improved))
                logger.debug("LLM polished %s", tf_file.filename)

            except Exception as exc:
                # Don't fail the whole generation if one file's polish fails.
                logger.warning("LLM polish failed for %s: %s — keeping skeleton", tf_file.filename, exc)
                polished.append(tf_file)

        return polished
