"""Tests for ADO pipeline generation."""

import pytest
from dataforge.constants import NodeType
from dataforge.models.flow_graph import FlowGraph, FlowMetadata, FlowNode
from dataforge.models.rbac import RbacResult
from dataforge.generation.renderer import Renderer


def _make_graph(node_types: list[NodeType]) -> FlowGraph:
    meta = FlowMetadata(
        environment="dev", location="eastus",
        resource_group="rg-test", application_name="dftest",
    )
    nodes = [FlowNode(id=f"n{i}", name=f"Node{i}", type=t) for i, t in enumerate(node_types)]
    return FlowGraph(nodes=nodes, edges=[], metadata=meta)


def _empty_rbac() -> RbacResult:
    return RbacResult(assignments=[], warnings=[], unresolved=[])


# ── Pipeline is always generated ──────────────────────────────────────────────

def test_pipeline_file_always_rendered():
    graph = _make_graph([NodeType.ADF])
    files = Renderer().render_all(graph, _empty_rbac())
    filenames = [f.filename for f in files]
    assert "azure-pipelines.yml" in filenames


def test_pipeline_rendered_for_minimal_graph():
    graph = _make_graph([NodeType.ADLS])
    files = Renderer().render_all(graph, _empty_rbac())
    filenames = [f.filename for f in files]
    assert "azure-pipelines.yml" in filenames


# ── Pipeline content structure ────────────────────────────────────────────────

def test_pipeline_has_three_stages():
    graph = _make_graph([NodeType.ADF])
    files = Renderer().render_all(graph, _empty_rbac())
    yml = next(f for f in files if f.filename == "azure-pipelines.yml")
    assert "stages:" in yml.content
    assert "Validate" in yml.content
    assert "Plan" in yml.content
    assert "Apply" in yml.content


def test_pipeline_has_terraform_init_and_apply():
    graph = _make_graph([NodeType.ADF])
    files = Renderer().render_all(graph, _empty_rbac())
    yml = next(f for f in files if f.filename == "azure-pipelines.yml")
    assert "terraform init" in yml.content.lower() or "command: init" in yml.content
    assert "terraform apply" in yml.content.lower() or "command: apply" in yml.content


def test_pipeline_requires_service_connection_var():
    graph = _make_graph([NodeType.ADF])
    files = Renderer().render_all(graph, _empty_rbac())
    yml = next(f for f in files if f.filename == "azure-pipelines.yml")
    assert "ARM_SERVICE_CONNECTION" in yml.content


def test_pipeline_apply_gated_to_main():
    graph = _make_graph([NodeType.ADF])
    files = Renderer().render_all(graph, _empty_rbac())
    yml = next(f for f in files if f.filename == "azure-pipelines.yml")
    assert "refs/heads/main" in yml.content


# ── AKS note injected when AKS present ───────────────────────────────────────

def test_pipeline_includes_kubectl_note_for_aks():
    graph = _make_graph([NodeType.AKS])
    files = Renderer().render_all(graph, _empty_rbac())
    yml = next(f for f in files if f.filename == "azure-pipelines.yml")
    assert "kubectl" in yml.content


def test_pipeline_no_kubectl_note_without_aks():
    graph = _make_graph([NodeType.ADF])
    files = Renderer().render_all(graph, _empty_rbac())
    yml = next(f for f in files if f.filename == "azure-pipelines.yml")
    assert "kubectl" not in yml.content


# ── LLM polish skips YAML files ──────────────────────────────────────────────

def test_hcl_generator_skips_yml_in_polish():
    from dataforge.generation.hcl_generator import _SKIP_LLM_EXTENSIONS
    assert ".yml" in _SKIP_LLM_EXTENSIONS
    assert ".yaml" in _SKIP_LLM_EXTENSIONS
