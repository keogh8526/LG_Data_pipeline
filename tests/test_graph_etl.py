"""Tests for the Step 6 Neo4j ETL batch builder."""

from __future__ import annotations

import pandas as pd

from src.graph.etl import build_batch


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    parts = pd.DataFrame(
        {"part_no": ["AB1234567", "AB1234568"], "part_name": ["x", "y"]}
    )
    models = pd.DataFrame({"model_code": ["WSED7667M.ABMQEUR"], "region": ["EUR"]})
    events = pd.DataFrame(
        {
            "event_id": ["EVT-1"],
            "base_part_no": ["AB1234567"],
            "new_part_no": ["AB1234568"],
            "model_code": ["WSED7667M.ABMQEUR"],
            "change_type": ["Change"],
            "form_version": ["v1.2"],
            "change_point": ["내열"],
        }
    )
    return parts, models, events


def test_build_batch_counts() -> None:
    batch = build_batch(*_frames())
    assert len(batch.parts) == 2
    assert len(batch.models) == 1
    assert len(batch.change_events) == 1


def test_build_batch_edges() -> None:
    batch = build_batch(*_frames())
    assert batch.changed_from == [{"event_id": "EVT-1", "part_no": "AB1234567"}]
    assert batch.changed_to == [{"event_id": "EVT-1", "part_no": "AB1234568"}]
    assert batch.belongs_to[0]["model_code"] == "WSED7667M.ABMQEUR"


def test_new_event_has_no_changed_from() -> None:
    parts, models, events = _frames()
    events.loc[0, "base_part_no"] = None
    batch = build_batch(parts, models, events)
    assert batch.changed_from == []
    assert len(batch.changed_to) == 1
