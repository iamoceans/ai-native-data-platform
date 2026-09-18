"""Deterministic demo data generator (spec section 26).

Synthetic mobile-app data only - no company data, no account data. The module
is importable from scripts and tests; generation is a pure function of
(seed, as_of, days, scale, scenario, generator_version).
"""

from demo.generator import GENERATOR_VERSION, GenerationResult, generate
from demo.scenarios import SCENARIOS, ScalePreset, scale_preset

__all__ = [
    "GENERATOR_VERSION",
    "SCENARIOS",
    "GenerationResult",
    "ScalePreset",
    "generate",
    "scale_preset",
]
