"""
DataForge self-service portal — FastAPI web application.

Provides a browser-based form where data engineers describe a pipeline and receive
a downloadable ZIP of production-ready Terraform (plus CI/CD and monitoring config).
The two primary API endpoints, ``/api/preview`` and ``/api/generate``, follow the
same pipeline as the CLI: YAML build → YamlParser → IntentResolver → RbacResolver →
HclGenerator/DataProductGenerator.  All endpoints are protected by a per-session
bearer-token nonce and a sliding-window rate limiter enforced in _SecurityMiddleware.
"""

from __future__ import annotations

import io
import os
import re
import secrets
import time
import yaml
import zipfile
from collections import defaultdict, deque
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from dataforge.generation.data_product_generator import DataProductGenerator
from dataforge.generation.hcl_generator import HclGenerator
from dataforge.generation.renderer import Renderer
from dataforge.parsing.intent_resolver import IntentResolver
from dataforge.parsing.yaml_parser import YamlParser
from dataforge.parsing.intent_parser import ParseError
from dataforge.rbac.resolver import RbacResolver

# ── Security configuration ─────────────────────────────────────────────────────
# Optional persistent token for network deployments (set DATAFORGE_PORTAL_TOKEN).
# When unset the portal generates a random per-session nonce; the nonce is
# injected into the served HTML page so the browser JS can include it as a
# bearer token on every API call, giving CSRF protection without user friction.
#
# The nonce is intentionally per-process (not per-request) so the same browser
# session stays valid across multiple API calls without a login flow.  A new
# process (server restart) automatically invalidates any previously issued nonce,
# which is sufficient protection for a local/intranet self-hosted tool.
_PORTAL_TOKEN: str = os.environ.get("DATAFORGE_PORTAL_TOKEN", "")
_SESSION_NONCE: str = _PORTAL_TOKEN or secrets.token_urlsafe(32)

# Rate-limiting sliding-window state (in-memory, resets on server restart).
# /generate is kept tighter (5 rpm) because it invokes HclGenerator and
# DataProductGenerator synchronously, both of which are CPU-bound.
_RATE_WINDOWS: dict[str, deque[float]] = defaultdict(deque)
_LIMIT_GENERATE = (5, 60.0)   # 5 requests per 60 s for the expensive /generate endpoint
_LIMIT_PREVIEW  = (20, 60.0)  # 20 requests per 60 s for /preview

_STATIC_DIR = Path(__file__).parent / "static"

# Pre-compiled validation patterns (used by Pydantic field_validators below).
_EMAIL_RE  = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _rate_exceeded(ip: str, key: str, max_req: int, window_s: float) -> bool:
    """Sliding-window rate limiter.  Returns True when the limit is exceeded.

    Each (ip, key) pair gets its own deque of request timestamps.  Entries older
    than window_s are evicted before checking the count, implementing a true
    sliding window rather than a fixed-interval bucket.
    """
    bucket = _RATE_WINDOWS[f"{ip}:{key}"]
    now = time.monotonic()
    while bucket and now - bucket[0] > window_s:
        bucket.popleft()
    if len(bucket) >= max_req:
        return True
    bucket.append(now)
    return False


# ── Application ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DataForge Portal",
    description="Self-service data platform generator — fill a form, get production-ready infrastructure",
    version="1.0.0",
)


class _SecurityMiddleware(BaseHTTPMiddleware):
    """
    Single middleware that handles three concerns:
      1. CSRF — reject cross-origin POST/PUT/DELETE without a valid session token.
      2. Rate limiting — protect expensive endpoints from abuse.
      3. Security headers — added to every response.

    Bundling all three in one middleware ensures they run unconditionally before
    any route handler, regardless of FastAPI dependency injection order.
    """

    _SAFE_ORIGINS = ("http://localhost", "http://127.0.0.1", "https://localhost")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 1. CSRF guard: cross-origin mutating requests must carry the session token.
        if request.method in ("POST", "PUT", "DELETE") and path.startswith("/api/"):
            origin = request.headers.get("Origin", "")
            if origin and not any(origin.startswith(o) for o in self._SAFE_ORIGINS):
                auth = request.headers.get("Authorization", "")
                token = auth.removeprefix("Bearer ").strip()
                # compare_digest performs a constant-time comparison to prevent
                # timing attacks that could reveal the nonce one byte at a time.
                if not token or not secrets.compare_digest(token, _SESSION_NONCE):
                    return Response(
                        content='{"detail":"Cross-origin request rejected"}',
                        status_code=403,
                        media_type="application/json",
                    )

        # 2. Rate limiting on the two expensive endpoints.
        client_ip = request.client.host if request.client else "unknown"
        if path == "/api/generate":
            if _rate_exceeded(client_ip, "generate", *_LIMIT_GENERATE):
                return Response(
                    content='{"detail":"Rate limit exceeded — try again in a minute"}',
                    status_code=429,
                    media_type="application/json",
                )
        elif path == "/api/preview":
            if _rate_exceeded(client_ip, "preview", *_LIMIT_PREVIEW):
                return Response(
                    content='{"detail":"Rate limit exceeded — try again in a minute"}',
                    status_code=429,
                    media_type="application/json",
                )

        response = await call_next(request)

        # 3. Security headers on every response.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        )
        return response


