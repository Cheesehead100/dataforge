"""Tests for VNet CIDR as variables (Phase 3)."""

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


# ── No hardcoded CIDRs in networking.tf ──────────────────────────────────────

def test_networking_uses_var_vnet_address_space():
    graph = _make_graph([NodeType.DATABRICKS])
    files = Renderer().render_all(graph, _empty_rbac())
    net = next(f for f in files if f.filename == "networking.tf")
    assert "var.vnet_address_space" in net.content
    assert "10.0.0.0/16" not in net.content


def test_networking_uses_var_dbw_private_subnet_cidr():
    graph = _make_graph([NodeType.DATABRICKS])
    files = Renderer().render_all(graph, _empty_rbac())
    net = next(f for f in files if f.filename == "networking.tf")
    assert "var.dbw_private_subnet_cidr" in net.content


def test_networking_uses_var_dbw_public_subnet_cidr():
    graph = _make_graph([NodeType.DATABRICKS])
    files = Renderer().render_all(graph, _empty_rbac())
    net = next(f for f in files if f.filename == "networking.tf")
    assert "var.dbw_public_subnet_cidr" in net.content


def test_networking_uses_var_pe_subnet_cidr():
    graph = _make_graph([NodeType.DATABRICKS])
    files = Renderer().render_all(graph, _empty_rbac())
    net = next(f for f in files if f.filename == "networking.tf")
    assert "var.pe_subnet_cidr" in net.content


# ── Variables declared only when Databricks present ──────────────────────────

def test_variables_has_cidr_vars_when_databricks():
    graph = _make_graph([NodeType.DATABRICKS])
    files = Renderer().render_all(graph, _empty_rbac())
    variables = next(f for f in files if f.filename == "variables.tf")
    assert "vnet_address_space" in variables.content
    assert "dbw_private_subnet_cidr" in variables.content


def test_variables_no_cidr_vars_without_databricks():
    graph = _make_graph([NodeType.ADLS])
    files = Renderer().render_all(graph, _empty_rbac())
    variables = next(f for f in files if f.filename == "variables.tf")
    assert "vnet_address_space" not in variables.content
    assert "dbw_private_subnet_cidr" not in variables.content


# ── Defaults are sensible ────────────────────────────────────────────────────

def test_cidr_vars_have_sensible_defaults():
    graph = _make_graph([NodeType.DATABRICKS])
    files = Renderer().render_all(graph, _empty_rbac())
    variables = next(f for f in files if f.filename == "variables.tf")
    assert "10.0.0.0/16" in variables.content
    assert "10.0.1.0/24" in variables.content
    assert "10.0.2.0/24" in variables.content
    assert "10.0.3.0/24" in variables.content
