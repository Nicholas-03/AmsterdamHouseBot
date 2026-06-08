#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="amsterdam-house-bot"
SERVICE_NAME="${APP_NAME}.service"
SERVICE_USER="amsterdambot"
SERVICE_HOME="/home/${SERVICE_USER}"
APP_DIR="/opt/${APP_NAME}"
ENV_DIR="/etc/${APP_NAME}"
ENV_FILE="${ENV_DIR}/bot.env"
DATA_DIR="/var/lib/${APP_NAME}"
DOCUMENTS_DIR="${DATA_DIR}/roofz-documents"
DB_PATH="${DATA_DIR}/listings.db"
LEGACY_DB_PATH="${APP_DIR}/listings.db"
UV_BIN="/usr/local/bin/uv"
UVX_BIN="/usr/local/bin/uvx"
UV_VERSION="0.8.15"
PYTHON_VERSION="3.13.7"
SERVICE_ENV=("HOME=${SERVICE_HOME}" "XDG_CACHE_HOME=${SERVICE_HOME}/.cache")

ARCHIVE_PATH="${1:-}"
UPLOADED_ENV="${2:-}"
DOCUMENTS_ARCHIVE="${3:-}"
STAGING_DIR="/tmp/${APP_NAME}-release"

log() {
    printf '\n[%s] %s\n' "$APP_NAME" "$*"
}

install_uv() {
    local arch target checksum archive_url tmp_dir archive_path

    arch="$(uname -m)"
    case "${arch}" in
        x86_64|amd64)
            target="uv-x86_64-unknown-linux-gnu"
            checksum="be9878e9d08ebcb621a683aba52e7fb8bbf92b2532e0d759026ffcc067673042"
            ;;
        aarch64|arm64)
            target="uv-aarch64-unknown-linux-gnu"
            checksum="6ede0fefa7db7be3d5d9eda8784a8e43b1cf5410720eb3da60ab1d2f66678e82"
            ;;
        *)
            echo "Unsupported architecture for pinned uv install: ${arch}" >&2
            exit 1
            ;;
    esac

    archive_url="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${target}.tar.gz"
    tmp_dir="$(mktemp -d)"
    archive_path="${tmp_dir}/${target}.tar.gz"

    curl -fL "${archive_url}" -o "${archive_path}"
    printf '%s  %s\n' "${checksum}" "${archive_path}" | sha256sum -c -
    tar -xzf "${archive_path}" -C "${tmp_dir}"
    install -m 0755 "${tmp_dir}/${target}/uv" "${UV_BIN}"
    install -m 0755 "${tmp_dir}/${target}/uvx" "${UVX_BIN}"
    rm -rf "${tmp_dir}"
}

if [[ "$(id -u)" -ne 0 ]]; then
    echo "This bootstrap script must be run as root." >&2
    exit 1
fi

if [[ -z "${ARCHIVE_PATH}" || ! -f "${ARCHIVE_PATH}" ]]; then
    echo "Deployment archive not found: ${ARCHIVE_PATH}" >&2
    exit 1
fi

if [[ -z "${UPLOADED_ENV}" || ! -f "${UPLOADED_ENV}" ]]; then
    echo "Uploaded environment file not found: ${UPLOADED_ENV}" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

log "Installing base system packages"
apt-get update
apt-get install -y ca-certificates curl unzip rsync xz-utils build-essential

if ! command -v "${UV_BIN}" >/dev/null 2>&1 || [[ "$("${UV_BIN}" --version 2>/dev/null | awk '{print $2}')" != "${UV_VERSION}" ]]; then
    log "Installing uv ${UV_VERSION}"
    install_uv
else
    log "uv ${UV_VERSION} is already installed"
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    log "Creating service user ${SERVICE_USER}"
    useradd \
        --system \
        --create-home \
        --home-dir "${SERVICE_HOME}" \
        --shell /usr/sbin/nologin \
        "${SERVICE_USER}"
fi

log "Extracting release archive"
rm -rf "${STAGING_DIR}"
mkdir -p "${STAGING_DIR}"
unzip -q "${ARCHIVE_PATH}" -d "${STAGING_DIR}"

log "Installing application files"
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0755 "${APP_DIR}"
if systemctl list-unit-files "${SERVICE_NAME}" >/dev/null 2>&1; then
    systemctl stop "${SERVICE_NAME}" || true
fi
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 "${DATA_DIR}"
if [[ -f "${LEGACY_DB_PATH}" && ! -f "${DB_PATH}" ]]; then
    log "Migrating legacy app-local database to ${DB_PATH}"
    install -m 0640 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${LEGACY_DB_PATH}" "${DB_PATH}"
fi
rsync -a --delete --exclude ".venv/" "${STAGING_DIR}/" "${APP_DIR}/"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"

log "Installing environment and data directories"
install -d -o root -g "${SERVICE_USER}" -m 0750 "${ENV_DIR}"
SANITIZED_ENV="$(mktemp)"
awk '!/^[[:space:]]*DB_PATH[[:space:]]*=/' "${UPLOADED_ENV}" > "${SANITIZED_ENV}"
install -m 0640 -o root -g "${SERVICE_USER}" "${SANITIZED_ENV}" "${ENV_FILE}"
rm -f "${SANITIZED_ENV}"
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 "${DATA_DIR}"
if [[ -n "${DOCUMENTS_ARCHIVE}" ]]; then
    if [[ ! -f "${DOCUMENTS_ARCHIVE}" ]]; then
        echo "Roofz document archive not found: ${DOCUMENTS_ARCHIVE}" >&2
        exit 1
    fi
    log "Installing Roofz application documents"
    rm -rf "${DOCUMENTS_DIR}"
    install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 "${DOCUMENTS_DIR}"
    unzip -q "${DOCUMENTS_ARCHIVE}" -d "${DOCUMENTS_DIR}"
    chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DOCUMENTS_DIR}"
    find "${DOCUMENTS_DIR}" -type d -exec chmod 0750 {} +
    find "${DOCUMENTS_DIR}" -type f -exec chmod 0640 {} +
fi
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA_DIR}"
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 "${SERVICE_HOME}/.cache"

cd "${APP_DIR}"

log "Installing Python ${PYTHON_VERSION} with uv"
runuser -u "${SERVICE_USER}" -- env "${SERVICE_ENV[@]}" "${UV_BIN}" python install "${PYTHON_VERSION}"

log "Installing Python dependencies"
runuser -u "${SERVICE_USER}" -- env "${SERVICE_ENV[@]}" UV_LINK_MODE=copy "${UV_BIN}" sync --locked --python "${PYTHON_VERSION}"

log "Installing Playwright system dependencies"
"${APP_DIR}/.venv/bin/python" -m playwright install-deps chromium

log "Installing browser assets"
runuser -u "${SERVICE_USER}" -- env "${SERVICE_ENV[@]}" "${APP_DIR}/.venv/bin/python" -m playwright install chromium

log "Installing systemd service"
install -m 0644 "${APP_DIR}/deploy/${SERVICE_NAME}" "/etc/systemd/system/${SERVICE_NAME}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

sleep 3
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    log "Service is running. SQLite database path: ${DB_PATH}"
else
    log "Service failed to start. Recent logs:"
    journalctl -u "${SERVICE_NAME}" -n 100 --no-pager
    exit 1
fi

if [[ -n "${DOCUMENTS_ARCHIVE}" ]]; then
    rm -f "${DOCUMENTS_ARCHIVE}"
fi
rm -rf "${STAGING_DIR}" "${ARCHIVE_PATH}" "${UPLOADED_ENV}"
log "Bootstrap complete"
