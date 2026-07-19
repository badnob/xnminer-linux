#!/usr/bin/env bash
# xnminer-linux one-shot host setup (Ubuntu/Debian family).
#
# Installs: python3, pip, venv, cmake, build tools, NVIDIA driver (if needed),
# CUDA toolkit (nvcc), pip requirements, and builds libxen_cuda.so.
#
# Usage (from a cloned repo):
#   chmod +x install.sh && ./install.sh
#
# One-liner (fresh machine):
#   curl -fsSL https://raw.githubusercontent.com/badnob/xnminer-linux/main/install.sh | bash
#
# Flags:
#   --no-driver     skip NVIDIA driver install
#   --no-cuda       skip CUDA toolkit install
#   --no-build      skip native engine build
#   --cpu-only      no driver/cuda/build (Python deps only)
#   --yes           non-interactive apt (-y)
set -euo pipefail

REPO_URL="${XNMINER_REPO_URL:-https://github.com/badnob/xnminer-linux.git}"
REPO_DIR_NAME="${XNMINER_DIR:-xnminer-linux}"
INSTALL_DRIVER=1
INSTALL_CUDA=1
BUILD_ENGINE=1
ASSUME_YES=1
CPU_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --no-driver) INSTALL_DRIVER=0 ;;
    --no-cuda) INSTALL_CUDA=0 ;;
    --no-build) BUILD_ENGINE=0 ;;
    --cpu-only)
      CPU_ONLY=1
      INSTALL_DRIVER=0
      INSTALL_CUDA=0
      BUILD_ENGINE=0
      ;;
    --yes|-y) ASSUME_YES=1 ;;
    --help|-h)
      sed -n '2,25p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (try --help)" >&2
      exit 1
      ;;
  esac
done

log()  { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1; }

# Resolve sudo
if [[ "${EUID}" -eq 0 ]]; then
  SUDO=()
else
  need_cmd sudo || die "sudo is required (or re-run as root)"
  SUDO=(sudo)
fi

APT_YES=()
if [[ "${ASSUME_YES}" -eq 1 ]]; then
  APT_YES=(-y)
fi

export DEBIAN_FRONTEND=noninteractive

detect_os() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_VER="${VERSION_ID:-}"
  else
    OS_ID=unknown
    OS_VER=
  fi
  case "${OS_ID}" in
    ubuntu|debian|linuxmint|pop)
      PKG=apt
      ;;
    *)
      PKG=other
      ;;
  esac
}

ensure_repo() {
  # If this script is already inside a checkout, use it.
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
  if [[ -n "${here}" && -f "${here}/main.py" && -f "${here}/requirements.txt" ]]; then
    MINER_ROOT="${here}"
    log "Using existing tree: ${MINER_ROOT}"
    return
  fi

  # Piped curl|bash: BASH_SOURCE may be empty or /dev/fd — clone into cwd.
  need_cmd git || {
    if [[ "${PKG}" == "apt" ]]; then
      log "Installing git..."
      "${SUDO[@]}" apt-get update -qq
      "${SUDO[@]}" apt-get install "${APT_YES[@]}" git
    else
      die "git is required to clone ${REPO_URL}"
    fi
  }

  if [[ -d "${REPO_DIR_NAME}/.git" || -f "${REPO_DIR_NAME}/main.py" ]]; then
    MINER_ROOT="$(cd "${REPO_DIR_NAME}" && pwd)"
    log "Using existing directory: ${MINER_ROOT}"
  else
    log "Cloning ${REPO_URL} → ./${REPO_DIR_NAME}"
    git clone --depth 1 "${REPO_URL}" "${REPO_DIR_NAME}"
    MINER_ROOT="$(cd "${REPO_DIR_NAME}" && pwd)"
  fi
}

apt_base_packages() {
  log "Installing system packages (python, pip, cmake, build tools)..."
  "${SUDO[@]}" apt-get update -qq
  "${SUDO[@]}" apt-get install "${APT_YES[@]}" \
    ca-certificates \
    curl \
    wget \
    git \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    cmake \
    ninja-build \
    pkg-config \
    pciutils
}

install_nvidia_driver() {
  if need_cmd nvidia-smi && nvidia-smi >/dev/null 2>&1; then
    log "NVIDIA driver already working ($(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1))"
    return 0
  fi

  if [[ "${INSTALL_DRIVER}" -ne 1 ]]; then
    warn "Skipping NVIDIA driver install (--no-driver)"
    return 0
  fi

  if ! lspci 2>/dev/null | grep -qi 'nvidia'; then
    warn "No NVIDIA PCI device detected — skipping driver install (use --cpu-only if intentional)"
    return 0
  fi

  log "Installing NVIDIA proprietary driver (ubuntu-drivers)..."
  "${SUDO[@]}" apt-get install "${APT_YES[@]}" ubuntu-drivers-common || true
  if need_cmd ubuntu-drivers; then
    # Recommended package; may require reboot before nvidia-smi works.
    "${SUDO[@]}" ubuntu-drivers autoinstall || {
      warn "ubuntu-drivers autoinstall failed — try: sudo apt install nvidia-driver-550"
    }
  else
    warn "ubuntu-drivers not available; install a driver manually, e.g. sudo apt install nvidia-driver-550"
  fi

  if ! need_cmd nvidia-smi || ! nvidia-smi >/dev/null 2>&1; then
    warn "nvidia-smi not ready yet. A reboot is usually required after driver install."
    warn "After reboot, re-run:  cd ${MINER_ROOT:-.} && ./install.sh --no-driver"
    NEED_REBOOT=1
  fi
}

