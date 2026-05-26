"""양식 어댑터 dispatcher.

분류기가 부여한 ``form_id``(또는 sub-variant)를 받아 적절한 어댑터로 라우팅.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Union

# D-011: activity_master_meta 어댑터 제거 (project_meta는 BOM Agent 답변에 안 씀).
from src.preprocess.adapters.base import BomExtraction, ExtractedRow
from src.preprocess.adapters.base_master_24 import extract_base_master_24
from src.preprocess.adapters.bom_ag_grid import extract_bom_ag_grid
from src.preprocess.adapters.changing_parts_list import (
    extract_changing_parts_list_family,
)
from src.preprocess.adapters.new_parts_list_75 import extract_new_parts_list_75
from src.preprocess.adapters.uae_dev_list import extract_uae_dev_list
from src.preprocess.adapters.v1_2_template import extract_v1_2_template
from src.utils.excel import SheetData
from src.utils.logging import get_logger

log = get_logger(__name__)

ExtractResult = Union[list[ExtractedRow], BomExtraction]


# form_id (또는 sub-variant) → 어댑터 함수
_CHANGING = {
    "변경부품_list_family",
    "변경부품_list_91",
    "변경부품_list_95",
    "변경부품_list_96",
    "변경부품_list_97",
}


def extract_sheet(
    file_path: Path,
    sheet: SheetData,
    form_id: str,
    file_meta: dict[str, Any] | None = None,
) -> ExtractResult:
    """form_id 기반 어댑터 dispatch.

    Returns:
        - 일반 양식: ``list[ExtractedRow]``
        - BOM_ag_grid_36: ``BomExtraction``
        - (D-011: activity_master_meta 제거됨)
        - unknown / error: ``[]``
    """
    file_meta = file_meta or {}

    if form_id in _CHANGING:
        return list(
            extract_changing_parts_list_family(file_path, sheet, file_meta)
        )
    if form_id == "신규부품리스트_75":
        return list(extract_new_parts_list_75(file_path, sheet, file_meta))
    if form_id == "BOM_ag_grid_36":
        return extract_bom_ag_grid(file_path, sheet, file_meta)
    if form_id == "v1_2_template_59":
        return list(extract_v1_2_template(file_path, sheet, file_meta))
    if form_id == "base_master_24":
        return list(extract_base_master_24(file_path, sheet, file_meta))
    if form_id == "UAE_신규개발_58":
        return list(extract_uae_dev_list(file_path, sheet, file_meta))
    # D-011: activity_master_meta는 제거 — unknown으로 분류돼 []반환.
    log.warning("dispatcher.unknown_form", form=form_id, file=file_path.name, sheet=sheet.name)
    return []
