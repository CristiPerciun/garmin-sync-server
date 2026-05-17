"""
Client HTTP Xiaomi / Mi Fitness (API Huami legacy, uso non supportato dall'editore).

Autenticazione e lista workout ispirate a progetti open source:
- micw/hacking-mifit-api (login email/password)
- rolandsz/Mi-Fit-and-Zepp-workout-exporter (history.json / detail.json su api-mifit.huami.com)

Possono cessare di funzionare senza preavviso se Xiaomi modifica gli endpoint.
"""
from __future__ import annotations

import os
import uuid
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

MI_FIT_APP_NAME = "com.xiaomi.hm.health"
MI_FIT_PLATFORM = "web"
# Cluster Huami storico: EU (Germania) vs globale (spesso account extra-EU).
DEFAULT_API_BASE_EU = "https://api-mifit-de.huami.com"
DEFAULT_API_BASE_GLOBAL = "https://api-mifit.huami.com"
# Back-compat: stesso host di prima (globale).
DEFAULT_API_BASE = DEFAULT_API_BASE_GLOBAL
REG_SUCCESS_REDIRECT = (
    "https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html"
)

# Stesso schema huami-token / Zepp (registrazione token cifrata).
_ZEPP_AES_KEY = b"xeNtBVqzDc6tuNTh"
_ZEPP_AES_IV = b"MAAAYAAAAAAAAABg"

ZEPP_US2_TOKENS_URL = "https://api-user-us2.zepp.com/v2/registrations/tokens"
ZEPP_US2_LOGIN_URL = "https://api-mifit-us2.zepp.com/v2/client/login"
# Zepp UE: i vecchi *.eu2.zepp.com sono NXDOMAIN; i CNAME attuali sono *.de2 (stesso alb eu-central).
ZEPP_EU_TOKENS_URL = "https://api-user-de2.zepp.com/v2/registrations/tokens"
ZEPP_EU_LOGIN_URL = "https://api-mifit-de2.zepp.com/v2/client/login"

_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


def normalize_mifitness_region(region: str | None) -> str:
    """eu = prima EU (Huami DE + Zepp EU/de2); us = globale + Zepp US2."""
    env = (os.getenv("MI_FITNESS_REGION") or "").strip().lower()
    default = env if env in ("eu", "us") else "eu"
    r = (region or "").strip().lower()
    if not r:
        return default
    if r in ("eu", "europe", "de", "it", "ita", "fr", "es", "uk", "gb", "nl", "be", "at", "ch", "pl"):
        return "eu"
    if r in ("us", "usa", "global", "row"):
        return "us"
    return default


def default_api_base_for_region(region_norm: str) -> str:
    return (
        DEFAULT_API_BASE_EU
        if region_norm == "eu"
        else DEFAULT_API_BASE_GLOBAL
    )


def _country_state_for_code(country_code: str | None) -> str:
    c = (country_code or "DE").strip().upper() or "DE"
    return {
        "IT": "IT-MI",
        "DE": "DE-BE",
        "FR": "FR-IDF",
        "ES": "ES-MD",
        "NL": "NL-NH",
        "BE": "BE-BRU",
        "AT": "AT-9",
        "PL": "PL-MZ",
        "GB": "GB-ENG",
        "UK": "GB-ENG",
        "US": "US-NY",
    }.get(c, "DE-BE")


class MiFitnessAuthError(Exception):
    """Credenziali errate o risposta inattesa da Huami."""


class MiFitnessTransportError(Exception):
    """DNS/timeout/connessione verso Xiaomi/Huami/Zepp (non credenziali)."""


class MiFitnessApiError(Exception):
    """Errore HTTP/API dopo login."""


def _extract_first(qs_val: list[str] | None) -> str | None:
    if not qs_val:
        return None
    v = qs_val[0]
    return str(v).strip() if v is not None else None


def _parse_redirect_query_dict(location: str) -> dict[str, list[str]]:
    """Unisce query e fragment (Huami a volte mette parametri dopo #)."""
    pu = urllib.parse.urlparse(location)
    merged: dict[str, list[str]] = {}
    if pu.query:
        for k, v in urllib.parse.parse_qs(
            pu.query, keep_blank_values=True
        ).items():
            merged[k] = v
    if pu.fragment:
        frag = pu.fragment.lstrip("?")
        for k, v in urllib.parse.parse_qs(frag, keep_blank_values=True).items():
            merged[k] = v
    return merged


