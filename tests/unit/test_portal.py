"""Unit tests for the DataForge self-service portal (FastAPI app)."""

from __future__ import annotations

import zipfile
import io

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed — run pip install dataforge[portal]")
pytest.importorskip("httpx", reason="httpx not installed — run pip install dataforge[dev]")

from fastapi.testclient import TestClient  # noqa: E402
from dataforge.portal.app import app, _build_yaml, GenerateRequest, PreviewRequest, QualityCheck  # noqa: E402

client = TestClient(app)

_BASE = {
    "product": "customer360",
    "source_type": "sqlserver",
    "target_type": "adls",
    "sla": "daily",
    "environment": "dev",
    "monthly_budget_usd": 1000,
    "cicd_provider": "github_actions",
    "max_workers": 8,
    "node_type": "Standard_DS3_v2",
    "spot_enabled": False,
    "private_endpoints": False,
}


# ── /api/health ───────────────────────────────────────────────────────────────

class TestHealth:

    def test_returns_200(self):
        r = client.get("/api/health")
        assert r.status_code == 200

    def test_returns_ok_status(self):
        r = client.get("/api/health")
        assert r.json()["status"] == "ok"


# ── /api/options ──────────────────────────────────────────────────────────────

class TestOptions:

    def test_returns_200(self):
        assert client.get("/api/options").status_code == 200

    def test_has_source_types(self):
        data = client.get("/api/options").json()
        assert "source_types" in data
        assert any(s["value"] == "sqlserver" for s in data["source_types"])

    def test_has_target_types(self):
        data = client.get("/api/options").json()
        assert "target_types" in data

    def test_has_cicd_providers(self):
        data = client.get("/api/options").json()
        assert "cicd_providers" in data


# ── GET / (HTML) ──────────────────────────────────────────────────────────────

class TestIndex:

    def test_returns_html(self):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_contains_dataforge(self):
        r = client.get("/")
        assert "DataForge" in r.text

    def test_has_form_elements(self):
        r = client.get("/")
        assert "source_type" in r.text
        assert "target_type" in r.text


# ── _build_yaml ───────────────────────────────────────────────────────────────

class TestBuildYaml:

    def _req(self, **overrides):
        data = {**_BASE, **overrides}
        return PreviewRequest(**data)

    def test_has_product_field(self):
        y = _build_yaml(self._req())
        assert "product: customer360" in y

    def test_has_source_type(self):
        y = _build_yaml(self._req())
        assert "type: sqlserver" in y

    def test_has_target_type(self):
        y = _build_yaml(self._req())
        assert "type: adls" in y

    def test_has_sla(self):
        y = _build_yaml(self._req())
        assert "sla: daily" in y

    def test_has_compute_block(self):
        y = _build_yaml(self._req())
        assert "databricks:" in y
        assert "max_workers: 8" in y

    def test_has_governance_block(self):
        y = _build_yaml(self._req())
        assert "unity_catalog:" in y

    def test_alert_block_absent_when_no_email(self):
        y = _build_yaml(self._req(alert_email=None))
        assert "pipeline_failure" not in y

    def test_alert_block_present_with_email(self):
        y = _build_yaml(self._req(alert_email="sre@company.com"))
        assert "pipeline_failure" in y
        assert "sre@company.com" in y

    def test_networking_block_absent_when_pe_false(self):
        y = _build_yaml(self._req(private_endpoints=False))
        assert "private_endpoints" not in y

    def test_networking_block_present_when_pe_true(self):
        y = _build_yaml(self._req(private_endpoints=True))
        assert "private_endpoints: true" in y

    def test_cicd_provider_in_yaml(self):
        y = _build_yaml(self._req(cicd_provider="azure_devops"))
        assert "provider: azure_devops" in y


# ── /api/preview ──────────────────────────────────────────────────────────────

