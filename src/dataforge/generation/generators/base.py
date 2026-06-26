"""Abstract base class for all platform layer generators.

Every generator in the generators/ package extends BaseGenerator and follows
the same two-method contract: applicable() acts as a fast guard that decides
whether this layer is relevant to the product, and generate() does the actual
template rendering. DataProductGenerator calls both in sequence for each
registered generator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult


class BaseGenerator(ABC):
    """Contract for a single platform layer generator (L3 through L10)."""

    @abstractmethod
    def applicable(self, product: DataProduct) -> bool:
        """Return True if this generator should produce output for the given product."""
        ...

    @abstractmethod
    def generate(
        self,
        product: DataProduct,
        graph: FlowGraph,
        rbac: RbacResult,
    ) -> GenerationResult:
        """Produce output files for this platform layer."""
        ...
