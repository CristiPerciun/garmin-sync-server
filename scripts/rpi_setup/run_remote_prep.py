#!/usr/bin/env python3
"""
Copia gli script rpi_setup sul Pi via SSH/SFTP ed esegue verifica sistema + preparazione ambiente.
Uso (PowerShell):
  $env:RPI_SSH_PASSWORD = 'la_tua_password'
  python run_remote_prep.py

Opzionale: RPI_HOST (default 172.20.10.4), RPI_USER (default cperciun).
Non memorizzare la password nel repository.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import paramiko

RPI_HOST = os.environ.get("RPI_HOST", "172.20.10.4")
RPI_USER = os.environ.get("RPI_USER", "cperciun")


def main() -> int:
    password = os.environ.get("RPI_SSH_PASSWORD")
    if not password:
        print("Imposta la variabile d'ambiente RPI_SSH_PASSWORD.", file=sys.stderr)
        return 1

    here = Path(__file__).resolve().parent
    upload_names = (
        "01_check_system.sh",
        "02_prepare_environment.sh",
        "03_clone_project.sh",
        "README.md",
    )
    for name in upload_names:
        if not (here / name).is_file():
            print(f"Manca il file locale: {here / name}", file=sys.stderr)
            return 1

    remote_base = f"/home/{RPI_USER}/rpi_setup"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            RPI_HOST,
            username=RPI_USER,
            password=password,
            timeout=int(os.environ.get("RPI_SSH_TIMEOUT", "90")),
            allow_agent=False,
            look_for_keys=False,
        )
        tr = client.get_transport()
        if tr:
            tr.set_keepalive(int(os.environ.get("RPI_SSH_KEEPALIVE_SEC", "25")))
    except Exception as e:
        print(f"Connessione SSH fallita: {e}", file=sys.stderr)
        return 1

    try:
        client.exec_command(f"mkdir -p {remote_base}")
        time.sleep(0.3)

        sftp = client.open_sftp()
        try:
            for name in upload_names:
                sftp.put(str(here / name), f"{remote_base}/{name}")
        finally:
            sftp.close()

        client.exec_command(f"chmod +x {remote_base}/*.sh")

        def run_bash(script: str, timeout: int = 120) -> tuple[int, str, str]:
            cmd = f"cd {remote_base} && bash {script}"
            _, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=timeout)
            out = stdout.read().decode(errors="replace")
            err = stderr.read().decode(errors="replace")
            return stdout.channel.recv_exit_status(), out, err

        def run_sudo_bash(script: str, timeout: int = 1200) -> tuple[int, str, str]:
            # get_pty=False: evita che la password finisca nell'eco del terminale remoto
            cmd = f"cd {remote_base} && sudo -S bash {script}"
            stdin, stdout, stderr = client.exec_command(cmd, get_pty=False, timeout=timeout)
            stdin.write(password + "\n")
            stdin.flush()
            stdin.channel.shutdown_write()
            out = stdout.read().decode(errors="replace")
            err = stderr.read().decode(errors="replace")
            return stdout.channel.recv_exit_status(), out, err

        print("--- 01_check_system.sh ---")
        code, out, err = run_bash("01_check_system.sh")
        print(out)
        if err.strip():
            print(err, file=sys.stderr)
        if code != 0:
            print(f"01_check_system.sh exit {code}", file=sys.stderr)
            return code

        print("--- 02_prepare_environment.sh (sudo, può richiedere alcuni minuti) ---")
        code, out, err = run_sudo_bash("02_prepare_environment.sh")
        print(out)
        if err.strip():
            print(err, file=sys.stderr)
        if code != 0:
            print(f"02_prepare_environment.sh exit {code}", file=sys.stderr)
            return code

        print(
            "--- Fatto. Sul Pi: modifica URL_REPO in 03_clone_project.sh, poi esegui bash 03_clone_project.sh ---"
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
