"""Wraps the infracost CLI and parses its JSON output."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class InfracostResourceCost(BaseModel):
    name: str
    monthly_cost: float = 0.0
    monthly_quantity: float = 0.0
    unit: str = ""


class InfracostReport(BaseModel):
    total_monthly_cost: float = 0.0
    total_hourly_cost: float = 0.0
    currency: str = "USD"
    resources: list[InfracostResourceCost] = Field(default_factory=list)
    installed: bool = True
    raw_output: str = ""
    error: str = ""


class InfracostRunner:
    """Wraps the infracost CLI and parses its JSON output."""

    def run(self, directory: Path) -> InfracostReport:
        if not shutil.which("infracost"):
            logger.warning("infracost not found — install from https://www.infracost.io/docs")
            return InfracostReport(installed=False, raw_output="infracost not installed")

        cmd = [
            "infracost", "breakdown",
            "--path", str(directory),
            "--format", "json",
            "--no-color",
            "--terraform-parse-hcl",
        ]
        logger.debug("Running: %s", " ".join(cmd))

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            logger.warning("infracost timed out after 180s")
            return InfracostReport(error="infracost timed out")

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            logger.warning("infracost exited %d: %s", result.returncode, err[:500])
            return InfracostReport(error=err[:200], raw_output=result.stdout)

        return self._parse(result.stdout)

    def _parse(self, raw: str) -> InfracostReport:
        if not raw.strip():
            return InfracostReport(raw_output=raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("infracost JSON parse error; raw: %s", raw[:500])
            return InfracostReport(raw_output=raw)

        summary = data.get("summary", {})
        total_monthly = float(data.get("totalMonthlyCost") or 0)
        total_hourly  = float(data.get("totalHourlyCost")  or 0)
        currency      = data.get("currency", "USD")

        resources: list[InfracostResourceCost] = []
        for project in data.get("projects", []):
            for breakdown in project.get("breakdown", {}).get("resources", []):
                monthly = float(breakdown.get("monthlyCost") or 0)
                resources.append(InfracostResourceCost(
                    name=breakdown.get("name", ""),
                    monthly_cost=monthly,
                    monthly_quantity=float(breakdown.get("monthlyQuantity") or 0),
                    unit=breakdown.get("unit", ""),
                ))

        return InfracostReport(
            total_monthly_cost=total_monthly,
            total_hourly_cost=total_hourly,
            currency=currency,
            resources=sorted(resources, key=lambda r: r.monthly_cost, reverse=True),
            raw_output=raw,
        )
