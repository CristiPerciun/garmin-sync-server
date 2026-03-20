#!/usr/bin/env python3
"""
Scrive FIREBASE_CREDENTIALS_B64 nel .env sul Pi e riavvia garmin-sync.

Sul PC Windows (stessa rete del Pi):
  $env:RPI_SSH_PASSWORD = "..."
  $env:FIREBASE_CREDENTIALS_B64 = "<stringa base64 del service account JSON>"
  python push_firebase_env_to_pi.py

Oppure passa il path del JSON (viene codificato in base64 in locale):
  python push_firebase_env_to_pi.py --json C:\\path\\firebase-adminsdk-xxx.json

Variabili: RPI_HOST (default 172.20.10.4), RPI_USER, RPI_HOST6, RPI_SSH_PASSWORD.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import socket
import sys
import time

import paramiko

USER = os.environ.get("RPI_USER", "cperciun")
PW = os.environ.get("RPI_SSH_PASSWORD", "")
HOST4 = os.environ.get("RPI_HOST", "172.20.10.4").strip().strip("[]")
HOST6 = os.environ.get("RPI_HOST6", "").strip().strip("[]")
REPO = f"/home/{USER}/garmin-sync-server"
ENV_PATH = f"{REPO}/.env"


def _connect(host: str) -> paramiko.SSHClient:
    sock = socket.create_connection((host, 22), timeout=90)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        host,
        username=USER,
        password=PW,
        sock=sock,
        allow_agent=False,
        look_for_keys=False,
    )
    t = c.get_transport()
    if t:
        t.set_keepalive(int(os.environ.get("RPI_SSH_KEEPALIVE_SEC", "25")))
    return c


def _merge_env_file(content: str, key: str, value: str) -> str:
    lines = content.splitlines()
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    out: list[str] = []
    for line in lines:
        if key_re.match(line):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    return "\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", help="Path al service account JSON (alternativa a FIREBASE_CREDENTIALS_B64)")
    args = p.parse_args()

    if args.json:
        path = os.path.expanduser(args.json)
        if not os.path.isfile(path):
            print("File JSON non trovato:", path, file=sys.stderr)
            return 1
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
    else:
        b64 = os.environ.get("FIREBASE_CREDENTIALS_B64", "").strip()
        if not b64:
            print(
                "Imposta FIREBASE_CREDENTIALS_B64 oppure usa --json path\\al.json",
                file=sys.stderr,
            )
            return 1

    if not PW:
        print("Imposta RPI_SSH_PASSWORD", file=sys.stderr)
        return 1

    hosts = [HOST4]
    if HOST6 and HOST6 not in hosts:
        hosts.append(HOST6)

    c: paramiko.SSHClient | None = None
    for h in hosts:
        try:
            c = _connect(h)
            print(f"SSH ok: {h}", file=sys.stderr)
            break
        except OSError as e:
            print(f"SSH {h}: {e}", file=sys.stderr)
    if c is None:
        return 1

    try:
        read_cmd = f"test -f {ENV_PATH} && cat {ENV_PATH} || true"
        _, stdout, _ = c.exec_command(read_cmd, timeout=30)
        current = stdout.read().decode("utf-8", errors="replace")

        new_body = _merge_env_file(current, "FIREBASE_CREDENTIALS_B64", b64)

        _, o_mk, _ = c.exec_command(f"mkdir -p {REPO}", timeout=30)
        o_mk.channel.recv_exit_status()
        sftp = c.open_sftp()
        sftp.chdir(REPO)
        with sftp.file(".env", "w") as f:
            f.write(new_body.encode("utf-8"))
        try:
            sftp.chmod(".env", 0o600)
        except OSError:
            pass
        sftp.close()

        stdin, stdout, stderr = c.exec_command(
            f"sudo -S bash -lc 'systemctl restart garmin-sync'",
            timeout=60,
        )
        stdin.write(PW + "\n")
        stdin.flush()
        stdin.channel.shutdown_write()
        _ = stdout.read() + stderr.read()
        rc = stdout.channel.recv_exit_status()
        if rc != 0:
            print("systemctl restart fallito (sudo?)", file=sys.stderr)
            return rc

        time.sleep(3)
        _, o2, _ = c.exec_command(
            "systemctl is-active garmin-sync; curl -sS http://127.0.0.1:8080/",
            timeout=30,
        )
        print(o2.read().decode("utf-8", errors="replace"))
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
