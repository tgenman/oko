#!/usr/bin/env bash
# Run as root from the exact reviewed release checkout on the NEW Oko VM.
set -euo pipefail
test "$(id -u)" = 0
test "$(uname -m)" = x86_64
. /etc/os-release
test "$ID" = ubuntu
test "$VERSION_ID" = 24.04
apt-get update
apt-get install -y ca-certificates curl git python3-venv python3-pip bubblewrap apparmor-profiles apparmor-utils certbot python3-certbot-dns-cloudflare
install -m 0644 /usr/share/apparmor/extra-profiles/bwrap-userns-restrict /etc/apparmor.d/bwrap-userns-restrict
apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
test "$(sysctl -n kernel.apparmor_restrict_unprivileged_userns)" = 1
if ! command -v docker >/dev/null; then
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod 0644 /etc/apt/keyrings/docker.asc
  printf 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable\n' > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce=5:29.1.3-1~ubuntu.24.04~noble docker-ce-cli=5:29.1.3-1~ubuntu.24.04~noble containerd.io docker-buildx-plugin docker-compose-plugin=2.40.3-1~ubuntu.24.04~noble
fi
systemctl enable --now docker
test "$(docker version --format '{{.Server.Version}}')" = 29.1.3
compose_version=$(docker compose version --short)
# Ubuntu's package includes a downstream build suffix after the pinned version.
test "${compose_version%%+*}" = 2.40.3
if ! getent group oko-ipc >/dev/null; then groupadd --gid 20001 oko-ipc; fi
test "$(getent group oko-ipc | cut -d: -f3)" = 20001
if ! id okocodex >/dev/null 2>&1; then
  useradd --system --uid 20002 --gid oko-ipc --home-dir /var/lib/oko-codex --shell /usr/sbin/nologin okocodex
fi
test "$(id -u okocodex)" = 20002
if ! command -v uv >/dev/null; then
  python3 -m venv /opt/oko-bootstrap
  /opt/oko-bootstrap/bin/pip install uv==0.11.28
  ln -s /opt/oko-bootstrap/bin/uv /usr/local/bin/uv
fi
install -d -m 0755 /srv/oko
operator_source=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ln -sfn "$operator_source" /srv/oko/operator
printf '#!/bin/sh\nexec /usr/bin/python3 /srv/oko/operator/okoctl.py "$@"\n' > /usr/local/sbin/okoctl
chmod 0755 /usr/local/sbin/okoctl
install -m 0644 "$operator_source/systemd/oko-certificate-apply.service" /etc/systemd/system/
install -m 0644 "$operator_source/systemd/oko-certificate-apply.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now oko-certificate-apply.timer
printf '%s\n' 'Host dependencies installed. Authentication, certificates and release install are separate checked steps.'
