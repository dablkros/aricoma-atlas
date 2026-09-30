#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 0022

ATLAS_USER="atlas"
ATLAS_HOME="/home/atlas"
ATLAS_ROOT="/opt/aricoma-atlas"
PYTHON="${ATLAS_ROOT}/.venv/bin/python3"
DEPLOY_SCRIPT="${ATLAS_ROOT}/scripts/deploy_atlas.py"

log() { printf '[INFO] %s\n' "$*"; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

if [[ ${EUID} -ne 0 ]]; then
    die "Run this script with sudo: sudo ./deploy.sh"
fi

id "$ATLAS_USER" >/dev/null 2>&1 || die "Atlas service account does not exist. Run sudo ./install.sh first."
[[ -x "$PYTHON" ]] || die "Atlas virtual environment is missing. Run sudo ./install.sh first."
[[ -f "$DEPLOY_SCRIPT" ]] || die "Atlas deploy script is missing. Run sudo ./install.sh first."
[[ -d "${ATLAS_ROOT}/.runtime" ]] || die "Atlas runtime directory is missing. Run sudo ./install.sh first."

PROXY_CONFIG="${ATLAS_PROXY_CONFIG:-${ATLAS_ROOT}/.runtime/proxy.yaml}"
[[ -f "$PROXY_CONFIG" ]] || \
    die "Atlas site proxy configuration is missing: ${PROXY_CONFIG}. Prepare the customer DNS/TLS configuration before deployment."

runuser -u "$ATLAS_USER" -- docker info >/dev/null || \
    die "Atlas service account cannot access Docker"

ENV_ARGS=(
    "HOME=${ATLAS_HOME}"
    "USER=${ATLAS_USER}"
    "LOGNAME=${ATLAS_USER}"
    "PYTHONUNBUFFERED=1"
    "PATH=${ATLAS_ROOT}/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)

# Preserve only the deployment overrides intentionally supported by the current
# Atlas scripts. Do not pass the invoking root user's full environment through.
for name in OPENBAO_URL ATLAS_PROXY_CONFIG; do
    if [[ -n "${!name:-}" ]]; then
        ENV_ARGS+=("${name}=${!name}")
    fi
done

log "Deploying Atlas as service account ${ATLAS_USER}"
log "Application root: ${ATLAS_ROOT}"

cd "$ATLAS_ROOT"
exec runuser -u "$ATLAS_USER" -- env "${ENV_ARGS[@]}" \
    "$PYTHON" "$DEPLOY_SCRIPT" "$@"
