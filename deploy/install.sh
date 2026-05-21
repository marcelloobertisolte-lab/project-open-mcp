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
PY_VERSION="3.12"

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

# 3) Modern Python via uv. The host Python (3.5 on Ubuntu 16.04) is too old, so
#    we provision a standalone CPython under INSTALL_DIR without touching the
#    system. Everything lives in INSTALL_DIR so the service user can run it.
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required to install uv. Run: sudo apt install -y curl" >&2
  exit 1
fi

UV_BIN="/usr/local/bin/uv"
if [[ ! -x "${UV_BIN}" ]]; then
  echo ">> Installing uv to /usr/local/bin"
  curl -LsSf https://astral.sh/uv/install.sh \
    | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi

export UV_PYTHON_INSTALL_DIR="${INSTALL_DIR}/.python"
export UV_CACHE_DIR="${INSTALL_DIR}/.uv-cache"

echo ">> Provisioning CPython ${PY_VERSION} via uv"
"${UV_BIN}" python install "${PY_VERSION}"

echo ">> Creating venv"
"${UV_BIN}" venv "${INSTALL_DIR}/.venv" --python "${PY_VERSION}"

echo ">> Installing package"
"${UV_BIN}" pip install --python "${INSTALL_DIR}/.venv/bin/python" -e "${INSTALL_DIR}"

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
