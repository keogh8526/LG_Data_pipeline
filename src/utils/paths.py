"""Canonical project paths."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# --- Data layers ----------------------------------------------------------
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
GOLDEN_DIR = DATA_DIR / "golden"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
QUARANTINE_DIR = DATA_DIR / "quarantine"
REPORTS_DIR = DATA_DIR / "reports"

# --- Config ---------------------------------------------------------------
CONFIG_DIR = PROJECT_ROOT / "config"
FORM_SIGNATURES_PATH = CONFIG_DIR / "form_signatures.yaml"
MAPPING_RULES_DIR = CONFIG_DIR / "mapping_rules"
NORMALIZATION_PATH = CONFIG_DIR / "normalization.yaml"
AXIOMS_PATH = CONFIG_DIR / "axioms.yaml"

# --- Ontology -------------------------------------------------------------
ONTOLOGY_DIR = PROJECT_ROOT / "src" / "ontology"
SCHEMA_JSON_PATH = ONTOLOGY_DIR / "schema.json"
