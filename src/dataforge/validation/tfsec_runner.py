"""Wraps the tfsec CLI and parses its JSON output."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TfsecFinding(BaseModel):
    rule_id: str
    description: str
    severity: str = "UNKNOWN"
    filename: str = ""
    start_line: int = 0


class TfsecReport(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    findings: list[TfsecFinding] = Field(default_factory=list)
    installed: bool = True
    raw_output: str = ""

    @property
    def ok(self) -> bool:
        return self.critical == 0 and self.high == 0

    @property
    def total(self) -> int:
        return self.critical + self.high + self.medium + self.low


class TfsecRunner:
    """Wraps the tfsec CLI and parses its JSON output."""

    def run(self, directory: Path) -> TfsecReport:
        if not shutil.which("tfsec"):
            logger.warning("tfsec not found — install from https://github.com/aquasecurity/tfsec")
            return TfsecReport(installed=False, raw_output="tfsec not installed")

        cmd = ["tfsec", str(directory), "--format", "json", "--no-colour", "--soft-fail"]
        logger.debug("Running: %s", " ".join(cmd))

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            logger.warning("tfsec timed out after 120s")
            return TfsecReport(raw_output="tfsec timed out")

        return self._parse(result.stdout or result.stderr)

    def _parse(self, raw: str) -> TfsecReport:
        if not raw.strip():
            return TfsecReport(raw_output=raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("tfsec JSON parse error; raw: %s", raw[:500])
            return TfsecReport(raw_output=raw)

        findings: list[TfsecFinding] = []
        counts: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}

        for result in data.get("results", []):
            severity = (result.get("severity") or "UNKNOWN").upper()
            location = result.get("location") or {}
            finding = TfsecFinding(
                rule_id=result.get("rule_id") or result.get("long_id", ""),
                description=result.get("description", ""),
                severity=severity,
                filename=location.get("filename", ""),
                start_line=location.get("start_line", 0),
            )
            findings.append(finding)
            if severity in counts:
                counts[severity] += 1

        return TfsecReport(
            critical=counts["CRITICAL"],
            high=counts["HIGH"],
            medium=counts["MEDIUM"],
            low=counts["LOW"],
            findings=findings,
            raw_output=raw,
        )
