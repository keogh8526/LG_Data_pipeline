"""Step 1 — deterministic form-version classifier (file + per-sheet).

Classifies against the weighted signatures in ``config/form_signatures.yaml``.
No LLM: every signal is a structural, reproducible check. Files matching no
signature fall back to ``"unknown"``; files matching two versions at threshold
are flagged ``needs_review``. A per-sheet view is exposed via
``ClassificationResult.sheet_results`` so multi-sheet workbooks can be
inspected individually.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.utils.excel import SheetData, read_workbook
from src.utils.logging import get_logger
from src.utils.paths import FORM_SIGNATURES_PATH

log = get_logger(__name__)

_EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
_SCAN_ROWS = 12
_SCAN_COLS = 14

# Signals that only make sense at the workbook level (refer to other sheets).
_FILE_LEVEL_SIGNALS = {"sheet_exists"}


@dataclass
class FormFeatures:
    """Structural features extracted from a workbook or a single sheet."""

    sheet_names: list[str]
    max_cols: int
    cell_texts: list[str]


@dataclass
class SheetResult:
    """Per-sheet classification result."""

    sheet_name: str
    form_version: str
    confidence: float
    evidence: dict[str, float] = field(default_factory=dict)


@dataclass
class ClassificationResult:
    """File-level classification with per-sheet breakdown."""

    file_path: str
    form_version: str
    confidence: float
    needs_review: bool = False
    evidence: dict[str, float] = field(default_factory=dict)
    features: FormFeatures | None = None
    sheet_results: dict[str, SheetResult] = field(default_factory=dict)


def load_signatures(path: Path = FORM_SIGNATURES_PATH) -> dict[str, Any]:
    """Load the form-signature config.

    Args:
        path: Path to ``form_signatures.yaml``.

    Returns:
        The parsed ``versions`` mapping.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["versions"]


def _collect_top_left(sheet: SheetData) -> list[str]:
    """Return non-empty top-left cell texts (stripped) for one sheet."""
    texts: list[str] = []
    for row in sheet.rows[:_SCAN_ROWS]:
        for cell in row[:_SCAN_COLS]:
            if cell is not None and cell != "":
                texts.append(str(cell).strip())
    return texts


def extract_features(path: Path) -> FormFeatures:
    """Extract workbook-level features (used for the file-level decision).

    Args:
        path: Path to the Excel file.

    Returns:
        A populated :class:`FormFeatures` across all sheets.
    """
    sheets = read_workbook(path)
    max_cols = max((s.max_col for s in sheets), default=0)
    cell_texts: list[str] = []
    for sheet in sheets:
        cell_texts.extend(_collect_top_left(sheet))
    return FormFeatures(
        sheet_names=[s.name for s in sheets],
        max_cols=max_cols,
        cell_texts=cell_texts,
    )


def _extract_sheet_features(sheet: SheetData) -> FormFeatures:
    """Per-sheet features. ``sheet_names`` only carries this sheet's own name
    so the per-sheet decision does not depend on sibling sheets."""
    return FormFeatures(
        sheet_names=[sheet.name],
        max_cols=sheet.max_col,
        cell_texts=_collect_top_left(sheet),
    )


def match_signal(
    features: FormFeatures,
    signal: dict[str, Any],
    *,
    scope: str = "file",
) -> bool:
    """Return True if a single signature signal matches the features.

    Args:
        features: Extracted features.
        signal: One signal entry from the signature config.
        scope: ``"file"`` matches every signal; ``"sheet"`` skips file-level
            signals (returns False) so per-sheet scoring is independent.

    Returns:
        Whether the signal matches.
    """
    signal_type = signal["type"]
    if scope == "sheet" and signal_type in _FILE_LEVEL_SIGNALS:
        return False
    lower_sheets = [s.lower() for s in features.sheet_names]
    lower_cells = [c.lower() for c in features.cell_texts]

    if signal_type == "sheet_exists":
        return str(signal["name"]).strip().lower() in lower_sheets
    if signal_type == "sheet_name_contains":
        kws = [str(k).lower() for k in signal["keywords"]]
        return any(kw in name for name in lower_sheets for kw in kws)
    if signal_type == "col_count_range":
        low, high = signal["range"]
        return low <= features.max_cols <= high
    if signal_type == "header_text_contains":
        kws = [str(k).lower() for k in signal["keywords"]]
        return any(kw in cell for cell in lower_cells for kw in kws)
    if signal_type == "marker_cell":
        return str(signal["value"]).lower() in lower_cells
    if signal_type == "stage_row":
        markers = {str(m) for m in signal["markers"]}
        return len(markers & set(features.cell_texts)) >= 4
    log.warning("classify.unknown_signal", signal_type=signal_type)
    return False


