"""Deterministic analysis kernel (spec sections 16, 17).

Pure functions over already-aggregated rows; no database access, no LLM, no
``eval``. All arithmetic uses ``Decimal`` so every acceptance number can be
recomputed from the stored query results.
"""

from app.analysis.compare import ComparisonResult, compare_totals
from app.analysis.contribution import (
    ContributionResult,
    GroupContribution,
    decompose_contribution,
)
from app.analysis.drivers import DriverDecomposition, decompose_revenue
from app.analysis.join import JoinResult, join_results
from app.analysis.relations import JoinRelation, load_relations
from app.analysis.types import AnalysisError

__all__ = [
    "AnalysisError",
    "ComparisonResult",
    "ContributionResult",
    "DriverDecomposition",
    "GroupContribution",
    "JoinRelation",
    "JoinResult",
    "compare_totals",
    "decompose_contribution",
    "decompose_revenue",
    "join_results",
    "load_relations",
]
