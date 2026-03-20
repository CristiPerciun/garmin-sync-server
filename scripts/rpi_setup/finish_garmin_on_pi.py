#!/usr/bin/env python3
"""
Completa sul Pi: git pull, pip (complete_pip.sh), unit systemd, verifica.
Variabili: RPI_SSH_PASSWORD (obbl.), RPI_HOST (default 172.20.10.4), RPI_HOST6 (fallback se v4 fallisce),
           RPI_USER (default cperciun).

IPv6 es.: $env:RPI_HOST6 = "2a02:b025:14:79c8:c244:4ff3:3191:33d2"
"""
from __future__ import annotations

import os
import socket
import sys
import time

import paramiko

USER = os.environ.get("RPI_USER", "cperciun")
PW = os.environ.get("RPI_SSH_PASSWORD", "")
HOST4 = os.environ.get("RPI_HOST", "172.20.10.4").strip().strip("[]")
HOST6 = os.environ.get("RPI_HOST6", "").strip().strip("[]")
REPO = f"/home/{USER}/garmin-sync-server"
CONNECT_TIMEOUT = float(os.environ.get("RPI_SSH_TIMEOUT", "90"))
CONNECT_RETRIES = int(os.environ.get("RPI_SSH_RETRIES", "8"))
RETRY_DELAY = float(os.environ.get("RPI_SSH_RETRY_DELAY", "4"))


def _normalize_host(h: str) -> str:
    h = h.strip()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    return h


def _connect_ssh(host: str) -> paramiko.SSHClient:
    host = _normalize_host(host)
    last: BaseException | None = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            sock = socket.create_connection((host, 22), timeout=CONNECT_TIMEOUT)
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
                # Hotspot/NAT: evita reset durante pip lunghi
                t.set_keepalive(int(os.environ.get("RPI_SSH_KEEPALIVE_SEC", "25")))
            print(f"SSH ok via {host} (tentativo {attempt})", file=sys.stderr)
            return c
        except (OSError, paramiko.SSHException) as e:
            last = e
            print(f"SSH {host} tentativo {attempt}/{CONNECT_RETRIES}: {e}", file=sys.stderr)
            time.sleep(RETRY_DELAY)
    assert last is not None
    raise last


def main() -> int:
    if not PW:
        print("Imposta RPI_SSH_PASSWORD", file=sys.stderr)
        return 1

    hosts_to_try = [HOST4]
    if HOST6 and HOST6 not in hosts_to_try:
        hosts_to_try.append(HOST6)

    c: paramiko.SSHClient | None = None
    last_err: BaseException | None = None
    for h in hosts_to_try:
        try:
            c = _connect_ssh(h)
            break
        except BaseException as e:
            last_err = e
    if c is None:
        print(f"SSH fallito per tutti gli host: {hosts_to_try}. Ultimo errore: {last_err}", file=sys.stderr)
        return 1

    try:

        def run(cmd: str, timeout: int = 300) -> tuple[int, str]:
            _, out, err = c.exec_command(cmd, timeout=timeout)
            text = out.read().decode("utf-8", errors="replace") + err.read().decode(
                "utf-8", errors="replace"
            )
            return out.channel.recv_exit_status(), text

        if os.environ.get("RPI_SKIP_PIP_INSECURE", "") != "1":
            stdin, stdout, _ = c.exec_command(
                "sudo -S bash -lc " + repr("echo GARMIN_SYNC_PIP_INSECURE=1 > /etc/default/garmin-sync-env"),
                timeout=60,
            )
            stdin.write(PW + "\n")
            stdin.flush()
            stdin.channel.shutdown_write()
            stdout.channel.recv_exit_status()

        clone = (
            f"test -d {REPO}/.git || git clone https://github.com/CristiPerciun/garmin-sync-server.git {REPO}"
        )
        code0, o0 = run(clone, timeout=300)
        print(o0)
        if code0 != 0:
            return code0

        code, o = run(
            f"cd {REPO} && git fetch origin && git reset --hard origin/main",
            timeout=180,
        )
        print(o)
        if code != 0:
            print("git exit", code, file=sys.stderr)
            return code

        print("--- complete_pip (max ~45 min) ---")
        pip_cmd = (
            f"cd {REPO} && bash deploy/rpi/complete_pip.sh > /tmp/garmin_pip.log 2>&1; "
            "ec=$?; tail -100 /tmp/garmin_pip.log; exit $ec"
        )
        code, o = run(pip_cmd, timeout=2700)
        print(o)
        if code != 0:
            print("pip exit", code, file=sys.stderr)

        setup = f"""
set -e
cp {REPO}/deploy/rpi/garmin-sync.service /etc/systemd/system/
cp {REPO}/deploy/rpi/garmin-sync-pull.service /etc/systemd/system/
cp {REPO}/deploy/rpi/garmin-sync-pull.timer /etc/systemd/system/
install -m 0755 {REPO}/deploy/rpi/garmin-sync-pull.sh /usr/local/sbin/garmin-sync-pull.sh
systemctl daemon-reload
systemctl enable garmin-sync.service garmin-sync-pull.timer
systemctl restart garmin-sync.service
systemctl start garmin-sync-pull.timer || true
"""
        print("--- systemd ---")
        stdin, stdout, stderr = c.exec_command(
            "sudo -S bash -lc " + repr(setup.strip()),
            timeout=120,
        )
        stdin.write(PW + "\n")
        stdin.flush()
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        print(out + err)
        if rc != 0:
            print("systemd setup exit", rc, file=sys.stderr)

        time.sleep(4)
        _, o4, _ = c.exec_command(
            "systemctl is-active garmin-sync.service; "
            "curl -sS -m 5 -o /dev/null -w 'http_docs:%{http_code}\\n' http://127.0.0.1:8080/docs 2>&1; "
            "systemctl is-active garmin-sync-pull.timer 2>/dev/null || true; "
            "journalctl -u garmin-sync -n 18 --no-pager 2>/dev/null",
            timeout=30,
        )
        print("--- verifica ---")
        print(o4.read().decode("utf-8", errors="replace"))

        return 0 if rc == 0 and code == 0 else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
