"""DataProductGenerator — orchestrates all platform layer generators (L3–L9)."""

from __future__ import annotations

from dataforge.generation.generators.adf_pipeline import AdfPipelineGenerator
from dataforge.generation.generators.ansible import AnsibleGenerator
from dataforge.generation.generators.cicd import CiCdGenerator
from dataforge.generation.generators.cost_optimizer import CostOptimizationGenerator
from dataforge.generation.generators.drift import DriftDetectionGenerator
from dataforge.generation.generators.governance import GovernanceGenerator
from dataforge.generation.generators.networking import NetworkingGenerator
from dataforge.generation.generators.monitoring import MonitoringGenerator
from dataforge.generation.generators.quality import QualityGenerator
from dataforge.generation.generators.readiness import ReadinessGenerator
from dataforge.generation.generators.sre_dashboard import SreDashboardGenerator
from dataforge.models.data_product import DataProduct
from dataforge.models.flow_graph import FlowGraph
from dataforge.models.rbac import RbacResult
from dataforge.models.terraform import GenerationResult

_GENERATORS = [
    NetworkingGenerator(),         # L10 – private endpoints + DNS + sequencing
    GovernanceGenerator(),         # L3
    QualityGenerator(),            # L4
    CiCdGenerator(),               # L5
    MonitoringGenerator(),         # L6
    AnsibleGenerator(),            # L7
    ReadinessGenerator(),          # L8 – readiness gate
    DriftDetectionGenerator(),     # L8 – drift detection (completes L8)
    AdfPipelineGenerator(),        # ADF pipelines (data-plane)
    SreDashboardGenerator(),       # L9 – SRE workbook + runbook
    CostOptimizationGenerator(),   # L9 – cost optimisation engine
]


class DataProductGenerator:
    """Runs L3–L9 generators against a DataProduct and returns all extra output files."""

    def generate(
        self,
        product: DataProduct,
        graph: FlowGraph,
        rbac: RbacResult,
    ) -> GenerationResult:
        all_files = []
        all_warnings: list[str] = []

        for gen in _GENERATORS:
            if gen.applicable(product):
                result = gen.generate(product, graph, rbac)
                all_files.extend(result.files)
                all_warnings.extend(result.warnings)

        return GenerationResult(files=all_files, warnings=all_warnings)