def _huami_redirect_error_message(qs: dict[str, list[str]]) -> str | None:
    err = _extract_first(qs.get("error"))
    msg = _extract_first(qs.get("message"))
    desc = _extract_first(qs.get("description"))
    parts: list[str] = []
    if err:
        parts.append(f"error={err}")
    if msg:
        parts.append(msg)
    if desc:
        parts.append(desc)
    if not parts:
        return None
    detail = " ".join(parts)
    if err == "401":
        return (
            "Huami/Zepp: error=401 — credenziali non accettate, account assente o "
            "registrato solo sull'app Zepp/Mi Fitness (prova stesse credenziali nell'app)."
        )
    return f"Huami/Zepp redirect: {detail}"


def _zepp_encrypt_form_body(payload_dict: dict[str, Any]) -> bytes:
    encoded = urllib.parse.urlencode(payload_dict, doseq=True).encode()
    cipher = AES.new(_ZEPP_AES_KEY, AES.MODE_CBC, iv=_ZEPP_AES_IV)
    return cipher.encrypt(pad(encoded, AES.block_size))


def _post_huami_account_login(
    client: httpx.Client, access_code: str, country_code: str
) -> dict[str, Any]:
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
        "country_code": country_code or "",
        "code": access_code,
    }
    r2 = client.post(login_url, data=login_form, follow_redirects=True)
    if r2.status_code != 200:
        raise MiFitnessAuthError(
            f"Login step 2 (account.huami.com): HTTP {r2.status_code} {r2.text[:500]}"
        )
    try:
        return r2.json()
    except Exception as e:
        raise MiFitnessAuthError(f"Login step 2: JSON non valido: {e}") from e


def _validate_login_json(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("token_info"):
        return data
    msg = data.get("message") or data.get("error") or str(data)[:400]
    raise MiFitnessAuthError(f"Login fallito: {msg}")


def _login_zepp_encrypted_one(
    client: httpx.Client,
    email: str,
    password: str,
    *,
    tokens_url: str,
    login_url: str,
    zepp_region: str,
    country_code_hint: str,
) -> dict[str, Any]:
    payload_dict: dict[str, Any] = {
        "emailOrPhone": email,
        "state": "REDIRECTION",
        "client_id": "HuaMi",
        "password": password,
        "redirect_uri": REG_SUCCESS_REDIRECT,
        "region": zepp_region,
        "token": ["access", "refresh"],
        "country_code": country_code_hint,
    }
    enc_body = _zepp_encrypt_form_body(payload_dict)
    tok_headers = {
        "app_name": "com.huami.midong",
        "appname": "com.huami.midong",
        "cv": "151689_9.12.5",
        "v": "2.0",
        "appplatform": "android_phone",
        "vb": "202509151347",
        "vn": "9.12.5",
        "user-agent": "Zepp/9.12.5 (Pixel 4; Android 12; Density/2.75)",
        "x-hm-ekv": "1",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "accept-encoding": "gzip",
    }
    r1 = client.post(
        tokens_url,
        content=enc_body,
        headers=tok_headers,
        follow_redirects=False,
    )
    loc = r1.headers.get("location") or r1.headers.get("Location")
    if r1.status_code not in _REDIRECT_STATUS or not loc:
        snippet = r1.text[:500] if r1.text else ""
        raise MiFitnessAuthError(
            f"Zepp tokens: HTTP {r1.status_code}, atteso redirect. "
            f"Location={loc!r} {snippet}"
        )
    qs = _parse_redirect_query_dict(loc)
    access = _extract_first(qs.get("access"))
    country = _extract_first(qs.get("country_code")) or country_code_hint
    if not access:
        hint = _huami_redirect_error_message(qs)
        raise MiFitnessAuthError(
            hint
            or "Zepp: token di accesso assente nella URL di redirect (dopo POST tokens)."
        )

    login_form = {
        "code": access,
        "device_id": str(uuid.uuid4()),
        "device_model": "android_phone",
        "app_version": "9.12.5",
        "dn": (
            "api-mifit.zepp.com,api-user.zepp.com,api-mifit.zepp.com,api-watch.zepp.com,"
            "app-analytics.zepp.com,auth.zepp.com,api-analytics.zepp.com"
        ),
        "third_name": "huami",
        "source": "com.huami.watch.hmwatchmanager:9.12.5:151689",
        "app_name": "com.huami.midong",
        "country_code": country,
        "grant_type": "access_token",
        "allow_registration": "false",
        "lang": "en",
        "countryState": _country_state_for_code(country),
    }
    login_headers = {
        "app_name": "com.huami.webapp",
        "appname": "com.huami.webapp",
        "origin": "https://user.zepp.com",
        "referer": "https://user.zepp.com/",
        "user-agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"
        ),
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.5",
    }
    r2 = client.post(
        login_url,
        data=login_form,
        headers=login_headers,
        follow_redirects=True,
    )
    if r2.status_code != 200:
        raise MiFitnessAuthError(
            f"Zepp login: HTTP {r2.status_code} {r2.text[:500]}"
        )
    try:
        data = r2.json()
    except Exception as e:
        raise MiFitnessAuthError(f"Zepp login: JSON non valido: {e}") from e
    if not isinstance(data, dict):
        raise MiFitnessAuthError("Zepp login: risposta non oggetto JSON.")
    return _validate_login_json(data)


