"""Structural validation and cycle detection for FlowGraph."""

from __future__ import annotations

from collections import defaultdict, deque

from dataforge.models.flow_graph import FlowGraph


class GraphValidationError(Exception):
    pass


def validate_graph(graph: FlowGraph) -> FlowGraph:
    """Run structural checks on a FlowGraph. Raises GraphValidationError on failure."""
    _check_no_orphan_nodes(graph)
    cycles = detect_cycles(graph)
    if cycles:
        cycle_str = " → ".join(cycles[0])
        raise GraphValidationError(
            f"FlowGraph contains a cycle: {cycle_str}. "
            "Data flows must be acyclic (DAG)."
        )
    return graph


def detect_cycles(graph: FlowGraph) -> list[list[str]]:
    """Return a list of cycles found via DFS. Empty list = no cycles (valid DAG)."""
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        adjacency[edge.source].append(edge.target)

    visited: set[str] = set()
    in_stack: set[str] = set()
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        in_stack.add(node)
        path.append(node)
        for neighbour in adjacency.get(node, []):
            if neighbour not in visited:
                dfs(neighbour, path)
            elif neighbour in in_stack:
                cycle_start = path.index(neighbour)
                cycles.append(path[cycle_start:] + [neighbour])
        path.pop()
        in_stack.discard(node)

    for node in graph.nodes:
        if node.id not in visited:
            dfs(node.id, [])

    return cycles


def _check_no_orphan_nodes(graph: FlowGraph) -> None:
    connected = set()
    for edge in graph.edges:
        connected.add(edge.source)
        connected.add(edge.target)
    node_ids = {n.id for n in graph.nodes}
    orphans = node_ids - connected
    if orphans:
        raise GraphValidationError(
            f"Orphan nodes (not connected by any edge): {sorted(orphans)}. "
            "All nodes must participate in at least one edge."
        )


def topological_order(graph: FlowGraph) -> list[str]:
    """Return nodes in topological order (sources first). Assumes graph is a valid DAG."""
    adjacency: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {n.id: 0 for n in graph.nodes}
    for edge in graph.edges:
        adjacency[edge.source].append(edge.target)
        in_degree[edge.target] += 1

    queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
    order: list[str] = []
    while queue:
        node_id = queue.popleft()
        order.append(node_id)
        for neighbour in adjacency[node_id]:
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                queue.append(neighbour)
    return order
