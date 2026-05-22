"""Synthetic Excel fixture generator.

Real LG master files are not yet available. These synthetic workbooks mimic the
coarse shape (sheet count, column width, marker cells) of each form version so
the deterministic pipeline code can be exercised. They do NOT reproduce the
real R1~R5 multi-header complexity — see ``# TODO(real-data)``.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

FIXTURE_DIR = Path(__file__).resolve().parent


def _save(workbook: openpyxl.Workbook, name: str) -> Path:
    path = FIXTURE_DIR / name
    workbook.save(path)
    return path


def make_v12() -> Path:
    """Create a v1.2-shaped fixture (History sheet + ~59 cols)."""
    wb = openpyxl.Workbook()
    main = wb.active
    main.title = "WSED7667M_Best-1_EUR"
    main.append(["Common", "FMEA", "HSMS"])
    headers = ["Base P/No", "New P/No", "Class Desc.", "BOM Level", "Part Type",
               "구분", "변경점", "변경사유", "Qty", "Base Model"]
    main.append(headers + [f"col{i}" for i in range(len(headers), 59)])
    main.append(["AB1234567", "AB1234568", "Bracket", 2, "단품",
                 "Change", "내열 보강", "필드 불량", 1, "WSED7667M"]
                + [None] * (59 - 10))
    history = wb.create_sheet("History")
    history.append(["version", "released_at", "change_summary", "updated_by"])
    history.append(["v1.2", "2025-06-01", "통합 양식", "관리자"])
    return _save(wb, "sample_v12.xlsx")


def make_96col() -> Path:
    """Create a 96col-shaped fixture (grouped header + aaaa marker)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "공통b_DRBFMa_부품a"
    ws.append(["공통b", "DRBFMa", "부품a"] + [None] * 93)
    for _ in range(5):
        ws.append([None] * 96)
    ws.append(["aaaa"] + [None] * 95)
    ws.append(["CP", "PP", "DV", "PV", "PQ"] + [None] * 91)
    ws.append([f"col{i}" for i in range(96)])
    return _save(wb, "sample_96col.xlsx")


def make_56col() -> Path:
    """Create a 56col-shaped fixture (Better sheet name)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Better-1"
    ws.append(["Base Model", "Suffix"] + [f"col{i}" for i in range(2, 56)])
    ws.append([None] * 56)
    return _save(wb, "sample_56col.xlsx")


def make_20col() -> Path:
    """Create a 20col-shaped fixture (simple single-header list)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "홍길동작업"
    ws.append([None] * 20)
    ws.append(["Base P/No", "New P/No", "Part Name"] + [f"c{i}" for i in range(3, 20)])
    ws.append(["CD1234567", "CD1234568", "Cover"] + [None] * 17)
    return _save(wb, "sample_20col.xlsx")


def make_all() -> list[Path]:
    """Generate every fixture workbook.

    Returns:
        Paths of the generated workbooks.
    """
    return [make_v12(), make_96col(), make_56col(), make_20col()]


if __name__ == "__main__":
    for created in make_all():
        print(f"created {created}")
