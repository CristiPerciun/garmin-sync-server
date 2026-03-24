"""garmin_env: flag ambiente condivisi da main e strava_sync."""
from __future__ import annotations

import os

import pytest


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("", False),
        ("no", False),
    ],
)
def test_env_flag_true(raw, expected):
    from garmin_env import env_flag_true

    os.environ["__GARMIN_ENV_TEST__"] = raw
    try:
        assert env_flag_true("__GARMIN_ENV_TEST__") is expected
    finally:
        os.environ.pop("__GARMIN_ENV_TEST__", None)
