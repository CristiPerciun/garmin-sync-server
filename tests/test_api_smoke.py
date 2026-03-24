"""Smoke test senza Firebase (db può essere None in lifespan)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok():
    from main import app

    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert data.get("status") == "ok" or data.get("service")


def test_openapi_contains_new_paths():
    from main import app

    with TestClient(app) as client:
        r = client.get("/openapi.json")
        assert r.status_code == 200
        paths = r.json().get("paths", {})
        assert "/garmin/sync-today" in paths
        assert "/sync/delta" in paths
        assert "/strava/register-tokens" in paths