def _score_version(
    features: FormFeatures, spec: dict[str, Any], *, scope: str = "file"
) -> float:
    """Sum the matched signal weights for one version spec."""
    total = 0.0
    for signal in spec["signals"]:
        if match_signal(features, signal, scope=scope):
            total += float(signal["weight"])
    return round(total, 4)


def _decide(
    versions: dict[str, Any], features: FormFeatures, *, scope: str
) -> tuple[str, float, bool, dict[str, float]]:
    """Common scoring + threshold + needs_review logic."""
    scores = {name: _score_version(features, spec, scope=scope) for name, spec in versions.items()}
    passed = [
        name
        for name, spec in versions.items()
        if scores[name] >= float(spec["threshold"])
    ]
    if not passed:
        return "unknown", 0.0, False, scores

    version = max(passed, key=lambda n: scores[n])
    confidence = scores[version]
    needs_review = len(passed) >= 2
    if needs_review:
        confidence = round(confidence * 0.7, 4)
    return version, confidence, needs_review, scores


def classify_form(
    path: Path, signatures: dict[str, Any] | None = None
) -> ClassificationResult:
    """Classify a workbook's form version and every sheet inside it.

    Args:
        path: Path to the Excel file.
        signatures: Optional pre-loaded signature config.

    Returns:
        A :class:`ClassificationResult` with file-level fields plus
        ``sheet_results`` for each worksheet.
    """
    versions = signatures if signatures is not None else load_signatures()
    sheets = read_workbook(path)
    sheet_names: list[str] = [s.name for s in sheets]

    # Per-sheet pass.
    sheet_results: dict[str, SheetResult] = {}
    for sheet in sheets:
        features = _extract_sheet_features(sheet)
        version, confidence, _needs_review, scores = _decide(
            versions, features, scope="sheet"
        )
        sheet_results[sheet.name] = SheetResult(
            sheet_name=sheet.name,
            form_version=version,
            confidence=confidence,
            evidence=scores,
        )

    # File-level pass (all sheets aggregated; lets file-level signals fire).
    cell_texts: list[str] = []
    for sheet in sheets:
        cell_texts.extend(_collect_top_left(sheet))
    max_cols = max((s.max_col for s in sheets), default=0)
    file_features = FormFeatures(
        sheet_names=sheet_names, max_cols=max_cols, cell_texts=cell_texts
    )
    version, confidence, needs_review, scores = _decide(
        versions, file_features, scope="file"
    )

    # If file-level is unknown but per-sheet agrees on a single non-unknown
    # version, lift that to the file level — common for v1.2 templates whose
    # main data sheet alone fails the History signal.
    if version == "unknown":
        votes = Counter(
            r.form_version
            for r in sheet_results.values()
            if r.form_version != "unknown"
        )
        if votes:
            version, _ = votes.most_common(1)[0]
            confidence = round(max(r.confidence for r in sheet_results.values()), 4)

    log.info(
        "classify.result",
        file=path.name,
        version=version,
        confidence=confidence,
        needs_review=needs_review,
        sheets=len(sheet_results),
    )
    return ClassificationResult(
        file_path=str(path),
        form_version=version,
        confidence=confidence,
        needs_review=needs_review,
        evidence=scores,
        features=file_features,
        sheet_results=sheet_results,
    )


def classify_dir(directory: Path) -> list[ClassificationResult]:
    """Classify every Excel file under a directory.

    Args:
        directory: Directory to scan recursively.

    Returns:
        One :class:`ClassificationResult` per Excel file.
    """
    signatures = load_signatures()
    results: list[ClassificationResult] = []
    for file in sorted(directory.rglob("*")):
        if file.suffix.lower() in _EXCEL_SUFFIXES:
            results.append(classify_form(file, signatures))
    return results
