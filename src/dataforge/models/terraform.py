"""Terraform output models."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class TerraformFile(BaseModel):
    """A single generated Terraform file."""

    filename: str = Field(description="Relative filename, e.g. 'rbac.tf'")
    content: str = Field(description="Full HCL content")

    def write_to(self, directory: Path) -> Path:
        path = directory / self.filename
        path.write_text(self.content, encoding="utf-8")
        return path


class GenerationResult(BaseModel):
    """All generated Terraform files for a FlowGraph."""

    files: list[TerraformFile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def file_map(self) -> dict[str, str]:
        return {f.filename: f.content for f in self.files}
