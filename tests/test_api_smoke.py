"""Smoke test senza Firebase (db può essere None in lifespan)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok():
    from main import SERVER_VERSION, app

    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert data.get("status") == "ok" or data.get("service")
        assert data.get("version") == SERVER_VERSION
        assert isinstance(SERVER_VERSION, str) and len(SERVER_VERSION) > 0


def test_openapi_contains_new_paths():
    from main import app

    with TestClient(app) as client:
        r = client.get("/openapi.json")
        assert r.status_code == 200
        paths = r.json().get("paths", {})
        assert "/garmin/sync-today" in paths
        assert "/sync/delta" in paths
        assert "/strava/register-tokens" in paths
