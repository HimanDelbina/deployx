#!/usr/bin/env bash
# ==============================================================================
# DeployX Production Server Installer for Ubuntu 22.04 & 24.04 LTS
# Idempotent: Can be run multiple times safely.
# ==============================================================================

set -euo pipefail

# Visual formatting
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${CYAN}[DeployX]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[DeployX SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[DeployX WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[DeployX ERROR]${NC} $1" >&2
}

# 1. Require root or sudo privileges
if [[ $EUID -ne 0 ]]; then
   log_error "This installer must be run as root or with sudo:"
   echo "    sudo bash install.sh"
   exit 1
fi

log_info "Initializing DeployX Production Installer..."

# 2. Check Ubuntu OS and version
if [[ ! -f /etc/os-release ]]; then
    log_error "Unsupported operating system. /etc/os-release not found."
    exit 1
fi

source /etc/os-release

if [[ "$ID" != "ubuntu" ]]; then
    log_warn "Detected OS '$NAME' is not Ubuntu. DeployX is designed for Ubuntu 22.04 / 24.04 LTS."
else
    if [[ "$VERSION_ID" != "22.04" && "$VERSION_ID" != "24.04" ]]; then
        log_warn "Ubuntu version $VERSION_ID detected. Recommended versions: 22.04 LTS (Jammy) or 24.04 LTS (Noble)."
    else
        log_info "Verified supported operating system: $PRETTY_NAME"
    fi
fi

# 3. Update apt and install essential packages
log_info "Installing core system dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    openssh-client \
    python3 \
    python3-venv \
    python3-pip \
    tar \
    gzip

# 4. Configure Official Docker Repository if Docker is not installed
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    log_info "Configuring official Docker repository..."
    install -m 0755 -d /etc/apt/keyrings
    if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        chmod a+r /etc/apt/keyrings/docker.gpg
    fi

    UBUNTU_CODENAME="${UBUNTU_CODENAME:-$VERSION_CODENAME}"
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      ${UBUNTU_CODENAME} stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

    apt-get update -y
    log_info "Installing Docker Engine and Docker Compose v2 plugin..."
    apt-get install -y \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin
fi

# 5. Enable and start Docker daemon
log_info "Ensuring Docker daemon is active..."
systemctl enable --now docker

# Optional: Add original invoking user to docker group
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
    if ! id -nG "$SUDO_USER" | grep -qw "docker"; then
        log_info "Adding user '$SUDO_USER' to 'docker' group..."
        usermod -aG docker "$SUDO_USER"
    fi
fi

# 6. Create DeployX Directories with strict permissions
DEPLOYX_ROOT="/opt/deployx"
DEPLOYX_CONFIG_DIR="/etc/deployx"

log_info "Setting up DeployX directory structure in ${DEPLOYX_ROOT}..."
mkdir -p "${DEPLOYX_ROOT}/projects"
mkdir -p "${DEPLOYX_ROOT}/keys"
mkdir -p "${DEPLOYX_ROOT}/backups"
mkdir -p "${DEPLOYX_ROOT}/logs"
mkdir -p "${DEPLOYX_ROOT}/state"
mkdir -p "${DEPLOYX_ROOT}/generated"
mkdir -p "${DEPLOYX_CONFIG_DIR}"

# Set permissions
chmod 0755 "${DEPLOYX_ROOT}"
chmod 0750 "${DEPLOYX_ROOT}/projects"
chmod 0700 "${DEPLOYX_ROOT}/keys"       # Secret SSH keys strictly 700
chmod 0700 "${DEPLOYX_ROOT}/backups"    # Backups strictly 700
chmod 0750 "${DEPLOYX_ROOT}/logs"
chmod 0750 "${DEPLOYX_ROOT}/state"
chmod 0750 "${DEPLOYX_ROOT}/generated"
chmod 0755 "${DEPLOYX_CONFIG_DIR}"

# 7. Create default /etc/deployx/config.yml if not exists
CONFIG_FILE="${DEPLOYX_CONFIG_DIR}/config.yml"
if [[ ! -f "$CONFIG_FILE" ]]; then
    log_info "Creating default configuration at ${CONFIG_FILE}..."
    cat > "$CONFIG_FILE" << 'EOF'
version: 1
system:
  root_dir: /opt/deployx
  compose_command: "docker compose"
docker:
  network: "deployx_default"
EOF
    chmod 0644 "$CONFIG_FILE"
fi

# 8. Setup isolated Python Virtual Environment
VENV_DIR="${DEPLOYX_ROOT}/venv"
if [[ ! -d "$VENV_DIR" ]]; then
    log_info "Creating dedicated Python venv at ${VENV_DIR}..."
    python3 -m venv "$VENV_DIR"
fi

log_info "Upgrading pip and installing DeployX..."
"${VENV_DIR}/bin/pip" install --upgrade pip setuptools wheel

# Install DeployX: if running from repo source directory, install editable or package
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "${REPO_ROOT}/pyproject.toml" ]]; then
    log_info "Installing DeployX from local source directory ${REPO_ROOT}..."
    "${VENV_DIR}/bin/pip" install "${REPO_ROOT}"
else
    log_info "Installing DeployX core package..."
    "${VENV_DIR}/bin/pip" install deployx
fi

# 9. Create global executable symlink
log_info "Linking /usr/local/bin/deployx..."
ln -sf "${VENV_DIR}/bin/deployx" /usr/local/bin/deployx
chmod +x /usr/local/bin/deployx

# 10. Verify installation
log_info "Verifying DeployX installation..."
deployx --version

log_success "DeployX successfully installed!"
echo ""
echo -e "${GREEN}Running system doctor:${NC}"
deployx doctor || true
echo ""
echo -e "${CYAN}Get started with:${NC}"
echo "    deployx project add"
echo "    deployx --help"
