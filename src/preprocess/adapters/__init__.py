"""v2.0 §8 양식 어댑터 7종 + dispatcher.

각 어댑터는 (file_path, sheet, file_meta) → Iterable[ExtractedRow] 또는
BOM의 경우 (parts, bom_edges) tuple, activity_master는 ProjectMeta를 반환.
"""

from __future__ import annotations

from src.preprocess.adapters.activity_master_meta import (
    ProjectMeta,
    extract_activity_master_meta,
)
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
    "ProjectMeta",
    "SheetMeta",
    "extract_activity_master_meta",
    "extract_base_master_24",
    "extract_bom_ag_grid",
    "extract_changing_parts_list_family",
    "extract_new_parts_list_75",
    "extract_sheet",
    "extract_uae_dev_list",
    "extract_v1_2_template",
]