class TestPreview:

    def test_returns_200_for_valid_input(self):
        r = client.post("/api/preview", json=_BASE)
        assert r.status_code == 200

    def test_returns_yaml_string(self):
        r = client.post("/api/preview", json=_BASE)
        assert "yaml" in r.json()
        assert "product:" in r.json()["yaml"]

    def test_returns_node_count(self):
        r = client.post("/api/preview", json=_BASE)
        data = r.json()
        assert "node_count" in data
        assert data["node_count"] >= 1

    def test_returns_nodes_list(self):
        r = client.post("/api/preview", json=_BASE)
        assert isinstance(r.json()["nodes"], list)

    def test_returns_422_for_invalid_product_name(self):
        bad = {**_BASE, "product": "has spaces!"}
        r = client.post("/api/preview", json=bad)
        assert r.status_code == 422

    def test_returns_422_for_missing_product(self):
        bad = {k: v for k, v in _BASE.items() if k != "product"}
        r = client.post("/api/preview", json=bad)
        assert r.status_code == 422

    def test_nodes_have_required_keys(self):
        r = client.post("/api/preview", json=_BASE)
        for node in r.json()["nodes"]:
            assert "id" in node
            assert "type" in node
            assert "name" in node

    def test_fabric_target_resolves(self):
        req = {**_BASE, "target_type": "fabric"}
        r = client.post("/api/preview", json=req)
        assert r.status_code == 200

    def test_edge_count_returned(self):
        r = client.post("/api/preview", json=_BASE)
        assert "edge_count" in r.json()


# ── /api/generate ─────────────────────────────────────────────────────────────

class TestGenerate:

    def test_returns_200(self):
        r = client.post("/api/generate", json=_BASE)
        assert r.status_code == 200

    def test_returns_zip_content_type(self):
        r = client.post("/api/generate", json=_BASE)
        assert "application/zip" in r.headers["content-type"]

    def test_content_disposition_has_filename(self):
        r = client.post("/api/generate", json=_BASE)
        assert "customer360-dev-dataforge.zip" in r.headers["content-disposition"]

    def test_zip_is_valid(self):
        r = client.post("/api/generate", json=_BASE)
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        assert len(names) > 0

    def test_zip_contains_data_product_yaml(self):
        r = client.post("/api/generate", json=_BASE)
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            assert "data-product.yaml" in zf.namelist()

    def test_zip_contains_terraform_files(self):
        r = client.post("/api/generate", json=_BASE)
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            tf_files = [n for n in zf.namelist() if n.endswith(".tf")]
        assert len(tf_files) > 0

    def test_zip_contains_cicd_files(self):
        r = client.post("/api/generate", json=_BASE)
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        ci_files = [n for n in names if ".yml" in n or ".yaml" in n]
        assert len(ci_files) > 0

    def test_zip_contains_governance_files(self):
        r = client.post("/api/generate", json=_BASE)
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        assert any("governance" in n or "unity" in n for n in names)

    def test_private_endpoints_adds_network_files(self):
        req = {**_BASE, "private_endpoints": True}
        r = client.post("/api/generate", json=req)
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        net_files = [n for n in names if "network/" in n]
        assert len(net_files) > 0

    def test_private_endpoints_false_has_no_network_dir(self):
        r = client.post("/api/generate", json=_BASE)
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        net_files = [n for n in names if "network/private_endpoints" in n]
        assert len(net_files) == 0

    def test_zip_contains_sre_files(self):
        r = client.post("/api/generate", json=_BASE)
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        sre_files = [n for n in names if "sre/" in n or "runbook" in n]
        assert len(sre_files) > 0

    def test_returns_422_for_invalid_product(self):
        bad = {**_BASE, "product": "invalid product name!"}
        r = client.post("/api/generate", json=bad)
        assert r.status_code == 422

    def test_no_duplicate_files_in_zip(self):
        r = client.post("/api/generate", json=_BASE)
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        assert len(names) == len(set(names))

    def test_azure_devops_cicd_provider(self):
        req = {**_BASE, "cicd_provider": "azure_devops"}
        r = client.post("/api/generate", json=req)
        assert r.status_code == 200
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        ado_files = [n for n in names if "azure-pipelines" in n]
        assert len(ado_files) > 0

    def test_data_product_yaml_in_zip_matches_product_name(self):
        r = client.post("/api/generate", json=_BASE)
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            yaml_content = zf.read("data-product.yaml").decode()
        assert "customer360" in yaml_content


