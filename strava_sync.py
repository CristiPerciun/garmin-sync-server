"""
Client HTTP Strava (token refresh, lista attività, dettaglio).
Usato da main.py per register-tokens, backfill e delta.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

STRAVA_API = "https://www.strava.com/api/v3"


def strava_refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict[str, Any]:
    r = httpx.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=60.0,
    )
    r.raise_for_status()
    return r.json()


def strava_list_activities(
    access_token: str,
    *,
    after_epoch: int | None = None,
    page: int = 1,
    per_page: int = 200,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if after_epoch is not None:
        params["after"] = after_epoch
    r = httpx.get(
        f"{STRAVA_API}/athlete/activities",
        params=params,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=60.0,
    )
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def strava_get_activity_detail(access_token: str, activity_id: int) -> dict[str, Any]:
    r = httpx.get(
        f"{STRAVA_API}/activities/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=60.0,
    )
    r.raise_for_status()
    return r.json()


def parse_strava_expires_at(exp: Any) -> datetime | None:
    if exp is None:
        return None
    if hasattr(exp, "timestamp"):
        return datetime.fromtimestamp(exp.timestamp(), tz=timezone.utc)
    if isinstance(exp, datetime):
        return exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
    return None
