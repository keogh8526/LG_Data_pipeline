"""Canonical project paths."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
EVAL_DIR = DATA_DIR / "eval"

ONTOLOGY_DIR = PROJECT_ROOT / "ontology"
MAPPING_RULES_DIR = ONTOLOGY_DIR / "mapping_rules"

V1_2_SCHEMA_PATH = ONTOLOGY_DIR / "v1_2_schema.json"
