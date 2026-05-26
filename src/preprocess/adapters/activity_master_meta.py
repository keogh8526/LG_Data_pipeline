"""v2.0 §8-7 — Activity Master 메타 (11~13 col) 어댑터.

데이터 행이 아니라 **메타정보**. project_meta(date, phase_dates, description)를
``preprocessing_runs.config_snapshot.project_meta``로 적재.

change_events에는 들어가지 않음 (ExtractedRow 0개 yield, ProjectMeta dict 반환).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.preprocess.adapters.base import normalize_cell_text
from src.utils.excel import SheetData
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class ProjectMeta:
    """Activity Master에서 추출한 프로젝트 메타."""

    source_file: str
    source_sheet: str
    date: str | None = None
    phase_dates: dict[str, str] = field(default_factory=dict)
    description: str | None = None
    raw_text: list[str] = field(default_factory=list)


_DATE_RE = re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})")
_PHASE_KEYS = {
    "1차 초품 의뢰",
    "1차 초품 완료",
    "2차 초품 의뢰",
    "2차 초품 완료",
    "R&D Result",
    "양산 적용",
    "양산일",
}


def _parse_date(text: str) -> str | None:
    m = _DATE_RE.search(text)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return None


def extract_activity_master_meta(
    file_path: Path,
    sheet: SheetData,
    file_meta: dict[str, Any] | None = None,
) -> ProjectMeta:
    """Activity Master 시트 → ProjectMeta."""
    meta = ProjectMeta(source_file=file_path.name, source_sheet=sheet.name)
    file_meta = file_meta or {}

    # 전체 셀 텍스트 수집
    texts: list[str] = []
    for r in sheet.rows[:50]:  # 상단 50행만
        for cell in r:
            t = normalize_cell_text(cell)
            if t:
                texts.append(t)
    meta.raw_text = texts

    # 첫 발견 날짜 = project date
    for t in texts:
        d = _parse_date(t)
        if d:
            meta.date = d
            break

    # phase_dates: "키:" 다음 셀에서 날짜
    for i, t in enumerate(texts[:-1]):
        for key in _PHASE_KEYS:
            if key in t:
                # 다음 몇 개 텍스트에서 날짜 찾기
                for follow in texts[i + 1 : i + 4]:
                    d = _parse_date(follow)
                    if d:
                        meta.phase_dates[key] = d
                        break

    # description: "개발 사유" 다음 긴 텍스트
    for i, t in enumerate(texts[:-1]):
        if "개발 사유" in t or "사유" == t:
            for follow in texts[i + 1 : i + 5]:
                if len(follow) > 20:
                    meta.description = follow
                    break
            if meta.description:
                break

    log.info(
        "adapter.activity.extracted",
        file=file_path.name,
        date=meta.date,
        phases=len(meta.phase_dates),
    )
    return meta
