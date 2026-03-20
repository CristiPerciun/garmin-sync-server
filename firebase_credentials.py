"""
Caricamento account di servizio Firebase da variabili d'ambiente.
Stesso comportamento atteso da main.py e dagli script di verifica.

- FIREBASE_CREDENTIALS_B64: consigliato su Pi/systemd (una sola riga, niente virgolette rotte).
- FIREBASE_CREDENTIALS: JSON come stringa (una riga nel .env; multilinea spesso non funziona con EnvironmentFile systemd).
"""
from __future__ import annotations

import base64
import json
import os
import re

from firebase_admin import credentials


def decode_firebase_b64(b64_value: str) -> dict:
    """
    Decodifica Base64 del file JSON account di servizio.
    Tollera spazi, tab, a capo incollati da mail/editor; aggiunge padding '=' se manca.
    """
    s = re.sub(r"\s+", "", (b64_value or "").strip())
    if not s:
        raise ValueError("FIREBASE_CREDENTIALS_B64 è vuoto dopo aver rimosso spazi/newline")
    pad = (-len(s)) % 4
    if pad:
        s += "=" * pad
    try:
        raw = base64.b64decode(s, validate=False)
    except Exception as e:
        raise ValueError(f"Base64 non decodificabile: {e}") from e
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError("Dopo Base64 il contenuto non è UTF-8 valido") from e
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(
            "Il contenuto decodificato non è JSON valido. "
            "Genera B64 dal file .json originale (vedi encode_firebase_credentials_b64.ps1)."
        ) from e
    if not isinstance(obj, dict):
        raise ValueError("Il JSON deve essere un oggetto (service account)")
    _validate_service_account_dict(obj)
    return obj


def _validate_service_account_dict(obj: dict) -> None:
    for key in ("type", "project_id", "private_key", "client_email"):
        if key not in obj:
            raise ValueError(f"Nel JSON manca la chiave obbligatoria '{key}'")
    if obj.get("type") != "service_account":
        raise ValueError("Il campo 'type' deve essere 'service_account'")


def certificate_from_environment() -> credentials.Certificate:
    """
    Costruisce Certificate da FIREBASE_CREDENTIALS (JSON) o FIREBASE_CREDENTIALS_B64.
    Raises:
        ValueError: variabile assente o formato non valido.
    """
    raw = os.getenv("FIREBASE_CREDENTIALS")
    if raw:
        s = raw.strip().lstrip("\ufeff")
        if s:
            try:
                obj = json.loads(s)
            except json.JSONDecodeError as e:
                raise ValueError(
                    "FIREBASE_CREDENTIALS non è JSON valido. "
                    "Usa una sola riga o passa a FIREBASE_CREDENTIALS_B64."
                ) from e
            if not isinstance(obj, dict):
                raise ValueError("FIREBASE_CREDENTIALS: il JSON deve essere un oggetto")
            _validate_service_account_dict(obj)
            return credentials.Certificate(obj)

    b64 = os.getenv("FIREBASE_CREDENTIALS_B64")
    if b64:
        obj = decode_firebase_b64(b64)
        return credentials.Certificate(obj)

    raise ValueError(
        "Manca FIREBASE_CREDENTIALS o FIREBASE_CREDENTIALS_B64 nell'ambiente. "
        "Sul Pi metti nel .env una riga FIREBASE_CREDENTIALS_B64=... (vedi RPI_DEPLOY.md)."
    )
