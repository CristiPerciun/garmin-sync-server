#!/usr/bin/env python3
"""
Verifica su Raspberry/Ubuntu (locale) che garmin-sync sia installato correttamente.
Esegui SULLA MACCHINA Linux, non da Windows:

  cd ~/garmin-sync-server
  python3 deploy/rpi/verify_pi_setup.py

Opzioni:
  --repo PATH   directory del clone (default: ~/garmin-sync-server)
  --user NAME   utente systemd (default: da unit o cperciun)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def sh(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    p = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def ok(name: str, detail: str = "") -> None:
    print(f"  [OK] {name}" + (f" — {detail}" if detail else ""))


def fail(name: str, detail: str = "") -> None:
    print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica install garmin-sync sul Pi")
    default_repo = Path.home() / "garmin-sync-server"
    parser.add_argument("--repo", type=Path, default=default_repo, help="Path clone git")
    parser.add_argument("--port", type=int, default=8080, help="Porta API")
    args = parser.parse_args()
    repo: Path = args.repo.expanduser().resolve()

    errors = 0
    print("=== Garmin sync server — verifica ambiente (eseguire su Linux) ===\n")

    # 1) Unit systemd
    print("1) Unit systemd")
    for unit in (
        "garmin-sync.service",
        "garmin-sync-pull.service",
        "garmin-sync-pull.timer",
    ):
        code, out, err = sh(["systemctl", "list-unit-files", unit, "--no-legend"])
        if code == 0 and unit in out:
            ok(unit, "registrata")
        else:
            fail(unit, "manca: esegui sudo bash deploy/rpi/install.sh dal repo")
            errors += 1

    # 2) Timer attivo
    print("\n2) Timer pull GitHub")
    code, out, _ = sh(["systemctl", "is-active", "garmin-sync-pull.timer"])
    if out == "active":
        ok("garmin-sync-pull.timer", "active")
    else:
        fail("garmin-sync-pull.timer", f"stato={out!r} — sudo systemctl enable --now garmin-sync-pull.timer")
        errors += 1

    code, out, _ = sh(["systemctl", "list-timers", "--all", "--no-pager"])
    if "garmin-sync-pull" in out:
        ok("Prossima esecuzione timer", "presente in list-timers")
    else:
        fail("list-timers", "garmin-sync-pull non in elenco")
        errors += 1

    # 3) Script pull
    print("\n3) Script pull")
    pull_sh = Path("/usr/local/sbin/garmin-sync-pull.sh")
    if pull_sh.is_file() and os.access(pull_sh, os.X_OK):
        ok(str(pull_sh), "eseguibile")
    else:
        fail(str(pull_sh), "manca o non eseguibile")
        errors += 1

    # 4) Repo git
    print("\n4) Repository")
    if (repo / ".git").is_dir():
        ok(str(repo), "clone presente")
        code, out, _ = sh(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"])
        if code == 0:
            ok("branch corrente", out)
        code, out, _ = sh(["git", "-C", str(repo), "remote", "-v"])
        if code == 0 and "origin" in out:
            ok("remote origin", out.split()[1] if len(out.split()) > 1 else out[:60])
    else:
        fail(str(repo), "directory .git assente")
        errors += 1

    # 5) venv + main
    print("\n5) Python / venv")
    venv_py = repo / "venv" / "bin" / "python"
    if venv_py.is_file():
        ok(str(venv_py), "venv presente")
    else:
        fail(str(venv_py), "eseguire install.sh o python3 -m venv venv && pip install -r requirements.txt")
        errors += 1

    if (repo / "main.py").is_file():
        ok("main.py", "presente")
    else:
        fail("main.py", "manca nel repo")
        errors += 1

    # 6) Servizio API
    print("\n6) Servizio garmin-sync (API)")
    code, out, _ = sh(["systemctl", "is-active", "garmin-sync.service"])
    if out == "active":
        ok("garmin-sync.service", "active")
    else:
        fail("garmin-sync.service", f"stato={out!r} — journalctl -u garmin-sync -n 50")
        errors += 1

    # 7) Health HTTP
    print("\n7) Health HTTP")
    url = f"http://127.0.0.1:{args.port}/"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            body = r.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        if data.get("status") == "ok":
            ok("GET /", json.dumps(data, ensure_ascii=False)[:120])
        else:
            fail("GET /", body[:200])
            errors += 1
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        fail("GET /", str(e))
        errors += 1

    # 8) Credenziali Firebase + Firestore (opzionale: richiede .env e venv con firebase-admin)
    print("\n8) Firebase .env + Firestore (opzionale)")
    env_file = repo / ".env"
    verify_fb = repo / "deploy" / "rpi" / "verify_firebase_credentials.py"
    if env_file.is_file() and verify_fb.is_file() and venv_py.is_file():
        code, out, err = sh(
            [str(venv_py), str(verify_fb), "--repo", str(repo)],
            timeout=60,
        )
        if code == 0:
            ok("verify_firebase_credentials.py", "vedi righe [OK] sopra nel blocco script")
            if out:
                for line in out.splitlines()[:12]:
                    print(f"     {line}")
        else:
            fail(
                "verify_firebase_credentials.py",
                (out or err or "exit != 0")[:200],
            )
            errors += 1
    elif not env_file.is_file():
        print("     (salta: manca .env)")
    else:
        print("     (salta: manca venv o verify_firebase_credentials.py)")

    print("\n=== Riepilogo ===")
    if errors == 0:
        print("Tutte le verifiche base sono OK.")
        return 0
    print(f"Errori: {errors} — correggi i [FAIL] sopra.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
