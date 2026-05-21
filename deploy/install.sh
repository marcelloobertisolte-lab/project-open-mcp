#!/usr/bin/env bash
#
# Install the Project-Open MCP server as a systemd service on the ]po[ host.
#
# Prerequisites:
#   - Ubuntu/Debian with systemd, python3 >= 3.10, python3-venv
#   - The project copied to /opt/project-open-mcp (see README "deploy")
#
# Usage (from the project root, as root or via sudo):
#   sudo deploy/install.sh
#
# Idempotent: safe to re-run after pulling new code (re-installs deps + restart).

set -euo pipefail

INSTALL_DIR="/opt/project-open-mcp"
SERVICE_USER="projop"
ENV_DIR="/etc/project-open-mcp"
ENV_FILE="${ENV_DIR}/project-open-mcp.env"
UNIT_DST="/etc/systemd/system/project-open-mcp.service"

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

# Resolve the directory this script lives in -> project root is its parent.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo ">> Project root: ${PROJECT_ROOT}"

# 1) Service user (no login, no home shell needed).
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  echo ">> Creating service user ${SERVICE_USER}"
  useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

# 2) Place code at INSTALL_DIR (rsync from the project root if different).
if [[ "${PROJECT_ROOT}" != "${INSTALL_DIR}" ]]; then
  echo ">> Syncing code to ${INSTALL_DIR}"
  mkdir -p "${INSTALL_DIR}"
  rsync -a --delete \
    --exclude '.venv' --exclude '.git' --exclude '.env' \
    --exclude 'logs' --exclude '__pycache__' \
    "${PROJECT_ROOT}/" "${INSTALL_DIR}/"
fi

# 3) Virtualenv + install.
if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  echo ">> Creating venv"
  python3 -m venv "${INSTALL_DIR}/.venv"
fi
echo ">> Installing package"
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip >/dev/null
"${INSTALL_DIR}/.venv/bin/pip" install -e "${INSTALL_DIR}"

# 4) EnvironmentFile (do not overwrite an existing one).
mkdir -p "${ENV_DIR}"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo ">> Installing ${ENV_FILE}"
  cp "${INSTALL_DIR}/deploy/project-open-mcp.env" "${ENV_FILE}"
  chmod 640 "${ENV_FILE}"
else
  echo ">> Keeping existing ${ENV_FILE}"
fi

# 5) Ownership.
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"

# 6) systemd unit.
echo ">> Installing systemd unit"
cp "${INSTALL_DIR}/deploy/project-open-mcp.service" "${UNIT_DST}"
systemctl daemon-reload
systemctl enable --now project-open-mcp

echo
echo ">> Service status:"
systemctl --no-pager --full status project-open-mcp || true
echo
echo ">> Done. The MCP server listens on 127.0.0.1:8080 (endpoint /mcp)."
echo ">> Next: configure nginx + TLS (deploy/nginx-mcp.conf) so openclaw can"
echo ">> reach https://<host>/mcp. Smoke test locally:"
echo "     curl -s -u '<po_user>:<po_pass>' \\"
echo "       -H 'Accept: application/json, text/event-stream' \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"curl\",\"version\":\"0\"}}}' \\"
echo "       http://127.0.0.1:8080/mcp"
