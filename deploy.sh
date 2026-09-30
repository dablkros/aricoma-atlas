#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    printf '[ERROR] Run this script with sudo: sudo ./deploy.sh\n' >&2
    exit 1
fi

[[ -x /usr/local/sbin/atlasctl ]] || {
    printf '[ERROR] atlasctl is not installed. Run sudo ./install.sh first.\n' >&2
    exit 1
}

exec /usr/local/sbin/atlasctl deploy "$@"
