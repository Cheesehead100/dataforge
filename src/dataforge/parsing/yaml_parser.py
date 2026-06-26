"""Parses a data-product YAML file or string into a validated DataProduct model.

This module is the first step of the YAML-based (no-LLM) pipeline entry point:
  data-product.yaml → YamlParser → DataProduct → IntentResolver → FlowGraph

YamlParser converts parse errors from PyYAML and Pydantic into the same
ParseError type that IntentParser raises, so callers can handle both paths
with a single except clause.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from dataforge.models.data_product import DataProduct
from dataforge.parsing.intent_parser import ParseError


class YamlParser:
    """Reads a data-product YAML file or string and returns a validated DataProduct."""

    def parse_file(self, path: Path) -> DataProduct:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ParseError(f"Cannot read {path}: {exc}") from exc
        return self.parse_string(raw)

    def parse_string(self, content: str) -> DataProduct:
        if not content or not content.strip():
            raise ParseError("DataProduct YAML is empty")

        try:
            data: Any = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ParseError(f"Invalid YAML: {exc}") from exc

        if not isinstance(data, dict):
            raise ParseError("DataProduct YAML must be a mapping at the top level")

        try:
            return DataProduct.model_validate(data)
        except ValidationError as exc:
            # Surface the first error message rather than the full Pydantic dump,
            # which can be several hundred characters and obscures the root cause
            # when reported back to the user.
            first = exc.errors()[0]
            msg = first.get("msg", str(exc))
            raise ParseError(msg) from exc
