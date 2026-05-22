"""Tests for the Step 10 FastAPI backend."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.app import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_validate_endpoint_passes_clean_bom() -> None:
    response = client.post(
        "/api/validate", json={"bom": [{"part_no": "AB1234567"}]}
    )
    assert response.status_code == 200
    assert response.json()["passed"] is True


def test_validate_endpoint_flags_invalid() -> None:
    response = client.post("/api/validate", json={"bom": [{"part_no": "123"}]})
    assert response.status_code == 200
    assert response.json()["passed"] is False


def test_draft_endpoint_deferred() -> None:
    response = client.post("/api/draft", json={"change_point": "x"})
    assert response.status_code == 501
