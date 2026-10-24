#!/usr/bin/env bash
set -euo pipefail
test "${RENEWED_DOMAINS:?}" = oko.observer
# The hourly persistent timer retries if another maintenance operation owns the
# lock. Certbot will not renew an already renewed certificate just to retry us.
exec systemctl start --no-block oko-certificate-apply.service
