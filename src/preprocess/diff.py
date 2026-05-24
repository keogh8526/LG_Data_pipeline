"""Step 0/4 — golden-diff: auto pipeline output vs. user hand-work.

The user's hand-made preprocessing result is the ground truth. This module
compares the automatic pipeline output against ``data/golden/`` to measure how
often automation diverges from the human.

Step 0 establishes the interface; the comparison logic is implemented in
Step 4 (see the v2 plan). The body is deferred until then.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, Field


class DiffReport(BaseModel):
    """Row-level comparison of auto output against golden ground truth."""

    rows_match: int = 0
    rows_mismatch: int = 0
    rows_only_in_auto: int = 0
    rows_only_in_golden: int = 0
    column_mismatches: dict[str, int] = Field(default_factory=dict)
    sample_mismatches: list[dict[str, object]] = Field(default_factory=list)

    @property
    def match_rate(self) -> float:
        """Fraction of compared rows that match exactly."""
        total = self.rows_match + self.rows_mismatch
        return self.rows_match / total if total else 0.0


def diff_against_golden(
    auto_df: pd.DataFrame,
    golden_df: pd.DataFrame,
    key: str = "base_part_no",
) -> DiffReport:
    """Compare automatic output against golden ground truth.

    Args:
        auto_df: The automatic pipeline output.
        golden_df: The user's hand-made result (ground truth).
        key: Join key for row alignment.

    Returns:
        A :class:`DiffReport`.

    Raises:
        NotImplementedError: Comparison logic is implemented in Step 4 (v2
            plan); the Step 0 deliverable is this interface only.
    """
    raise NotImplementedError("golden diff implemented in Step 4 (v2 plan).")