# ── Quality Checks ────────────────────────────────────────────────────────────

class TestQualityChecks:

    def _req(self, quality_checks=None, **overrides):
        data = {**_BASE, "quality_checks": quality_checks or [], **overrides}
        return PreviewRequest(**data)

    def test_no_quality_block_when_empty(self):
        y = _build_yaml(self._req(quality_checks=[]))
        assert "quality:" not in y

    def test_not_null_check_in_yaml(self):
        checks = [{"type": "not_null", "column": "customer_id"}]
        data = {**_BASE, "quality_checks": checks}
        y = _build_yaml(PreviewRequest(**data))
        assert "not_null" in y
        assert "customer_id" in y

    def test_unique_check_in_yaml(self):
        checks = [{"type": "unique", "column": "order_id"}]
        data = {**_BASE, "quality_checks": checks}
        y = _build_yaml(PreviewRequest(**data))
        assert "unique" in y
        assert "order_id" in y

    def test_freshness_check_in_yaml(self):
        checks = [{"type": "freshness_within", "hours": 24}]
        data = {**_BASE, "quality_checks": checks}
        y = _build_yaml(PreviewRequest(**data))
        assert "freshness_within" in y
        assert "hours: 24" in y

    def test_multiple_checks_all_emitted(self):
        checks = [
            {"type": "not_null",        "column": "id"},
            {"type": "unique",          "column": "order_id"},
            {"type": "freshness_within","hours": 12},
        ]
        data = {**_BASE, "quality_checks": checks}
        y = _build_yaml(PreviewRequest(**data))
        assert y.count("- type:") >= 3

    def test_not_null_without_column_ignored(self):
        checks = [{"type": "not_null", "column": ""}]
        data = {**_BASE, "quality_checks": checks}
        y = _build_yaml(PreviewRequest(**data))
        assert "quality:" not in y

    def test_freshness_without_hours_ignored(self):
        checks = [{"type": "freshness_within", "hours": None}]
        data = {**_BASE, "quality_checks": checks}
        y = _build_yaml(PreviewRequest(**data))
        assert "quality:" not in y

    def test_quality_block_nested_under_monitoring(self):
        checks = [{"type": "not_null", "column": "id"}]
        data = {**_BASE, "quality_checks": checks}
        y = _build_yaml(PreviewRequest(**data))
        # quality: must appear AFTER monitoring:
        mon_pos = y.index("monitoring:")
        qual_pos = y.index("quality:")
        assert qual_pos > mon_pos

    def test_preview_endpoint_accepts_quality_checks(self):
        req = {**_BASE, "quality_checks": [{"type": "not_null", "column": "id"}]}
        r = client.post("/api/preview", json=req)
        assert r.status_code == 200
        assert "not_null" in r.json()["yaml"]

    def test_generate_endpoint_accepts_quality_checks(self):
        req = {**_BASE, "quality_checks": [{"type": "unique", "column": "sku"}]}
        r = client.post("/api/generate", json=req)
        assert r.status_code == 200
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            yaml_content = zf.read("data-product.yaml").decode()
        assert "unique" in yaml_content
        assert "sku" in yaml_content

    def test_quality_check_model_validates_type(self):
        chk = QualityCheck(type="not_null", column="id")
        assert chk.type == "not_null"
        assert chk.column == "id"

    def test_freshness_check_model_stores_hours(self):
        chk = QualityCheck(type="freshness_within", hours=48)
        assert chk.hours == 48
