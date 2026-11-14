from __future__ import annotations

from glycolens.schemas import CanonicalDataset, StatisticalEvidence
from glycolens.stats.engine import (
    probe_activity_patterns,
    probe_dosing_patterns,
    probe_sleep_patterns,
    probe_vitals_patterns,
    query_custom_cohort_split,
)
from glycolens.stats.profiler import (
    build_data_profile,
    build_day_replays,
    fit_walk_model,
    simulate_walk,
)


def run_all_probes(ds: CanonicalDataset) -> list[StatisticalEvidence]:
    """Run all 4 domain statistical hypothesis test suites deterministically."""
    return (
        probe_activity_patterns(ds)
        + probe_sleep_patterns(ds)
        + probe_dosing_patterns(ds)
        + probe_vitals_patterns(ds)
    )


__all__ = [
    "build_data_profile",
    "build_day_replays",
    "fit_walk_model",
    "simulate_walk",
    "probe_activity_patterns",
    "probe_sleep_patterns",
    "probe_dosing_patterns",
    "probe_vitals_patterns",
    "query_custom_cohort_split",
    "run_all_probes",
]
