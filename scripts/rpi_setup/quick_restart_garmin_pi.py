"""git pull + restart garmin-sync. RPI_SSH_PASSWORD, RPI_HOST=172.20.10.4"""
import os
import socket
import sys

import paramiko

HOST = os.environ.get("RPI_HOST", "172.20.10.4").strip()
USER = os.environ.get("RPI_USER", "cperciun")
PW = os.environ.get("RPI_SSH_PASSWORD", "")
REPO = f"/home/{USER}/garmin-sync-server"

if not PW:
    sys.exit("Imposta RPI_SSH_PASSWORD")

sock = socket.create_connection((HOST, 22), timeout=90)
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PW, sock=sock, allow_agent=False, look_for_keys=False)
t = c.get_transport()
if t:
    t.set_keepalive(25)
try:
    _, o, e = c.exec_command(
        f"cd {REPO} && git fetch origin && git reset --hard origin/main && echo GIT_OK",
        timeout=120,
    )
    print(o.read().decode(errors="replace"))
    er = e.read().decode(errors="replace")
    if er.strip():
        print(er, file=sys.stderr)

    # Una sola riga per bash -lc (evita problemi con repr e newline)
    one = (
        f"cp {REPO}/deploy/rpi/garmin-sync.service /etc/systemd/system/ && "
        "systemctl daemon-reload && systemctl enable garmin-sync.service && "
        "systemctl restart garmin-sync.service && sleep 3 && systemctl is-active garmin-sync.service && "
        "curl -sS -m 5 http://127.0.0.1:8080/"
    )
    stdin, stdout, stderr = c.exec_command("sudo -S bash -lc " + repr(one), timeout=120)
    stdin.write(PW + "\n")
    stdin.flush()
    stdin.channel.shutdown_write()
    print(stdout.read().decode(errors="replace"))
    er2 = stderr.read().decode(errors="replace")
    if er2.strip():
        print(er2, file=sys.stderr)
finally:
    c.close()
