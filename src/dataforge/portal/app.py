"""DataForge self-service portal — FastAPI application."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from dataforge.generation.data_product_generator import DataProductGenerator
from dataforge.generation.hcl_generator import HclGenerator
from dataforge.generation.renderer import Renderer
from dataforge.parsing.intent_resolver import IntentResolver
from dataforge.parsing.yaml_parser import YamlParser
from dataforge.parsing.intent_parser import ParseError
from dataforge.rbac.resolver import RbacResolver

app = FastAPI(
    title="DataForge Portal",
    description="Self-service data platform generator — fill a form, get production-ready infrastructure",
    version="1.0.0",
)

_STATIC_DIR = Path(__file__).parent / "static"


# ── Request / response models ─────────────────────────────────────────────────

class QualityCheck(BaseModel):
    """A single data quality rule (not_null / unique / freshness_within)."""

    type: str = Field(..., description="not_null | unique | freshness_within")
    column: str | None = Field(None, description="Column name (not_null, unique)")
    hours: int | None = Field(None, ge=1, description="Max acceptable staleness in hours (freshness_within)")


class GenerateRequest(BaseModel):
    product: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    source_type: str = Field(..., description="Source system type (sqlserver, blob_storage, eventhub, adls)")
    target_type: str = Field(..., description="Target system type (adls, fabric, databricks)")
    sla: str = Field("daily", description="hourly | daily | weekly")
    environment: str = Field("dev", description="dev | test | prod")
    region: str = Field("eastus")
    resource_group: str | None = Field(None, description="Defaults to rg-{product}-{environment}")
    catalog: str | None = Field(None, description="Unity Catalog name (defaults to {product}_{environment})")
    schemas: list[str] = Field(default_factory=lambda: ["bronze", "silver", "gold"])
    private_endpoints: bool = Field(False)
    vnet_cidr: str = Field("10.20.0.0/16")
    monthly_budget_usd: int = Field(1000, ge=100, le=100000)
    alert_email: str | None = Field(None, description="SRE alert email address")
    cicd_provider: str = Field("github_actions", description="github_actions | azure_devops")
    max_workers: int = Field(8, ge=2, le=64)
    node_type: str = Field("Standard_DS3_v2")
    spot_enabled: bool = Field(False)
    quality_checks: list[QualityCheck] = Field(
        default_factory=list,
        description="Data quality rules (not_null, unique, freshness_within)",
    )


class PreviewRequest(BaseModel):
    product: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    source_type: str
    target_type: str
    sla: str = "daily"
    environment: str = "dev"
    private_endpoints: bool = False
    monthly_budget_usd: int = 1000
    alert_email: str | None = None
    cicd_provider: str = "github_actions"
    max_workers: int = 8
    node_type: str = "Standard_DS3_v2"
    spot_enabled: bool = False
    quality_checks: list[QualityCheck] = Field(default_factory=list)


class PreviewResponse(BaseModel):
    yaml: str
    node_count: int
    edge_count: int
    nodes: list[dict]


# ── YAML builder ──────────────────────────────────────────────────────────────

def _build_yaml(req: GenerateRequest | PreviewRequest) -> str:
    rg = getattr(req, "resource_group", None) or f"rg-{req.product}-{req.environment}"
    catalog = getattr(req, "catalog", None) or f"{req.product}_{req.environment}".replace("-", "_")
    schemas = getattr(req, "schemas", ["bronze", "silver", "gold"])
    vnet_cidr = getattr(req, "vnet_cidr", "10.20.0.0/16")
    private_endpoints = req.private_endpoints

    alert_block = ""
    if req.alert_email:
        alert_block = f"""
  alerts:
    - name: pipeline_failure
      metric: adf_pipeline_run_failed
      threshold: 1
      severity: critical
      channel: "email:{req.alert_email}"
    - name: freshness_breach
      metric: data_freshness_hours
      threshold: 24
      severity: warning
      channel: "email:{req.alert_email}"
"""

    # Quality checks block (nested under monitoring so MonitoringSpec.extra="allow" picks it up)
    quality_block = ""
    raw_checks = getattr(req, "quality_checks", []) or []
    check_lines: list[str] = []
    for chk in raw_checks:
        chk_type = chk.type if hasattr(chk, "type") else chk.get("type", "")
        chk_col  = (chk.column if hasattr(chk, "column") else chk.get("column")) or ""
        chk_hrs  = (chk.hours  if hasattr(chk, "hours")  else chk.get("hours"))
        if chk_type in ("not_null", "unique") and chk_col:
            check_lines.append(f"      - type: {chk_type}\n        column: {chk_col}")
        elif chk_type == "freshness_within" and chk_hrs:
            check_lines.append(f"      - type: freshness_within\n        hours: {int(chk_hrs)}")
    if check_lines:
        quality_block = "\n  quality:\n    checks:\n" + "\n".join(check_lines) + "\n"

    networking_block = ""
    if private_endpoints:
        networking_block = f"""
