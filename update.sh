#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 0022

log() { printf '[INFO] %s\n' "$*"; }
ok()  { printf '[OK] %s\n' "$*"; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

on_error() {
    local exit_code=$?
    printf '[ERROR] Atlas update failed at line %s (exit code %s).\n' \
        "${BASH_LINENO[0]}" "$exit_code" >&2
    exit "$exit_code"
}
trap on_error ERR

require_root() {
    [[ ${EUID} -eq 0 ]] || die "Run this script with sudo: sudo ./update.sh"
}

source_root() {
    local script_dir
    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
    [[ -d "${script_dir}/.git" ]] || \
        die "update.sh must be located in the root of an Aricoma Atlas Git checkout"
    printf '%s\n' "$script_dir"
}

require_root

SOURCE_DIR="$(source_root)"
SOURCE_OWNER="$(stat -c '%U' "$SOURCE_DIR")"
SOURCE_HOME="$(getent passwd "$SOURCE_OWNER" | cut -d: -f6)"

[[ -n "$SOURCE_OWNER" && "$SOURCE_OWNER" != "UNKNOWN" ]] || \
    die "Cannot resolve the source checkout owner"
[[ -n "$SOURCE_HOME" && -d "$SOURCE_HOME" ]] || \
    die "Cannot resolve the home directory for ${SOURCE_OWNER}"

source_git() {
    runuser -u "$SOURCE_OWNER" -- env \
        HOME="$SOURCE_HOME" \
        USER="$SOURCE_OWNER" \
        LOGNAME="$SOURCE_OWNER" \
        git -C "$SOURCE_DIR" "$@"
}

[[ -z "$(source_git status --porcelain)" ]] || \
    die "Source repository has uncommitted or untracked changes; refusing to update"

BRANCH="$(source_git symbolic-ref --quiet --short HEAD)" || \
    die "Source repository is in detached HEAD state"
UPSTREAM="$(source_git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}')" || \
    die "Branch ${BRANCH} has no configured upstream"
BEFORE="$(source_git rev-parse HEAD)"

log "Source checkout: ${SOURCE_DIR}"
log "Branch: ${BRANCH}"
log "Upstream: ${UPSTREAM}"
log "Installed source before update: ${BEFORE}"

source_git pull --ff-only

AFTER="$(source_git rev-parse HEAD)"
UPSTREAM_HEAD="$(source_git rev-parse '@{upstream}')"
[[ "$AFTER" == "$UPSTREAM_HEAD" ]] || \
    die "Local branch does not exactly match ${UPSTREAM} after pull"
[[ -z "$(source_git status --porcelain)" ]] || \
    die "Source repository changed during update; refusing to install"

ok "Source checkout matches ${UPSTREAM} at ${AFTER}"

"${SOURCE_DIR}/install.sh"

[[ -x /usr/local/sbin/atlasctl ]] || \
    die "atlasctl is unavailable after installation"

exec /usr/local/sbin/atlasctl deploy "$@"
