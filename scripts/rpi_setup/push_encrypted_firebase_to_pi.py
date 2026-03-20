#!/usr/bin/env python3
"""
Carica su Pi i file cifrati Firebase e attiva l'avvio tramite garmin-sync-encrypted-start.sh.

Prima sul PC:
  .\\encrypt_garmin_firebase_secret.ps1 -JsonPath C:\\path\\chiave.json

Poi (stessa rete del Pi):
  $env:RPI_SSH_PASSWORD = "..."
  $env:RPI_GARMIN_DECRYPT_PASS = "stessa-passphrase-del-pc"
  python push_encrypted_firebase_to_pi.py --enc .\\garmin-firebase.enc

Crea su Pi:
  ~/.secrets/ (700)
  ~/.secrets/garmin-firebase.enc + garmin-firebase.pass (600)
  /usr/local/sbin/garmin-sync-encrypted-start.sh (0755, fuori dal git pull)
  — così il server resta autonomo senza il PC acceso.

Rimuovi dal .env sul Pi le righe FIREBASE_CREDENTIALS_B64 / FIREBASE_CREDENTIALS se presenti,
per evitare conflitti (opzionale: questo script le commenta).

Variabili: RPI_HOST, RPI_USER, RPI_HOST6, RPI_SSH_PASSWORD, RPI_GARMIN_DECRYPT_PASS.
Passphrase alternativa: --pass-file percorso_locale (una riga, UTF-8).
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

import paramiko

USER = os.environ.get("RPI_USER", "cperciun")
PW = os.environ.get("RPI_SSH_PASSWORD", "")
HOST4 = os.environ.get("RPI_HOST", "172.20.10.4").strip().strip("[]")
HOST6 = os.environ.get("RPI_HOST6", "").strip().strip("[]")
REPO = f"/home/{USER}/garmin-sync-server"
SCRIPT_DIR = Path(__file__).resolve().parent
LOCAL_WRAPPER = SCRIPT_DIR / "garmin-sync-encrypted-start.sh"
LOCAL_SERVICE = SCRIPT_DIR / "garmin-sync.service.encrypted"


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--enc", required=True, help="File .enc prodotto da encrypt_garmin_firebase_secret.ps1")
    ap.add_argument("--pass-file", help="File locale con passphrase (una riga)")
    args = ap.parse_args()

    enc_path = Path(args.enc).expanduser().resolve()
    if not enc_path.is_file():
        print("File .enc non trovato:", enc_path, file=sys.stderr)
        return 1

    if args.pass_file:
        passphrase = Path(args.pass_file).read_text(encoding="utf-8").splitlines()[0].strip("\r\n")
    else:
        passphrase = os.environ.get("RPI_GARMIN_DECRYPT_PASS", "").strip("\r\n")
    if not passphrase:
        print("Imposta RPI_GARMIN_DECRYPT_PASS o --pass-file", file=sys.stderr)
        return 1

    if not PW:
        print("Imposta RPI_SSH_PASSWORD", file=sys.stderr)
        return 1

    if not LOCAL_WRAPPER.is_file():
        print("Manca", LOCAL_WRAPPER, file=sys.stderr)
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

    secrets = f"/home/{USER}/.secrets"
    remote_enc = f"{secrets}/garmin-firebase.enc"
    remote_pass = f"{secrets}/garmin-firebase.pass"
    remote_wr_tmp = f"/home/{USER}/.secrets/garmin-sync-encrypted-start.sh.tmp"

    try:
        _, o_mk, _ = c.exec_command(f"mkdir -p {secrets}", timeout=30)
        o_mk.channel.recv_exit_status()

        sftp = c.open_sftp()
        sftp.put(str(enc_path), remote_enc)
        with sftp.file(remote_pass, "w") as pf:
            pf.write(passphrase.encode("utf-8"))
        with open(LOCAL_WRAPPER, "rb") as wf:
            with sftp.open(remote_wr_tmp, "wb") as rf:
                rf.write(wf.read())
        if not LOCAL_SERVICE.is_file():
            print("Manca", LOCAL_SERVICE, file=sys.stderr)
            return 1
        svc_local = LOCAL_SERVICE.read_bytes()
        with sftp.open("/tmp/garmin-sync.service.new", "wb") as f:
            f.write(svc_local)
        sftp.close()

        _, ch_out, _ = c.exec_command(
            f"chmod 700 {secrets} && chmod 600 {remote_enc} {remote_pass} && chmod 0644 {remote_wr_tmp}",
            timeout=30,
        )
        ch_out.channel.recv_exit_status()

        stdin, stdout, stderr = c.exec_command(
            "sudo -S bash -lc "
            + repr(
                "install -m 0755 "
                + remote_wr_tmp
                + " /usr/local/sbin/garmin-sync-encrypted-start.sh && "
                "cp /tmp/garmin-sync.service.new /etc/systemd/system/garmin-sync.service && "
                "systemctl daemon-reload && systemctl restart garmin-sync"
            ),
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
            print("Installazione unit systemd fallita", file=sys.stderr)
            return rc

        # commenta FIREBASE_* nel .env se esiste
        strip_fb = (
            f"test -f {REPO}/.env && sed -i.bak "
            f"-e 's/^FIREBASE_CREDENTIALS_B64=/#FIREBASE_CREDENTIALS_B64=/' "
            f"-e 's/^FIREBASE_CREDENTIALS=/#FIREBASE_CREDENTIALS=/' {REPO}/.env || true"
        )
        _, st_out, _ = c.exec_command(strip_fb, timeout=20)
        st_out.channel.recv_exit_status()

        _, o2, _ = c.exec_command(
            "sleep 4; systemctl is-active garmin-sync; curl -sS -m 8 http://127.0.0.1:8080/",
            timeout=30,
        )
        print(o2.read().decode("utf-8", errors="replace"))
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
