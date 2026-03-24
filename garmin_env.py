"""Normalizza variabili d'ambiente prima di importare/inizializzare garth (garminconnect)."""

import os


def env_flag_true(name: str) -> bool:
    """True se la variabile è 1/true/yes/on (case-insensitive)."""
    v = (os.environ.get(name) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def unset_garth_home_if_incomplete() -> None:
    """
    Se GARTH_HOME punta a una cartella senza oauth1_token.json, garth.Client()
    fallisce in __init__ (_auto_resume → load). Il server salva i token su Firestore,
    non in garth_tokens: con .env.example molti hanno GARTH_HOME e cartella vuota.
    """
    gh = (os.environ.get("GARTH_HOME") or "").strip()
    if not gh:
        return
    base = os.path.abspath(os.path.expanduser(gh))
    oauth1 = os.path.join(base, "oauth1_token.json")
    if not os.path.isfile(oauth1):
        os.environ.pop("GARTH_HOME", None)
