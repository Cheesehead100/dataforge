"""Runs Checkov against generated Terraform and parses the results."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CheckovFinding(BaseModel):
    check_id: str
    check_type: str
    resource: str
    file_path: str
    guideline: str = ""
    severity: str = "UNKNOWN"

    @property
    def is_critical(self) -> bool:
        return self.severity in {"CRITICAL", "HIGH"}


class CheckovReport(BaseModel):
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failed_checks: list[CheckovFinding] = Field(default_factory=list)
    raw_output: str = ""

    @property
    def ok(self) -> bool:
        return self.failed == 0

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.failed_checks if f.is_critical)


class CheckovRunner:
    """Wraps the checkov CLI and parses its JSON output."""

    def run(self, directory: Path) -> CheckovReport:
        cmd = [
            sys.executable, "-m", "checkov",
            "--directory", str(directory),
            "--framework", "terraform",
            "--output", "json",
            "--compact",
            "--quiet",
        ]
        logger.debug("Running: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            logger.warning("checkov not found — install with: pip install checkov")
            return CheckovReport(raw_output="checkov not installed")
        except subprocess.TimeoutExpired:
            logger.warning("checkov timed out after 120s")
            return CheckovReport(raw_output="checkov timed out")

        raw = result.stdout
        return self._parse(raw)

    def _parse(self, raw: str) -> CheckovReport:
        if not raw.strip():
            return CheckovReport(raw_output=raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Checkov JSON parse error; raw: %s", raw[:500])
            return CheckovReport(raw_output=raw)

        # checkov can emit a list of framework results or a single dict
        if isinstance(data, list):
            data = data[0] if data else {}

        summary = data.get("summary", {})
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        skipped = summary.get("skipped", 0)

        failed_checks: list[CheckovFinding] = []
        for check in data.get("results", {}).get("failed_checks", []):
            failed_checks.append(
                CheckovFinding(
                    check_id=check.get("check_id", ""),
                    check_type=check.get("check_type", ""),
                    resource=check.get("resource", ""),
                    file_path=check.get("file_path", ""),
                    guideline=check.get("guideline", ""),
                    severity=check.get("severity", "UNKNOWN"),
                )
            )

        return CheckovReport(
            passed=passed,
            failed=failed,
            skipped=skipped,
            failed_checks=failed_checks,
            raw_output=raw,
        )
