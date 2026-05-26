"""v2.0 (D-011 후) 양식 어댑터 + dispatcher.

각 어댑터는 ``(file_path, sheet, file_meta) → list[ExtractedRow]`` 또는
BOM의 경우 ``BomExtraction``을 반환.

D-011: activity_master_meta 어댑터 제거. ProjectMeta 의존성도 함께 제거.
"""

from __future__ import annotations

from src.preprocess.adapters.base import (
    BomExtraction,
    ExtractedRow,
    SheetMeta,
)
from src.preprocess.adapters.base_master_24 import extract_base_master_24
from src.preprocess.adapters.bom_ag_grid import extract_bom_ag_grid
from src.preprocess.adapters.changing_parts_list import (
    extract_changing_parts_list_family,
)
from src.preprocess.adapters.dispatcher import extract_sheet
from src.preprocess.adapters.new_parts_list_75 import extract_new_parts_list_75
from src.preprocess.adapters.uae_dev_list import extract_uae_dev_list
from src.preprocess.adapters.v1_2_template import extract_v1_2_template

__all__ = [
    "BomExtraction",
    "ExtractedRow",
    "SheetMeta",
    "extract_base_master_24",
    "extract_bom_ag_grid",
    "extract_changing_parts_list_family",
    "extract_new_parts_list_75",
    "extract_sheet",
    "extract_uae_dev_list",
    "extract_v1_2_template",
]