def _login_zepp_encrypted(
    client: httpx.Client,
    email: str,
    password: str,
    *,
    prefer_region: str,
) -> dict[str, Any]:
    """Prova cluster Zepp in base a prefer_region; fallback sull'altro cluster."""
    prefer = "eu" if prefer_region == "eu" else "us"
    order: list[tuple[str, str, str, str, str]] = []
    if prefer == "eu":
        order = [
            (ZEPP_EU_TOKENS_URL, ZEPP_EU_LOGIN_URL, "eu-central-1", "DE"),
            (ZEPP_US2_TOKENS_URL, ZEPP_US2_LOGIN_URL, "us-west-2", "US"),
        ]
    else:
        order = [
            (ZEPP_US2_TOKENS_URL, ZEPP_US2_LOGIN_URL, "us-west-2", "US"),
            (ZEPP_EU_TOKENS_URL, ZEPP_EU_LOGIN_URL, "eu-central-1", "DE"),
        ]
    errs: list[str] = []
    for tokens_url, login_url, zreg, c_hint in order:
        host = urllib.parse.urlparse(tokens_url).hostname or ""
        try:
            return _login_zepp_encrypted_one(
                client,
                email,
                password,
                tokens_url=tokens_url,
                login_url=login_url,
                zepp_region=zreg,
                country_code_hint=c_hint,
            )
        except MiFitnessAuthError as e:
            errs.append(f"[{host}] {str(e)[:200]}")
        except httpx.RequestError as e:
            errs.append(f"[{host}] rete: {e!s}")
    joined = " | ".join(errs)
    transport_only = bool(errs) and all(" rete:" in msg for msg in errs)
    detail = "Zepp login: falliti tutti i cluster. " + joined
    if transport_only:
        raise MiFitnessTransportError(detail[:800]) from None
    raise MiFitnessAuthError(detail[:800])