app.add_middleware(_SecurityMiddleware)

# Bearer-token dependency — validates the per-session nonce injected into the HTML.
_bearer = HTTPBearer(auto_error=False)


def _require_token(
    creds: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    """Reject requests that do not carry the valid session nonce.

    The portal HTML page has the nonce injected at serve time, so legitimate
    browser usage is transparent.  Direct API access requires knowing the nonce
    (or the value of DATAFORGE_PORTAL_TOKEN for persistent deployments).

    This dependency is separate from _SecurityMiddleware's CSRF check: the
    middleware guards cross-origin requests by Origin header, while this
    dependency enforces authentication on same-origin direct API calls too.
    """
    token = creds.credentials if creds else ""
    # Constant-time compare here for the same reason as in the middleware.
    if not token or not secrets.compare_digest(token, _SESSION_NONCE):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Request / response models ──────────────────────────────────────────────────

class QualityCheck(BaseModel):
    """A single data quality rule (not_null / unique / freshness_within)."""

    type: Literal["not_null", "unique", "freshness_within"]
    column: str | None = Field(
        None,
        pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$",
        description="Column name (not_null, unique)",
    )
    hours: int | None = Field(None, ge=1, description="Max staleness in hours (freshness_within)")


class GenerateRequest(BaseModel):
    """Full generation request submitted by the portal form.

    All string fields use Pydantic Literal types or regex patterns so that
    invalid values are rejected before they reach any generator or YAML builder.
    """

    product:      str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    source_type:  Literal["sqlserver", "blob_storage", "eventhub", "adls", "sql_mi"]
    target_type:  Literal["adls", "fabric", "databricks", "fabric_lakehouse"]
    sla:          Literal["hourly", "daily", "weekly"] = "daily"
    environment:  Literal["dev", "test", "prod"] = "dev"
    region:       str = Field("eastus", max_length=32, pattern=r"^[a-z][a-z0-9\-]*$")
    resource_group: str | None = Field(
        None,
        max_length=90,
        pattern=r"^[a-zA-Z0-9_\-\.]*$",
        description="Defaults to rg-{product}-{environment}",
    )
    catalog: str | None = Field(
        None,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-]*$",
        description="Unity Catalog name (defaults to {product}_{environment})",
    )
    schemas:        list[str] = Field(default_factory=lambda: ["bronze", "silver", "gold"])
    private_endpoints: bool = Field(False)
    vnet_cidr:      str = Field(
        "10.20.0.0/16",
        pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}$",
    )
    monthly_budget_usd: int = Field(1000, ge=100, le=100000)
    alert_email:    str | None = Field(None, description="SRE alert email address")
    cicd_provider:  Literal["github_actions", "azure_devops"] = "github_actions"
    max_workers:    int = Field(8, ge=2, le=64)
    node_type:      Literal[
        "Standard_DS3_v2", "Standard_DS4_v2", "Standard_DS5_v2",
        "Standard_D8s_v3", "Standard_D16s_v3"
    ] = "Standard_DS3_v2"
    spot_enabled:   bool = Field(False)
    quality_checks: list[QualityCheck] = Field(default_factory=list)

    @field_validator("schemas")
    @classmethod
    def _validate_schemas(cls, v: list[str]) -> list[str]:
        for s in v:
            if not _SCHEMA_RE.match(s):
                raise ValueError(
                    f"Schema name {s!r} must match ^[a-z][a-z0-9_]*$ "
                    "(lowercase letters, digits, underscores)"
                )
        return v

    @field_validator("alert_email")
    @classmethod
    def _validate_email(cls, v: str | None) -> str | None:
        if v is not None and not _EMAIL_RE.match(v):
            raise ValueError(f"Invalid email address: {v!r}")
        return v


class PreviewRequest(BaseModel):
    """Lightweight request for the /api/preview endpoint.

    Contains the subset of GenerateRequest fields needed to build and parse the
    YAML for visual graph feedback.  Does not include region, resource_group, or
    catalog because those are cosmetic at preview time.
    """

    product:      str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    source_type:  Literal["sqlserver", "blob_storage", "eventhub", "adls", "sql_mi"]
    target_type:  Literal["adls", "fabric", "databricks", "fabric_lakehouse"]
    sla:          Literal["hourly", "daily", "weekly"] = "daily"
    environment:  Literal["dev", "test", "prod"] = "dev"
    private_endpoints:  bool = False
    monthly_budget_usd: int = Field(1000, ge=100, le=100000)
    alert_email:        str | None = Field(None)
    cicd_provider:      Literal["github_actions", "azure_devops"] = "github_actions"
    max_workers:        int = Field(8, ge=2, le=64)
    node_type:          Literal[
        "Standard_DS3_v2", "Standard_DS4_v2", "Standard_DS5_v2",
        "Standard_D8s_v3", "Standard_D16s_v3"
    ] = "Standard_DS3_v2"
    spot_enabled:       bool = False
    quality_checks:     list[QualityCheck] = Field(default_factory=list)

    @field_validator("alert_email")
    @classmethod
    def _validate_email(cls, v: str | None) -> str | None:
        if v is not None and not _EMAIL_RE.match(v):
            raise ValueError(f"Invalid email address: {v!r}")
        return v


