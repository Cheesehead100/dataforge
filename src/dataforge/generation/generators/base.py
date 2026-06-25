"""Abstract base class for all platform layer generators."""

from __future__ import annotations

from abc import ABC, abstractmethod

from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult


class BaseGenerator(ABC):
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
