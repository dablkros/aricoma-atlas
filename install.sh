#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 0022

ATLAS_USER="atlas"
ATLAS_GROUP="atlas"
ATLAS_HOME="/home/atlas"
ATLAS_ROOT="/opt/aricoma-atlas"
RUNTIME_DIR="${ATLAS_ROOT}/.runtime"
VENV_DIR="${ATLAS_ROOT}/.venv"

log()  { printf '[INFO] %s\n' "$*"; }
ok()   { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die()  { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

header() {
    printf '\n==============================================================================\n'
    printf '%s\n' "$1"
    printf '==============================================================================\n'
}

on_error() {
    local exit_code=$?
    printf '\n[ERROR] Host bootstrap failed at line %s (exit code %s).\n' "${BASH_LINENO[0]}" "$exit_code" >&2
    exit "$exit_code"
}
trap on_error ERR

require_root() {
    [[ ${EUID} -eq 0 ]] || die "Run this script with sudo: sudo ./install.sh"
}

source_root() {
    local script_dir
    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
    [[ -d "${script_dir}/.git" ]] || \
        die "install.sh must be located in the root of an Aricoma Atlas Git checkout"
    printf '%s\n' "$script_dir"
}

source_git() {
    local source_dir=$1
    shift
    git -c "safe.directory=${source_dir}" -C "$source_dir" "$@"
}

target_git() {
    git -c "safe.directory=${ATLAS_ROOT}" -C "$ATLAS_ROOT" "$@"
}

validate_source_repo() {
    local source_dir=$1

    [[ -f "${source_dir}/scripts/deploy_atlas.py" ]] || \
        die "Missing scripts/deploy_atlas.py in ${source_dir}"
    [[ -f "${source_dir}/scripts/deploy_netbox.py" ]] || \
        die "Missing scripts/deploy_netbox.py in ${source_dir}"
    [[ -f "${source_dir}/requirements.txt" ]] || \
        die "Missing requirements.txt in ${source_dir}"

    local dirty
    dirty="$(source_git "$source_dir" status --porcelain --untracked-files=no)"
    [[ -z "$dirty" ]] || die "Source repository has modified tracked files. Commit or discard them before installing."
}

validate_os() {
    header "HOST PREFLIGHT"

    [[ -r /etc/os-release ]] || die "/etc/os-release is missing"
    # shellcheck disable=SC1091
    . /etc/os-release

    [[ "${ID:-}" == "ubuntu" ]] || \
        die "Unsupported OS: ${PRETTY_NAME:-unknown}. Atlas host bootstrap currently supports Ubuntu only."

    case "${VERSION_ID:-}" in
        22.04|24.04|26.04)
            ;;
        *)
            die "Unsupported Ubuntu release: ${VERSION_ID:-unknown}. Supported releases: 22.04, 24.04, 26.04."
            ;;
    esac

    local arch
    arch="$(dpkg --print-architecture)"
    [[ "$arch" == "amd64" ]] || \
        die "Atlas is currently validated for amd64 hosts; detected architecture: ${arch}"

    ok "Operating system: ${PRETTY_NAME} (${arch})"

    local available_kb
    available_kb="$(df -Pk /opt | awk 'NR==2 {print $4}')"
    log "Free space available on /opt filesystem: $((available_kb / 1024)) MiB"

    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
        warn "UFW is active. Docker-published ports have special firewall semantics; review Docker DOCKER-USER filtering for this host."
    fi
}

install_host_packages() {
    header "HOST PACKAGES"

    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        openssl \
        python3 \
        python3-pip \
        python3-venv

    ok "Required host packages installed"
}