def _legacy_login_attempt(
    client: httpx.Client, email: str, password: str
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Flusso legacy api-user.huami.com/registrations/{email}/tokens.
    Ritorna (json login, None) se OK; (None, diagnostica) se va provato Zepp.
    """
    auth_url = (
        "https://api-user.huami.com/registrations/"
        + urllib.parse.quote(email, safe="")
        + "/tokens"
    )
    form1 = {
        "state": "REDIRECTION",
        "client_id": "HuaMi",
        "redirect_uri": REG_SUCCESS_REDIRECT,
        "token": "access",
        "password": password,
    }
    r1 = client.post(auth_url, data=form1, follow_redirects=False)
    loc = r1.headers.get("location") or r1.headers.get("Location")
    if r1.status_code not in _REDIRECT_STATUS or not loc:
        try:
            body = r1.text[:800]
        except Exception:
            body = ""
        raise MiFitnessAuthError(
            f"Login Huami (legacy) step 1: HTTP {r1.status_code}, atteso redirect. "
            f"Location={loc!r} {body}"
        )

    qs = _parse_redirect_query_dict(loc)
    code = _extract_first(qs.get("access"))
    country = _extract_first(qs.get("country_code")) or ""
    if code:
        data = _post_huami_account_login(client, code, country)
        if isinstance(data, dict) and data.get("token_info"):
            return (data, None)
        msg = (
            (data.get("message") or data.get("error") or str(data)[:400])
            if isinstance(data, dict)
            else str(data)
        )
        raise MiFitnessAuthError(f"Login legacy step 2 fallito: {msg}")

    err_txt = _huami_redirect_error_message(qs)
    diag = err_txt or (
        "Token di accesso assente nella URL di redirect (flusso legacy); "
        "tento il login Zepp cifrato."
    )
    return (None, diag)


def login_with_email_password(
    email: str,
    password: str,
    *,
    region: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """
    Esegue login Huami e restituisce il JSON di risposta login (token_info.*).

    Prova prima il flusso legacy in chiaro; se manca `access` nel redirect
    (tipico con account Zepp/beta), usa POST cifrato verso cluster Zepp EU (de2) o US2.
    `region` vedi ``normalize_mifitness_region`` (default server: EU).
    """
    em = (email or "").strip()
    if not em or not password:
        raise MiFitnessAuthError("Email o password vuoti.")
    rn = normalize_mifitness_region(region)

    with httpx.Client(timeout=timeout) as client:
        legacy_json, legacy_diag = _legacy_login_attempt(client, em, password)
        if legacy_json is not None:
            return legacy_json

        try:
            return _login_zepp_encrypted(client, em, password, prefer_region=rn)
        except MiFitnessAuthError as e:
            hint = legacy_diag or ""
            if hint and str(e) not in hint:
                raise MiFitnessAuthError(
                    f"{hint} — Fallback Zepp: {e}"
                ) from e
            raise


def session_from_login_result(
    login_json: dict[str, Any],
    *,
    region_norm: str,
) -> dict[str, Any]:
    """Estrae campi persistenti minimi dalla risposta login."""
    ti = login_json.get("token_info") or {}
    app_token = ti.get("app_token") or ti.get("login_token")
    user_id = ti.get("user_id")
    if not app_token:
        raise MiFitnessAuthError("app_token assente dopo login.")
    if user_id is None:
        raise MiFitnessAuthError("user_id assente dopo login.")
    rn = region_norm if region_norm in ("eu", "us") else normalize_mifitness_region(None)
    return {
        "app_token": str(app_token),
        "user_id": str(user_id),
        "app_name": MI_FIT_APP_NAME,
        "api_base": default_api_base_for_region(rn),
        "mi_fitness_region": rn,
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


@dataclass(frozen=True)
class FetchWorkoutSummariesResult:
    rows: list[dict[str, Any]]
    api_base_used: str


def _history_api_base_candidates(api_base: str) -> list[str]:
    """Ordine: host salvato in sessione, poi l'altro cluster Huami."""
    primary = api_base.rstrip("/")
    out: list[str] = [primary]
    for b in (DEFAULT_API_BASE_EU, DEFAULT_API_BASE_GLOBAL):
        br = b.rstrip("/")
        if br not in out:
            out.append(br)
    return out


def fetch_workout_summaries_paginated(
    app_token: str,
    *,
    api_base: str | None = None,
    timeout: float = 60.0,
    since_ts: int | None = None,
    max_summaries: int = 2500,
) -> FetchWorkoutSummariesResult:
    """
    GET /v1/sport/run/history.json ripetuti finché ``next == -1`` o limite sicurezza.
    Se ``since_ts`` è impostato, interrompe la paginazione quando un intero batch
    ha solo trackid < since_ts (storia tipicamente dal più recente).

    Prova in sequenza l'host in ``api_base`` (salvato su Firestore) e il cluster
    Huami alternativo (EU ``api-mifit-de`` vs globale ``api-mifit``) se 401/403.
    """
    primary = (api_base or DEFAULT_API_BASE_GLOBAL).rstrip("/")
    candidates = _history_api_base_candidates(primary)

    last_err: MiFitnessApiError | None = None
    for base in candidates:
        try:
            rows = _fetch_workout_summaries_impl(
                app_token,
                base,
                timeout=timeout,
                since_ts=since_ts,
                max_summaries=max_summaries,
            )
            return FetchWorkoutSummariesResult(rows=rows, api_base_used=base)
        except MiFitnessApiError as e:
            last_err = e
            es = str(e)
            if "HTTP 401" in es or "HTTP 403" in es:
                continue
            raise
    if last_err is not None:
        raise last_err
    return FetchWorkoutSummariesResult(rows=[], api_base_used=primary)


def _fetch_workout_summaries_impl(
    app_token: str,
    base: str,
    *,
    timeout: float,
    since_ts: int | None,
    max_summaries: int,
) -> list[dict[str, Any]]:
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
