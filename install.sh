#!/usr/bin/env bash
# ==============================================================================
# DeployX Root Bootstrap Installer for Ubuntu Linux
# Automates bootstrap, clones verified official source, and invokes internal installer.
# ==============================================================================

set -euo pipefail

# Visual formatting
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() {
    echo -e "${CYAN}[DeployX Bootstrap]${NC} $1"
}

log_error() {
    echo -e "${RED}[DeployX Bootstrap ERROR]${NC} $1" >&2
}

# 1. Require root or sudo privileges
if [[ $EUID -ne 0 ]]; then
   log_error "This installer must be run as root or with sudo:"
   echo "    curl -fsSL https://raw.githubusercontent.com/HimanDelbina/deployx/main/install.sh | sudo bash" >&2
   exit 1
fi

log_info "Initializing DeployX Bootstrap Installer..."

# 2. Check Ubuntu OS
if [[ ! -f /etc/os-release ]]; then
    log_error "Unsupported operating system. /etc/os-release not found."
    exit 1
fi

source /etc/os-release
if [[ "$ID" != "ubuntu" ]]; then
    echo -e "${YELLOW}[DeployX Bootstrap WARNING]${NC} Detected OS '$NAME' is not Ubuntu. DeployX is designed for Ubuntu 22.04 / 24.04 LTS."
else
    if [[ "$VERSION_ID" != "22.04" && "$VERSION_ID" != "24.04" ]]; then
        echo -e "${YELLOW}[DeployX Bootstrap WARNING]${NC} Ubuntu $VERSION_ID detected. DeployX has not yet been formally validated on this version. Continuing in compatibility mode."
    fi
fi

# 3. Resolve and validate target version / branch
DEPLOYX_VERSION="${DEPLOYX_VERSION:-main}"
if [[ ! "$DEPLOYX_VERSION" =~ ^[a-zA-Z0-9._/-]+$ ]]; then
    log_error "Invalid DEPLOYX_VERSION '${DEPLOYX_VERSION}'. Only alphanumeric characters, dots, dashes, and underscores allowed."
    exit 1
fi

# 4. Install bootstrap dependencies if missing
export DEBIAN_FRONTEND=noninteractive
MISSING_PKGS=()
for pkg in git curl ca-certificates; do
    if ! command -v "$pkg" >/dev/null 2>&1; then
        MISSING_PKGS+=("$pkg")
    fi
done

if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
    log_info "Installing bootstrap dependencies: ${MISSING_PKGS[*]}..."
    apt-get update -y
    apt-get install -y --no-install-recommends "${MISSING_PKGS[@]}"
fi

# 5. Create secure temporary workspace with guaranteed cleanup
TMP_DIR="$(mktemp -d -t deployx-bootstrap-XXXXXX)"
cleanup() {
    if [[ -d "$TMP_DIR" ]]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT INT TERM

# 6. Clone official DeployX repository strictly from official source
OFFICIAL_REPO="https://github.com/HimanDelbina/deployx.git"
log_info "Cloning DeployX (${DEPLOYX_VERSION}) from ${OFFICIAL_REPO}..."

git clone \
    --depth 1 \
    --branch "$DEPLOYX_VERSION" \
    "$OFFICIAL_REPO" \
    "${TMP_DIR}/deployx"

# 7. Handover execution to verified internal installer
INTERNAL_INSTALLER="${TMP_DIR}/deployx/scripts/install.sh"
if [[ ! -f "$INTERNAL_INSTALLER" ]]; then
    log_error "Internal installer script missing at ${INTERNAL_INSTALLER}."
    exit 1
fi

log_info "Launching DeployX system installation..."
export PIP_INDEX_URL="${PIP_INDEX_URL:-}"
bash "$INTERNAL_INSTALLER"
