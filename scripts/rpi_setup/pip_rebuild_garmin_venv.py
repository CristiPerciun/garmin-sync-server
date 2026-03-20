"""Ricrea venv sul Pi e pip install -r requirements.txt. RPI_SSH_PASSWORD, RPI_HOST."""
import os
import socket
import sys

import paramiko

H = os.environ.get("RPI_HOST", "172.20.10.4").strip()
U = os.environ.get("RPI_USER", "cperciun")
P = os.environ.get("RPI_SSH_PASSWORD", "")
REPO = f"/home/{U}/garmin-sync-server"
TH = (
    "--trusted-host pypi.org --trusted-host files.pythonhosted.org "
    "--trusted-host www.piwheels.org"
)

if not P:
    sys.exit("RPI_SSH_PASSWORD")

s = socket.create_connection((H, 22), 90)
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(H, username=U, password=P, sock=s, allow_agent=False, look_for_keys=False)
c.get_transport().set_keepalive(20)
try:
    parts = [
        f"cd {REPO}",
        "git fetch origin",
        "git reset --hard origin/main",
        "rm -rf venv",
        "python3 -m venv venv",
        f"./venv/bin/pip install {TH} --upgrade pip",
        f"./venv/bin/pip install {TH} --no-cache-dir -r requirements.txt",
        "./venv/bin/python -c 'import uvicorn; print(uvicorn.__version__)'",
    ]
    cmd = " && ".join(parts)
    _, stdout, stderr = c.exec_command(cmd, timeout=3600)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    print(out)
    if err.strip():
        print(err, file=sys.stderr)
    sys.exit(stdout.channel.recv_exit_status())
finally:
    c.close()