remove_docker_conflicts() {
    local candidates=(
        docker.io
        docker-compose
        docker-compose-v2
        docker-doc
        docker-buildx
        podman-docker
        containerd
        runc
    )
    local installed=()
    local package

    for package in "${candidates[@]}"; do
        if dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null | grep -qx installed; then
            installed+=("$package")
        fi
    done

    if ((${#installed[@]})); then
        log "Removing Docker-conflicting distribution packages: ${installed[*]}"
        apt-get remove -y "${installed[@]}"
    fi
}

configure_docker_repository() {
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    local codename arch
    # shellcheck disable=SC1091
    . /etc/os-release
    codename="${UBUNTU_CODENAME:-${VERSION_CODENAME}}"
    arch="$(dpkg --print-architecture)"

    cat > /etc/apt/sources.list.d/docker.sources <<EOF_DOCKER
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${codename}
Components: stable
Architectures: ${arch}
Signed-By: /etc/apt/keyrings/docker.asc
EOF_DOCKER

    apt-get update
}

install_docker() {
    header "DOCKER ENGINE"

    remove_docker_conflicts
    configure_docker_repository

    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin

    systemctl enable --now docker.service
    systemctl enable --now containerd.service

    docker info >/dev/null
    docker compose version >/dev/null

    ok "$(docker --version)"
    ok "$(docker compose version)"
    ok "Docker daemon reachable"
}

ensure_atlas_user() {
    header "ATLAS SERVICE ACCOUNT"

    if id "$ATLAS_USER" >/dev/null 2>&1; then
        local actual_home
        actual_home="$(getent passwd "$ATLAS_USER" | cut -d: -f6)"
        [[ "$actual_home" == "$ATLAS_HOME" ]] || \
            die "Existing user ${ATLAS_USER} has unexpected home ${actual_home}; expected ${ATLAS_HOME}"
        ok "Service account ${ATLAS_USER} already exists"
    else
        useradd \
            --create-home \
            --home-dir "$ATLAS_HOME" \
            --shell /usr/sbin/nologin \
            "$ATLAS_USER"
        passwd -l "$ATLAS_USER" >/dev/null 2>&1 || true
        ok "Created service account ${ATLAS_USER}"
    fi

    usermod -aG docker "$ATLAS_USER"

    # A new runuser process resolves supplementary groups immediately; no SSH
    # logout/login is required for this dedicated service account.
    runuser -u "$ATLAS_USER" -- docker info >/dev/null
    ok "${ATLAS_USER} can access Docker"

    warn "Membership in the docker group is root-equivalent by design. The dedicated ${ATLAS_USER} account must not be used as a general interactive account."
}

install_repository() {
    local source_dir=$1
    local source_commit
    source_commit="$(source_git "$source_dir" rev-parse HEAD)"

    header "ATLAS APPLICATION"
    log "Source commit: ${source_commit}"

    install -d -m 0755 /opt

    if [[ -e "$ATLAS_ROOT" && ! -d "${ATLAS_ROOT}/.git" ]]; then
        die "${ATLAS_ROOT} exists but is not an Atlas Git checkout"
    fi

    if [[ ! -d "${ATLAS_ROOT}/.git" ]]; then
        git -c "safe.directory=${source_dir}" clone --no-hardlinks "$source_dir" "$ATLAS_ROOT"
    else
        local target_dirty
        target_dirty="$(target_git status --porcelain --untracked-files=no)"
        [[ -z "$target_dirty" ]] || \
            die "${ATLAS_ROOT} contains modified tracked files; refusing to overwrite them"

        target_git fetch --force "$source_dir" "$source_commit"
        target_git checkout --detach --force FETCH_HEAD
    fi

    target_git checkout --detach --force "$source_commit"

    chown -R "${ATLAS_USER}:${ATLAS_GROUP}" "$ATLAS_ROOT"
    chmod 0750 "$ATLAS_ROOT"

    install -d -m 0700 -o "$ATLAS_USER" -g "$ATLAS_GROUP" "$RUNTIME_DIR"

    # Site-specific proxy/TLS configuration is intentionally not invented by
    # the installer. If the administrator prepared .runtime/proxy.yaml in the
    # source checkout, carry only that configuration file into the installed
    # runtime. Never copy the rest of .runtime, because it may contain OpenBao
    # identities or other machine-specific secrets.
    if [[ -f "${source_dir}/.runtime/proxy.yaml" && ! -f "${RUNTIME_DIR}/proxy.yaml" ]]; then
        install -m 0600 -o "$ATLAS_USER" -g "$ATLAS_GROUP" \
            "${source_dir}/.runtime/proxy.yaml" "${RUNTIME_DIR}/proxy.yaml"
        ok "Copied prepared site proxy configuration"
    fi

    local installed_commit
    installed_commit="$(target_git rev-parse HEAD)"
    [[ "$installed_commit" == "$source_commit" ]] || \
        die "Installed Atlas commit ${installed_commit} does not match source commit ${source_commit}"

    ok "Atlas repository installed at ${ATLAS_ROOT}"
    ok "Installed commit: ${installed_commit}"
}

install_python_environment() {
    header "PYTHON ENVIRONMENT"

    if [[ ! -x "${VENV_DIR}/bin/python3" ]]; then
        runuser -u "$ATLAS_USER" -- \
            python3 -m venv "$VENV_DIR"
        ok "Created Python virtual environment"
    else
        ok "Python virtual environment already exists"
    fi

    runuser -u "$ATLAS_USER" -- env HOME="$ATLAS_HOME" \
        "${VENV_DIR}/bin/python3" -m pip install --upgrade pip

    runuser -u "$ATLAS_USER" -- env HOME="$ATLAS_HOME" \
        "${VENV_DIR}/bin/python3" -m pip install -r "${ATLAS_ROOT}/requirements.txt"

    runuser -u "$ATLAS_USER" -- env HOME="$ATLAS_HOME" \
        "${VENV_DIR}/bin/python3" -c 'import requests, yaml; print("Python dependencies import successfully")'

    ok "Atlas Python dependencies installed"
}

verify_installation() {
    header "INSTALLATION VERIFY"

    [[ -x "${VENV_DIR}/bin/python3" ]] || die "Atlas Python virtual environment is missing"
    [[ -f "${ATLAS_ROOT}/scripts/deploy_atlas.py" ]] || die "Atlas deploy script is missing"
    [[ "$(stat -c '%a' "$RUNTIME_DIR")" == "700" ]] || die "${RUNTIME_DIR} must have mode 0700"

    runuser -u "$ATLAS_USER" -- docker info >/dev/null
    runuser -u "$ATLAS_USER" -- docker compose version >/dev/null
    runuser -u "$ATLAS_USER" -- env HOME="$ATLAS_HOME" \
        "${VENV_DIR}/bin/python3" -m py_compile \
        "${ATLAS_ROOT}/scripts/deploy_atlas.py" \
        "${ATLAS_ROOT}/scripts/deploy_netbox.py" \
        "${ATLAS_ROOT}/scripts/deploy_openbao.py" \
        "${ATLAS_ROOT}/scripts/deploy_oxidized.py" \
        "${ATLAS_ROOT}/scripts/deploy_proxy.py"

    ok "Host is ready for Atlas deployment"
}

main() {
    require_root

    local source_dir
    source_dir="$(source_root)"
    validate_source_repo "$source_dir"
    validate_os
    install_host_packages
    install_docker
    ensure_atlas_user
    install_repository "$source_dir"
    install_python_environment
    verify_installation

    header "ATLAS HOST READY"
    printf 'Installation root: %s\n' "$ATLAS_ROOT"
    printf 'Service account:   %s\n' "$ATLAS_USER"
    printf '\nNext step:\n'
    printf '  sudo ./deploy.sh\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
