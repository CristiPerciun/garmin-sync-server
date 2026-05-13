"""
Client HTTP Xiaomi / Mi Fitness (API Huami legacy, uso non supportato dall'editore).

Autenticazione e lista workout ispirate a progetti open source:
- micw/hacking-mifit-api (login email/password)
- rolandsz/Mi-Fit-and-Zepp-workout-exporter (history.json / detail.json su api-mifit.huami.com)

Possono cessare di funzionare senza preavviso se Xiaomi modifica gli endpoint.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from typing import Any

import httpx

MI_FIT_APP_NAME = "com.xiaomi.hm.health"
MI_FIT_PLATFORM = "web"
DEFAULT_API_BASE = "https://api-mifit.huami.com"
REG_SUCCESS_REDIRECT = (
    "https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html"
)


class MiFitnessAuthError(Exception):
    """Credenziali errate o risposta inattesa da Huami."""


class MiFitnessApiError(Exception):
    """Errore HTTP/API dopo login."""


def _extract_first(qs_val: list[str] | None) -> str | None:
    if not qs_val:
        return None
    v = qs_val[0]
    return str(v).strip() if v is not None else None


def login_with_email_password(
    email: str,
    password: str,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """
    Esegue login Huami e restituisce il JSON di risposta di account.huami.com/v2/client/login
    (deve contenere token_info.app_token e token_info.user_id).
    """
    em = (email or "").strip()
    if not em or not password:
        raise MiFitnessAuthError("Email o password vuoti.")

    auth_url = (
        "https://api-user.huami.com/registrations/"
        + urllib.parse.quote(em, safe="")
        + "/tokens"
    )
    form1 = {
        "state": "REDIRECTION",
        "client_id": "HuaMi",
        "redirect_uri": REG_SUCCESS_REDIRECT,
        "token": "access",
        "password": password,
    }
    with httpx.Client(timeout=timeout) as client:
        r1 = client.post(auth_url, data=form1, follow_redirects=False)
        loc = r1.headers.get("location") or r1.headers.get("Location")
        if r1.status_code not in (301, 302, 303, 307, 308) or not loc:
            try:
                body = r1.text[:800]
            except Exception:
                body = ""
            raise MiFitnessAuthError(
                f"Login step 1: HTTP {r1.status_code}, atteso redirect. {body}"
            )

        pu = urllib.parse.urlparse(loc)
        qs = urllib.parse.parse_qs(pu.query)
        code = _extract_first(qs.get("access"))
        country = _extract_first(qs.get("country_code"))
        if not code:
            raise MiFitnessAuthError("Token di accesso assente nella URL di redirect.")
        if not country:
            country = ""

        login_url = "https://account.huami.com/v2/client/login"
        login_form = {
            "app_name": MI_FIT_APP_NAME,
            "dn": (
                "account.huami.com,api-user.huami.com,api-watch.huami.com,"
                "api-analytics.huami.com,app-analytics.huami.com,api-mifit.huami.com"
            ),
            "device_id": "02:00:00:00:00:00",
            "device_model": "android_phone",
            "app_version": "4.0.9",
            "allow_registration": "false",
            "third_name": "huami",
            "grant_type": "access_token",
            "country_code": country,
            "code": code,
        }
        r2 = client.post(login_url, data=login_form, follow_redirects=True)
        if r2.status_code != 200:
            raise MiFitnessAuthError(
                f"Login step 2: HTTP {r2.status_code} {r2.text[:500]}"
            )
        try:
            data = r2.json()
        except Exception as e:
            raise MiFitnessAuthError(f"Login step 2: JSON non valido: {e}") from e

    if isinstance(data, dict) and data.get("token_info"):
        return data
    if isinstance(data, dict):
        msg = data.get("message") or data.get("error") or str(data)[:400]
        raise MiFitnessAuthError(f"Login fallito: {msg}")
    raise MiFitnessAuthError("Login: risposta inattesa.")


def session_from_login_result(login_json: dict[str, Any]) -> dict[str, Any]:
    """Estrae campi persistenti minimi dalla risposta login."""
    ti = login_json.get("token_info") or {}
    app_token = ti.get("app_token") or ti.get("login_token")
    user_id = ti.get("user_id")
    if not app_token:
        raise MiFitnessAuthError("app_token assente dopo login.")
    if user_id is None:
        raise MiFitnessAuthError("user_id assente dopo login.")
    return {
        "app_token": str(app_token),
        "user_id": str(user_id),
        "app_name": MI_FIT_APP_NAME,
        "api_base": DEFAULT_API_BASE,
    }


def _parse_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(str(val).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_int(val: Any) -> int:
    if val is None or val == "":
        return 0
    try:
        return int(float(str(val).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return 0


# Sport type Zepp/Huami → token compatibile con ActivityUtils (run, ride, walk, …)
_HUAMI_SPORT_KIND: dict[int, str] = {
    1: "run",
    2: "walk",
    3: "ride",
    4: "hike",
    5: "run",
    6: "ride",
    7: "walk",
    8: "run",
    9: "hike",
    10: "workout",
    11: "swim",
    12: "swim",
    14: "workout",
    15: "swim",
    16: "swim",
    19: "workout",
    21: "workout",
    24: "workout",
    27: "workout",
    28: "workout",
    30: "run",
    31: "ride",
    32: "walk",
}


def mi_fitness_sport_token(type_id: Any) -> str:
    try:
        n = int(type_id)
    except (TypeError, ValueError):
        return str(type_id or "unknown").lower().strip()
    return _HUAMI_SPORT_KIND.get(n, f"sport{n}")


# Etichetta italiana breve per activityName / prompt
_HUAMI_LABEL_IT: dict[int, str] = {
    1: "Corsa",
    2: "Camminata",
    3: "Ciclismo",
    4: "Escursionismo",
    5: "Corsa tapis",
    6: "Bici outdoor",
    7: "Passeggiata",
    8: "Trail",
    9: "Trekking",
    10: "Fitness",
    11: "Nuoto",
    12: "Nuoto",
    14: "HIIT",
    15: "Nuoto vasca",
    16: "Nuoto acque libere",
    19: "Ellittica",
    21: "Yoga",
    24: "Ellittica",
    27: "Salto corda",
    28: "Canoa",
    30: "Corsa indoor",
    31: "Spinning",
    32: "Camminata veloce",
}


def mi_fitness_activity_label_it(type_id: Any) -> str:
    try:
        n = int(type_id)
    except (TypeError, ValueError):
        return "Allenamento Mi Fitness"
    return _HUAMI_LABEL_IT.get(n, f"Allenamento (tipo {n})")


def summary_to_normalized_activity(summary: dict[str, Any]) -> dict[str, Any]:
    """
    Converte un elemento di history.data.summary nell'endpoint Huami in un dict
    simile ai campi chiave delle attività Strava/Garmin per ingest unificato.
    """
    trackid = summary.get("trackid")
    raw_type = summary.get("type")
    stype = mi_fitness_sport_token(raw_type)

    moving = _parse_int(summary.get("run_time"))

    distance_m = _parse_float(summary.get("dis")) or 0.0

    cal_f = _parse_float(summary.get("calorie"))

    avg_hrf = _parse_float(summary.get("avg_heart_rate"))
    max_hrf = _parse_float(summary.get("max_heart_rate"))

    label_it = mi_fitness_activity_label_it(raw_type)
    ts = None
    name_guess = f"{label_it} (Mi Fitness)"
    try:
        tid = int(str(trackid))
        ts = datetime.fromtimestamp(tid, tz=timezone.utc)
        local_fmt = ts.strftime("%d/%m %H:%M")
        name_guess = f"{label_it} · {local_fmt}"
    except (TypeError, ValueError):
        pass

    return {
        "_mi_summary": summary,
        "trackid": str(trackid),
        "id": trackid,
        "name": name_guess,
        "sport_type": stype,
        "sport_type_original": raw_type,
        "distance": distance_m,
        "moving_time": moving,
        "elapsed_time": moving,
        "calories": cal_f,
        "average_heartrate": avg_hrf,
        "max_heartrate": max_hrf,
        "start_date": ts.isoformat() if ts else None,
        "device_name": (summary.get("bind_device") or summary.get("source") or "Mi Fitness"),
    }


def fetch_workout_summaries_paginated(
    app_token: str,
    *,
    api_base: str = DEFAULT_API_BASE,
    timeout: float = 60.0,
    since_ts: int | None = None,
    max_summaries: int = 2500,
) -> list[dict[str, Any]]:
    """
    GET /v1/sport/run/history.json ripetuti finché ``next == -1`` o limite sicurezza.
    Se ``since_ts`` è impostato, interrompe la paginazione quando un intero batch
    ha solo trackid < since_ts (storia tipicamente dal più recente).
    """
    base = api_base.rstrip("/")
    out: list[dict[str, Any]] = []
    next_id: str | None = None
    headers = {
        "apptoken": app_token,
        "appPlatform": MI_FIT_PLATFORM,
        "appname": MI_FIT_APP_NAME,
        "User-Agent": " okhttp/5.x MiFitnessExporter/1",
    }

    with httpx.Client(timeout=timeout) as client:
        while len(out) < max_summaries:
            params: dict[str, Any] = {}
            if next_id is not None:
                params["trackid"] = next_id

            r = client.get(
                f"{base}/v1/sport/run/history.json",
                headers=headers,
                params=params or None,
            )
            if r.status_code != 200:
                raise MiFitnessApiError(f"history.json HTTP {r.status_code}: {r.text[:500]}")
            try:
                js = r.json()
            except Exception as e:
                raise MiFitnessApiError(f"history.json JSON: {e}") from e

            if not isinstance(js, dict):
                raise MiFitnessApiError(f"history.json tipo inatteso: {type(js)}")
            err_c = js.get("code")
            if err_c not in (None, 0, 1, "0", "1") and js.get("data") is None:
                msg = js.get("message") if isinstance(js, dict) else str(js)
                raise MiFitnessApiError(f"history.json errore API: {msg}")

            data = js.get("data") or {}
            summaries = data.get("summary") or []
            nx = data.get("next")
            if not isinstance(summaries, list):
                break

            batch_all_old = False
            if summaries and since_ts is not None:
                batch_all_old = True
                for row in summaries:
                    if isinstance(row, dict):
                        try:
                            if int(str(row.get("trackid") or "0")) >= since_ts:
                                batch_all_old = False
                                break
                        except (TypeError, ValueError):
                            batch_all_old = False
                            break

            for row in summaries:
                if isinstance(row, dict):
                    out.append(row)

                if len(out) >= max_summaries:
                    break

            if since_ts is not None and batch_all_old:
                break

            try:
                nxi = int(nx)
            except (TypeError, ValueError):
                nxi = -1
            if nxi == -1:
                break
            next_id = str(nxi)

    return out
