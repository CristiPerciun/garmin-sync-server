#!/usr/bin/env python3
"""
Clona/aggiorna garmin-sync-server da GitHub sul Pi ed esegue deploy/rpi/install.sh.
Richiede: pip install paramiko, variabile RPI_SSH_PASSWORD.
Opzionali: RPI_HOST (default 172.20.10.4), RPI_USER (default cperciun).
"""
from __future__ import annotations

import os
import sys

import paramiko


def main() -> int:
    pw = os.environ.get("RPI_SSH_PASSWORD")
    if not pw:
        print("Imposta RPI_SSH_PASSWORD", file=sys.stderr)
        return 1
    host = os.environ.get("RPI_HOST", "172.20.10.4")
    user = os.environ.get("RPI_USER", "cperciun")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host,
        username=user,
        password=pw,
        timeout=int(os.environ.get("RPI_SSH_TIMEOUT", "90")),
        allow_agent=False,
        look_for_keys=False,
    )
    tr = client.get_transport()
    if tr:
        tr.set_keepalive(int(os.environ.get("RPI_SSH_KEEPALIVE_SEC", "25")))
    try:
        # Reti con proxy SSL: abilita pip --trusted-host (vedi RPI_DEPLOY.md)
        if os.environ.get("RPI_SKIP_PIP_INSECURE", "") != "1":
            stdin, stdout, _ = client.exec_command(
                "sudo -S bash -lc "
                + repr("echo GARMIN_SYNC_PIP_INSECURE=1 > /etc/default/garmin-sync-env"),
                timeout=60,
            )
            stdin.write(pw + "\n")
            stdin.flush()
            stdin.channel.shutdown_write()
            stdout.channel.recv_exit_status()

        sync = (
            "cd ~ && "
            "(test -d garmin-sync-server/.git || git clone https://github.com/CristiPerciun/garmin-sync-server.git) "
            "&& cd garmin-sync-server && git fetch origin && "
            "(git checkout -B main origin/main 2>/dev/null || git checkout -B master origin/master)"
        )
        _, out, err = client.exec_command(sync, timeout=600)
        o = out.read().decode(errors="replace")
        e = err.read().decode(errors="replace")
        code = out.channel.recv_exit_status()
        print(o)
        if e.strip():
            print(e, file=sys.stderr)
        if code != 0:
            return code

        install = f"sudo -S bash /home/{user}/garmin-sync-server/deploy/rpi/install.sh"
        stdin, stdout, stderr = client.exec_command(install, timeout=1800)
        stdin.write(pw + "\n")
        stdin.flush()
        stdin.channel.shutdown_write()
        o2 = stdout.read().decode(errors="replace")
        e2 = stderr.read().decode(errors="replace")
        code2 = stdout.channel.recv_exit_status()
        print(o2)
        if e2.strip():
            print(e2, file=sys.stderr)

        _, o3, _ = client.exec_command("systemctl is-active garmin-sync.service 2>/dev/null; df -h / | tail -1")
        print(o3.read().decode(errors="replace"))

        return code2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