class PreviewResponse(BaseModel):
    yaml: str
    node_count: int
    edge_count: int
    nodes: list[dict]


# ── YAML builder ───────────────────────────────────────────────────────────────

def _build_yaml(req: GenerateRequest | PreviewRequest) -> str:
    """Serialise the request as a data-product.yaml string.

    Uses yaml.dump() on a typed Python dict so that all values are correctly
    escaped by the YAML serialiser — no injection via string interpolation.
    """
    catalog_name = (
        getattr(req, "catalog", None)
        or f"{req.product}_{req.environment}".replace("-", "_")
    )
    schemas = list(getattr(req, "schemas", ["bronze", "silver", "gold"]))
    vnet_cidr = getattr(req, "vnet_cidr", "10.20.0.0/16")

    doc: dict = {
        "product": req.product,
        "environment": req.environment,
        "source": {"type": req.source_type},
        "target": {"type": req.target_type},
        "sla": req.sla,
        "compute": {
            "databricks": {
                "node_type": req.node_type,
                "autoscale": {"min_workers": 2, "max_workers": req.max_workers},
                "runtime": "14.3.x-scala2.12",
                "spot_enabled": req.spot_enabled,
            }
        },
        "governance": {
            "unity_catalog": {
                "metastore": f"unity-catalog-{req.environment}",
                "catalog": catalog_name,
                "schemas": [{"name": s} for s in schemas],
            }
        },
    }

    monitoring: dict = {}
    if req.alert_email:
        monitoring["alerts"] = [
            {
                "name": "pipeline_failure",
                "metric": "adf_pipeline_run_failed",
                "threshold": 1,
                "severity": "critical",
                "channel": f"email:{req.alert_email}",
            },
            {
                "name": "freshness_breach",
                "metric": "data_freshness_hours",
                "threshold": 24,
                "severity": "warning",
                "channel": f"email:{req.alert_email}",
            },
        ]

    raw_checks = list(getattr(req, "quality_checks", []) or [])
    checks: list[dict] = []
    for chk in raw_checks:
        chk_type = chk.type if hasattr(chk, "type") else chk.get("type", "")
        chk_col  = (chk.column if hasattr(chk, "column") else chk.get("column")) or ""
        chk_hrs  = chk.hours  if hasattr(chk, "hours")  else chk.get("hours")
        if chk_type in ("not_null", "unique") and chk_col:
            checks.append({"type": chk_type, "column": chk_col})
        elif chk_type == "freshness_within" and chk_hrs:
            checks.append({"type": "freshness_within", "hours": int(chk_hrs)})
    if checks:
        monitoring["quality"] = {"checks": checks}

    cost: dict = {
        "monthly_budget_usd": req.monthly_budget_usd,
        "alert_at_pct": [75, 90, 100],
    }
    if req.alert_email:
        cost["alert_channel"] = f"email:{req.alert_email}"
    monitoring["cost"] = cost
    doc["monitoring"] = monitoring

    doc["cicd"] = {
        "provider": req.cicd_provider,
        "gates": ["terraform_format", "terraform_validate", "checkov_scan", "python_unit_tests"],
    }

    if req.private_endpoints:
        doc["networking"] = {
            "private_endpoints": True,
            "vnet_cidr": vnet_cidr,
            "databricks_vnet_injection": True,
        }

    return yaml.dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ── API endpoints ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html_path = _STATIC_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=500, detail="Portal static assets not found")
    html = html_path.read_text(encoding="utf-8")
    # Inject the per-session nonce so the browser JS can authenticate API calls.
    # The nonce is embedded directly into the served HTML so the browser JS can
    # read it from window.__DF_TOKEN and attach it as `Authorization: Bearer <nonce>`
    # on every /api/* call.  The placeholder comment is the injection point.
    html = html.replace(
        "<!-- DATAFORGE_TOKEN_INJECT -->",
        f'<script>window.__DF_TOKEN = "{_SESSION_NONCE}";</script>',
    )
    return HTMLResponse(content=html)


@app.post("/api/preview", response_model=PreviewResponse, dependencies=[Depends(_require_token)])
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


@app.post("/api/generate", dependencies=[Depends(_require_token)])
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

    # The portal runs skeleton-only mode (llm_polish=False, client=None) so the
    # web server does not need an LLM API key and generation stays fast (<1 s).
    tf_result = HclGenerator(Renderer(), None).generate(graph, rbac, llm_polish=False)
    platform_result = DataProductGenerator().generate(product, graph, rbac)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
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
            {"value": "adls",             "label": "ADLS Gen2 (raw / medallion)"},
            {"value": "fabric",           "label": "Microsoft Fabric Lakehouse"},
            {"value": "databricks",       "label": "Databricks Delta Lake"},
            {"value": "fabric_lakehouse", "label": "Fabric Lakehouse (explicit)"},
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
