"""Output models representing generated Terraform files.

These are the final objects produced by the generation/renderer layer.
TerraformFile holds the HCL content of one .tf file; GenerationResult
bundles all files for a FlowGraph together so the output/writer can ZIP
or write them to disk in a single pass.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class TerraformFile(BaseModel):
    """A single generated .tf file with its relative filename and full HCL content."""

    filename: str = Field(description="Relative filename, e.g. 'rbac.tf'")
    content: str = Field(description="Full HCL content")

    def write_to(self, directory: Path) -> Path:
        path = directory / self.filename
        path.write_text(self.content, encoding="utf-8")
        return path


class GenerationResult(BaseModel):
    """The complete set of generated Terraform files for one FlowGraph, plus any warnings."""

    files: list[TerraformFile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def file_map(self) -> dict[str, str]:
        return {f.filename: f.content for f in self.files}