install_cuda_toolkit() {
  if need_cmd nvcc; then
    log "CUDA nvcc already present ($(nvcc --version | tail -1))"
    return 0
  fi
  # Common install locations
  if [[ -x /usr/local/cuda/bin/nvcc ]]; then
    export PATH="/usr/local/cuda/bin:${PATH}"
    export LD_LIBRARY_PATH="/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    log "Found CUDA at /usr/local/cuda"
    return 0
  fi

  if [[ "${INSTALL_CUDA}" -ne 1 ]]; then
    warn "Skipping CUDA toolkit install (--no-cuda)"
    return 0
  fi

  log "Installing CUDA toolkit (nvcc) via distro packages..."
  # Ubuntu/Debian meta-package — good enough for building the engine.
  # For bleeding-edge GPUs you may still want NVIDIA's official CUDA network repo.
  if ! "${SUDO[@]}" apt-get install "${APT_YES[@]}" nvidia-cuda-toolkit nvidia-cuda-dev; then
    warn "nvidia-cuda-toolkit install failed."
    warn "Install CUDA Toolkit from https://developer.nvidia.com/cuda-downloads then re-run."
    return 0
  fi

  if need_cmd nvcc; then
    log "nvcc OK: $(nvcc --version | tail -1)"
  elif [[ -x /usr/bin/nvcc ]]; then
    log "nvcc at /usr/bin/nvcc"
  else
    warn "nvcc still not on PATH after package install"
  fi
}

setup_python() {
  cd "${MINER_ROOT}"
  log "Python: $(python3 --version 2>&1)"

  if [[ ! -d .venv ]]; then
    log "Creating virtualenv .venv ..."
    python3 -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install --upgrade pip wheel setuptools
  log "Installing Python requirements..."
  pip install -r requirements.txt
  log "Python packages OK"
}

build_engine() {
  cd "${MINER_ROOT}"
  if [[ "${BUILD_ENGINE}" -ne 1 ]]; then
    warn "Skipping native CUDA build (--no-build)"
    return 0
  fi

  if [[ "${NEED_REBOOT:-0}" -eq 1 ]]; then
    warn "Skipping CUDA engine build until after reboot (driver not ready)"
    return 0
  fi

  if ! need_cmd nvcc && [[ ! -x /usr/local/cuda/bin/nvcc ]]; then
    warn "nvcc missing — cannot build libxen_cuda.so yet"
    warn "Install CUDA, then:  ./native/build.sh"
    return 0
  fi

  if [[ -x /usr/local/cuda/bin/nvcc ]]; then
    export PATH="/usr/local/cuda/bin:${PATH}"
    export LD_LIBRARY_PATH="/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  fi

  chmod +x native/build.sh start-miner.sh 2>/dev/null || true
  log "Building libxen_cuda.so ..."
  if ./native/build.sh; then
    log "CUDA engine built: native/build/bin/libxen_cuda.so"
  else
    warn "Native build failed. Check CUDA arches / driver match, then re-run ./native/build.sh"
    warn "Older GPUs example:  CMAKE_CUDA_ARCHITECTURES=75;86 ./native/build.sh"
  fi
}

print_next_steps() {
  cat <<EOF

────────────────────────────────────────────────────────
  xnminer-linux setup finished
  Tree: ${MINER_ROOT}
────────────────────────────────────────────────────────
EOF

  if [[ "${NEED_REBOOT:-0}" -eq 1 ]]; then
    cat <<EOF
  1) Reboot this machine (new NVIDIA driver).
  2) cd ${MINER_ROOT}
  3) source .venv/bin/activate
  4) ./install.sh --no-driver     # finish CUDA build if skipped
  5) ./start-miner.sh
EOF
  else
    cat <<EOF
  Start mining:
    cd ${MINER_ROOT}
    source .venv/bin/activate   # if you used the venv
    ./start-miner.sh

  Or:
    python3 main.py
EOF
  fi

  if [[ "${CPU_ONLY}" -eq 1 ]]; then
    echo "  Note: CPU-only mode — set backend = cpu in miner.ini"
  fi
  echo
}

main() {
  NEED_REBOOT=0
  detect_os
  log "OS: ${OS_ID:-?} ${OS_VER:-}  package manager: ${PKG}"

  if [[ "${PKG}" != "apt" ]]; then
    warn "This installer targets Ubuntu/Debian (apt)."
    warn "On other distros, install manually: python3, pip, cmake, NVIDIA driver, CUDA toolkit."
    ensure_repo
    setup_python
    print_next_steps
    exit 0
  fi

  ensure_repo
  apt_base_packages
  install_nvidia_driver
  install_cuda_toolkit
  setup_python
  build_engine
  print_next_steps
}

main "$@"