networking:
  private_endpoints: true
  vnet_cidr: {vnet_cidr}
  databricks_vnet_injection: true
"""

    schemas_yaml = "\n".join(f"      - name: {s}" for s in schemas)

    return f"""product: {req.product}
environment: {req.environment}

source:
  type: {req.source_type}

target:
  type: {req.target_type}

sla: {req.sla}

compute:
  databricks:
    node_type: {req.node_type}
    autoscale:
      min_workers: 2
      max_workers: {req.max_workers}
    runtime: "14.3.x-scala2.12"
    spot_enabled: {str(req.spot_enabled).lower()}

governance:
  unity_catalog:
    metastore: unity-catalog-{req.environment}
    catalog: {catalog}
    schemas:
{schemas_yaml}

monitoring:{alert_block}{quality_block}
  cost:
    monthly_budget_usd: {req.monthly_budget_usd}
    alert_at_pct: [75, 90, 100]
{f'    alert_channel: "email:{req.alert_email}"' if req.alert_email else ''}

cicd:
  provider: {req.cicd_provider}
  gates:
    - terraform_format
    - terraform_validate
    - checkov_scan
    - python_unit_tests
{networking_block}"""


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html_path = _STATIC_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=500, detail="Portal static assets not found")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/api/preview", response_model=PreviewResponse)
async def preview(req: PreviewRequest) -> PreviewResponse:
    """Build and parse the YAML — returns the graph for visual feedback before generating."""
    yaml_str = _build_yaml(req)
    try:
        product = YamlParser().parse_string(yaml_str)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        graph = IntentResolver().resolve(product, env=req.environment)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return PreviewResponse(
        yaml=yaml_str,
        node_count=len(graph.nodes),
        edge_count=len(graph.edges),
        nodes=[{"id": n.id, "type": n.type.value, "name": n.name} for n in graph.nodes],
    )


@app.post("/api/generate")
async def generate(req: GenerateRequest) -> StreamingResponse:
    """Generate the full platform stack and return a ZIP file."""
    yaml_str = _build_yaml(req)

    try:
        product = YamlParser().parse_string(yaml_str)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=f"YAML parse error: {exc}") from exc

    try:
        graph = IntentResolver().resolve(product, env=req.environment)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Graph resolution error: {exc}") from exc

    rbac = RbacResolver().resolve(graph)

    # Portal always uses skeleton-only mode (no LLM polish) so no API key is needed.
    tf_result = HclGenerator(Renderer(), None).generate(graph, rbac, llm_polish=False)
    platform_result = DataProductGenerator().generate(product, graph, rbac)

    # Package everything into a ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Include the source data-product.yaml so users can reproduce / modify
        zf.writestr("data-product.yaml", yaml_str)
        for tf_file in tf_result.files + platform_result.files:
            zf.writestr(tf_file.filename, tf_file.content)

    buf.seek(0)
    zip_name = f"{req.product}-{req.environment}-dataforge.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/options")
async def options() -> dict:
    """Return available source/target types and other enum options for the form."""
    return {
        "source_types": [
            {"value": "sqlserver",    "label": "SQL Server / Azure SQL"},
            {"value": "blob_storage", "label": "Azure Blob Storage"},
            {"value": "eventhub",     "label": "Azure Event Hub (streaming)"},
            {"value": "adls",         "label": "ADLS Gen2 (existing lake)"},
            {"value": "sql_mi",       "label": "SQL Managed Instance"},
        ],
        "target_types": [
            {"value": "adls",              "label": "ADLS Gen2 (raw / medallion)"},
            {"value": "fabric",            "label": "Microsoft Fabric Lakehouse"},
            {"value": "databricks",        "label": "Databricks Delta Lake"},
            {"value": "fabric_lakehouse",  "label": "Fabric Lakehouse (explicit)"},
        ],
        "sla_options": [
            {"value": "hourly",  "label": "Hourly (freshness ≤ 1h)"},
            {"value": "daily",   "label": "Daily (freshness ≤ 24h)"},
            {"value": "weekly",  "label": "Weekly (freshness ≤ 7d)"},
        ],
        "cicd_providers": [
            {"value": "github_actions", "label": "GitHub Actions"},
            {"value": "azure_devops",   "label": "Azure DevOps"},
        ],
        "environments": ["dev", "test", "prod"],
        "node_types": [
            "Standard_DS3_v2",
            "Standard_DS4_v2",
            "Standard_DS5_v2",
            "Standard_D8s_v3",
            "Standard_D16s_v3",
        ],
    }
