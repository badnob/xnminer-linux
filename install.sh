#!/usr/bin/env bash
# xnminer-linux host setup (Ubuntu / Debian family).
#
# Install order (important — avoids driver/CUDA/pip conflicts):
#   1) apt base tools
#   2) Python 3.10+ + pip + venv + cmake + compilers
#   3) NVIDIA proprietary driver (if GPU present / not already working)
#   4) CUDA *toolkit only* (nvcc) — does NOT reinstall drivers
#   5) Python venv + requirements.txt
#   6) Build libxen_cuda.so (needs cmake + nvcc; runtime needs driver)
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
#   --cpu-only      Python deps only (no driver / CUDA / build)
#   --yes           non-interactive apt (-y)  [default]
#   --help
set -euo pipefail

REPO_URL="${XNMINER_REPO_URL:-https://github.com/badnob/xnminer-linux.git}"
REPO_DIR_NAME="${XNMINER_DIR:-xnminer-linux}"
INSTALL_DRIVER=1
INSTALL_CUDA=1
BUILD_ENGINE=1
ASSUME_YES=1
CPU_ONLY=0
NEED_REBOOT=0
MINER_ROOT=""

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
      sed -n '2,30p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (try --help)" >&2
      exit 1
      ;;
  esac
done

log()  { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32mOK:\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1; }

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
# Avoid interactive needrestart prompts on servers
export NEEDRESTART_MODE="${NEEDRESTART_MODE:-a}"

# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------
detect_os() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_VER="${VERSION_ID:-}"
    OS_CODENAME="${VERSION_CODENAME:-}"
  else
    OS_ID=unknown
    OS_VER=
    OS_CODENAME=
  fi
  case "${OS_ID}" in
    ubuntu|debian|linuxmint|pop) PKG=apt ;;
    *) PKG=other ;;
  esac
}

# Map Ubuntu version → NVIDIA CUDA apt repo slug (ubuntu2204 / ubuntu2404).
cuda_repo_slug() {
  case "${OS_ID}-${OS_VER}" in
    ubuntu-22.04|linuxmint-21*|pop-22.04) echo "ubuntu2204" ;;
    ubuntu-24.04|linuxmint-22*|pop-24.04) echo "ubuntu2404" ;;
    ubuntu-20.04) echo "ubuntu2004" ;;
    debian-12) echo "ubuntu2204" ;;  # close enough for many installs
    debian-13) echo "ubuntu2404" ;;
    *)
      # Best effort from major.minor
      if [[ "${OS_VER}" == 24.* ]]; then echo "ubuntu2404"
      elif [[ "${OS_VER}" == 22.* ]]; then echo "ubuntu2204"
      else echo ""
      fi
      ;;
  esac
}

# ---------------------------------------------------------------------------
# PATH helpers for CUDA (toolkit under /usr/local/cuda or distro paths)
# ---------------------------------------------------------------------------
export_cuda_env() {
  local candidates=(
    /usr/local/cuda/bin
    /usr/local/cuda-12.6/bin
    /usr/local/cuda-12.5/bin
    /usr/local/cuda-12.4/bin
    /usr/local/cuda-12.3/bin
    /usr/local/cuda-12.2/bin
    /usr/local/cuda-12.1/bin
    /usr/local/cuda-12.0/bin
    /usr/local/cuda-11.8/bin
  )
  local d
  for d in "${candidates[@]}"; do
    if [[ -x "${d}/nvcc" ]]; then
      export PATH="${d}:${PATH}"
      local libdir
      libdir="$(dirname "${d}")/lib64"
      if [[ -d "${libdir}" ]]; then
        export LD_LIBRARY_PATH="${libdir}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
      fi
      return 0
    fi
  done
  # Distro nvcc is usually already on PATH (/usr/bin/nvcc)
  return 0
}

have_nvcc() {
  export_cuda_env
  need_cmd nvcc || [[ -x /usr/local/cuda/bin/nvcc ]]
}

have_working_driver() {
  need_cmd nvidia-smi && nvidia-smi >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# Repo checkout
# ---------------------------------------------------------------------------
ensure_repo() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
  if [[ -n "${here}" && -f "${here}/main.py" && -f "${here}/requirements.txt" ]]; then
    MINER_ROOT="${here}"
    log "Phase 0 — using existing tree: ${MINER_ROOT}"
    return
  fi

  if ! need_cmd git; then
    if [[ "${PKG}" == "apt" ]]; then
      log "Installing git (needed to clone)..."
      "${SUDO[@]}" apt-get update -qq
      "${SUDO[@]}" apt-get install "${APT_YES[@]}" git ca-certificates
    else
      die "git is required to clone ${REPO_URL}"
    fi
  fi

  if [[ -d "${REPO_DIR_NAME}/.git" || -f "${REPO_DIR_NAME}/main.py" ]]; then
    MINER_ROOT="$(cd "${REPO_DIR_NAME}" && pwd)"
    log "Phase 0 — using existing directory: ${MINER_ROOT}"
  else
    log "Phase 0 — cloning ${REPO_URL} → ./${REPO_DIR_NAME}"
    git clone --depth 1 "${REPO_URL}" "${REPO_DIR_NAME}"
    MINER_ROOT="$(cd "${REPO_DIR_NAME}" && pwd)"
  fi
}

# ---------------------------------------------------------------------------
# Phase 1: base OS packages (no NVIDIA / CUDA yet)
# ---------------------------------------------------------------------------
apt_update() {
  log "Phase 1a — apt update"
  "${SUDO[@]}" apt-get update -qq
}

install_base_packages() {
  log "Phase 1b — base tools (no GPU packages yet)"
  "${SUDO[@]}" apt-get install "${APT_YES[@]}" \
    ca-certificates \
    curl \
    wget \
    gnupg \
    git \
    pciutils \
    software-properties-common \
    apt-transport-https
  ok "base tools installed"
}

# ---------------------------------------------------------------------------
# Phase 2: Python + cmake + compilers  (before GPU stack)
# ---------------------------------------------------------------------------
install_python_and_build_tools() {
  log "Phase 2 — Python 3, pip, venv, cmake, compilers"

  "${SUDO[@]}" apt-get install "${APT_YES[@]}" \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    cmake \
    ninja-build \
    pkg-config \
    g++ \
    make

  need_cmd python3 || die "python3 failed to install"
  need_cmd cmake || die "cmake failed to install"

  local py_ver cmake_ver
  py_ver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  # Require 3.10+
  if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    die "Python ${py_ver} found; need Python 3.10+. Upgrade python3 and re-run."
  fi
  ok "python3 ${py_ver}"

  # Ensure pip works via python3 -m pip (never rely on bare `pip` alone)
  if ! python3 -m pip --version >/dev/null 2>&1; then
    warn "python3 -m pip missing — bootstrapping ensurepip"
    python3 -m ensurepip --upgrade 2>/dev/null || \
      "${SUDO[@]}" apt-get install "${APT_YES[@]}" python3-pip
  fi
  python3 -m pip --version >/dev/null 2>&1 || die "pip is not available for python3"
  ok "pip: $(python3 -m pip --version)"

  cmake_ver="$(cmake --version | head -1)"
  # cmake 3.18+ required by native/engine/CMakeLists.txt
  if ! cmake --version | head -1 | grep -qE 'version (3\.(1[8-9]|[2-9][0-9])|[4-9]\.)'; then
    warn "${cmake_ver} — need 3.18+. On older distros install a newer cmake."
  else
    ok "${cmake_ver}"
  fi

  need_cmd g++ || die "g++ failed to install"
  ok "g++ $(g++ -dumpversion 2>/dev/null || true)"
}

# ---------------------------------------------------------------------------
# Phase 3: NVIDIA driver only
# ---------------------------------------------------------------------------
install_nvidia_driver() {
  log "Phase 3 — NVIDIA driver"

  if have_working_driver; then
    local ver
    ver="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || true)"
    ok "driver already working${ver:+ (${ver})}"
    return 0
  fi

  if [[ "${INSTALL_DRIVER}" -ne 1 ]]; then
    warn "Skipping driver install (--no-driver)"
    return 0
  fi

  if ! lspci 2>/dev/null | grep -qi nvidia; then
    warn "No NVIDIA PCI device detected — skipping driver (use --cpu-only if intentional)"
    return 0
  fi

  # Kernel headers required for DKMS modules
  log "Installing linux headers for current kernel..."
  "${SUDO[@]}" apt-get install "${APT_YES[@]}" \
    "linux-headers-$(uname -r)" \
    dkms \
    || warn "Could not install linux-headers-$(uname -r) — driver DKMS may fail"

  log "Installing proprietary NVIDIA driver (ubuntu-drivers recommended)..."
  "${SUDO[@]}" apt-get install "${APT_YES[@]}" ubuntu-drivers-common || true

  if need_cmd ubuntu-drivers; then
    # autoinstall picks a driver matched to this GPU + kernel
    if ! "${SUDO[@]}" ubuntu-drivers autoinstall; then
      warn "ubuntu-drivers autoinstall failed — trying a recent LTS driver package"
      "${SUDO[@]}" apt-get install "${APT_YES[@]}" nvidia-driver-550 \
        || "${SUDO[@]}" apt-get install "${APT_YES[@]}" nvidia-driver-535 \
        || warn "Could not install nvidia-driver-* automatically"
    fi
  else
    warn "ubuntu-drivers unavailable; trying nvidia-driver-550"
    "${SUDO[@]}" apt-get install "${APT_YES[@]}" nvidia-driver-550 \
      || warn "Manual driver install required"
  fi

  if have_working_driver; then
    ok "nvidia-smi works (no reboot needed)"
  else
    NEED_REBOOT=1
    warn "Driver packages installed, but nvidia-smi is not ready yet."
    warn "A reboot is required before GPU mining. Toolkit/Python can still be installed."
  fi
}

# ---------------------------------------------------------------------------
# Phase 4: CUDA toolkit ONLY (nvcc) — never the full "cuda" meta package
# ---------------------------------------------------------------------------
install_cuda_toolkit() {
  log "Phase 4 — CUDA toolkit (nvcc only; does not replace your driver)"

  export_cuda_env
  if have_nvcc; then
    ok "nvcc already present: $(nvcc --version 2>/dev/null | tail -1)"
    return 0
  fi

  if [[ "${INSTALL_CUDA}" -ne 1 ]]; then
    warn "Skipping CUDA toolkit (--no-cuda)"
    return 0
  fi

  local slug
  slug="$(cuda_repo_slug)"
  local keyring_ok=0

  # Prefer NVIDIA network repo → package "cuda-toolkit" (toolkit only).
  # Do NOT install meta-package "cuda" — that pulls drivers and often conflicts
  # with ubuntu-drivers / already-installed proprietary drivers.
  if [[ -n "${slug}" ]]; then
    log "Trying NVIDIA CUDA network repo (${slug}) for cuda-toolkit..."
    local deb tmp
    deb="cuda-keyring_1.1-1_all.deb"
    tmp="$(mktemp -d)"
    if curl -fsSL \
      "https://developer.download.nvidia.com/compute/cuda/repos/${slug}/x86_64/${deb}" \
      -o "${tmp}/${deb}"; then
      if "${SUDO[@]}" dpkg -i "${tmp}/${deb}"; then
        "${SUDO[@]}" apt-get update -qq || true
        if "${SUDO[@]}" apt-get install "${APT_YES[@]}" cuda-toolkit; then
          keyring_ok=1
          ok "Installed cuda-toolkit from NVIDIA repo"
        else
          warn "cuda-toolkit from NVIDIA repo failed — will try distro packages"
        fi
      else
        warn "cuda-keyring install failed — will try distro packages"
      fi
    else
      warn "Could not download CUDA keyring for ${slug} — will try distro packages"
    fi
    rm -rf "${tmp}"
  fi

  export_cuda_env
  if [[ "${keyring_ok}" -eq 0 ]] && ! have_nvcc; then
    log "Falling back to distro packages: nvidia-cuda-toolkit + nvidia-cuda-dev"
    # Ubuntu/Debian multiverse/universe packages (older CUDA, but usable)
    if ! "${SUDO[@]}" apt-get install "${APT_YES[@]}" \
        nvidia-cuda-toolkit nvidia-cuda-dev; then
      warn "Distro CUDA packages failed."
      warn "Install toolkit from https://developer.nvidia.com/cuda-downloads"
      warn "Choose: Toolkit only (do not reinstall drivers if they already work)."
      return 0
    fi
  fi

  export_cuda_env
  if have_nvcc; then
    ok "nvcc: $(nvcc --version 2>/dev/null | tail -1)"
    # Persist PATH for login shells (non-destructive)
    if [[ -d /usr/local/cuda/bin ]]; then
      local profile_snip="/etc/profile.d/xnminer-cuda.sh"
      if [[ ! -f "${profile_snip}" ]]; then
        log "Writing ${profile_snip} so nvcc is on PATH for new shells"
        "${SUDO[@]}" tee "${profile_snip}" >/dev/null <<'EOF'
# Added by xnminer-linux install.sh — CUDA toolkit on PATH
if [ -d /usr/local/cuda/bin ]; then
  export PATH="/usr/local/cuda/bin${PATH:+:$PATH}"
fi
if [ -d /usr/local/cuda/lib64 ]; then
  export LD_LIBRARY_PATH="/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
EOF
      fi
    fi
  else
    warn "nvcc still not found after CUDA install"
  fi
}

# ---------------------------------------------------------------------------
# Phase 5: Python venv + requirements.txt  (after system Python is ready)
# ---------------------------------------------------------------------------
setup_python_venv() {
  log "Phase 5 — Python virtualenv + requirements.txt"
  cd "${MINER_ROOT}"
  [[ -f requirements.txt ]] || die "requirements.txt missing in ${MINER_ROOT}"

  # Always isolate miner deps — never pip install into system site-packages
  if [[ ! -d .venv ]]; then
    log "Creating .venv with python3..."
    python3 -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate

  # Use python -m pip inside the venv (correct module resolution)
  python -m pip install --upgrade pip wheel setuptools
  log "Installing project requirements into .venv..."
  python -m pip install -r requirements.txt

  # Import check — pynvml talks to the driver only when used, not at import time
  python - <<'PY'
import argon2
import psutil
import rich
import pynvml  # provided by nvidia-ml-py
print("imports ok: argon2, psutil, rich, pynvml")
PY
  ok "venv ready: ${MINER_ROOT}/.venv"
  ok "activate with:  source ${MINER_ROOT}/.venv/bin/activate"
}

# ---------------------------------------------------------------------------
# Phase 6: native CUDA engine build
# ---------------------------------------------------------------------------
build_engine() {
  log "Phase 6 — build libxen_cuda.so"
  cd "${MINER_ROOT}"

  if [[ "${BUILD_ENGINE}" -ne 1 ]]; then
    warn "Skipping native build (--no-build)"
    return 0
  fi

  export_cuda_env
  if ! have_nvcc; then
    warn "nvcc missing — cannot build engine yet"
    warn "After CUDA is installed:  cd ${MINER_ROOT} && ./native/build.sh"
    return 0
  fi

  # Compile does not require a live GPU, but warn if driver is pending reboot
  if [[ "${NEED_REBOOT}" -eq 1 ]]; then
    warn "Driver needs reboot for *mining*, but compile can still proceed with nvcc."
  fi

  chmod +x native/build.sh start-miner.sh install.sh 2>/dev/null || true

  if ./native/build.sh; then
    ok "CUDA engine: ${MINER_ROOT}/native/build/bin/libxen_cuda.so"
  else
    warn "Native build failed."
    warn "Common fixes:"
    warn "  • Older GPU arches:  CMAKE_CUDA_ARCHITECTURES=75;86 ./native/build.sh"
    warn "  • Ensure: cmake, g++, nvcc on PATH  (source /etc/profile.d/xnminer-cuda.sh)"
  fi
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print_summary() {
  export_cuda_env
  local py_s="missing" nv_s="missing" cmake_s="missing" so_s="missing" drv_s="not ready"

  if [[ -x "${MINER_ROOT}/.venv/bin/python" ]]; then
    py_s="$("${MINER_ROOT}/.venv/bin/python" --version 2>&1)"
  elif need_cmd python3; then
    py_s="$(python3 --version 2>&1) (no .venv)"
  fi
  if have_nvcc; then nv_s="$(nvcc --version 2>/dev/null | tail -1)"; fi
  if need_cmd cmake; then cmake_s="$(cmake --version | head -1)"; fi
  if [[ -f "${MINER_ROOT}/native/build/bin/libxen_cuda.so" ]]; then so_s="present"; fi
  if have_working_driver; then
    drv_s="ok ($(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | head -1))"
  elif [[ "${NEED_REBOOT}" -eq 1 ]]; then
    drv_s="installed — REBOOT required"
  fi

  cat <<EOF

────────────────────────────────────────────────────────────
  xnminer-linux setup summary
  Tree: ${MINER_ROOT}
────────────────────────────────────────────────────────────
  Order used:
    1. base apt tools
    2. Python + pip + venv tools + cmake + g++
    3. NVIDIA driver (if needed)
    4. CUDA toolkit only (nvcc)
    5. .venv + requirements.txt
    6. native engine build

  Python : ${py_s}
  cmake  : ${cmake_s}
  nvcc   : ${nv_s}
  driver : ${drv_s}
  engine : ${so_s}
────────────────────────────────────────────────────────────
EOF

  if [[ "${NEED_REBOOT}" -eq 1 ]]; then
    cat <<EOF
  NEXT:
    1) sudo reboot
    2) cd ${MINER_ROOT}
    3) source .venv/bin/activate
    4) ./install.sh --no-driver   # only if engine build was skipped
    5) ./start-miner.sh
EOF
  else
    cat <<EOF
  START MINING:
    cd ${MINER_ROOT}
    source .venv/bin/activate
    ./start-miner.sh
EOF
  fi

  if [[ "${CPU_ONLY}" -eq 1 ]]; then
    echo "  Note: --cpu-only → set backend = cpu in miner.ini"
  fi
  echo
}

# ---------------------------------------------------------------------------
main() {
  detect_os
  log "OS: ${OS_ID:-?} ${OS_VER:-}  package manager: ${PKG}"

  if [[ "${PKG}" != "apt" ]]; then
    warn "This installer is written for Ubuntu/Debian (apt)."
    warn "Install manually in this order: python3+pip → cmake → NVIDIA driver → CUDA toolkit → pip requirements → ./native/build.sh"
    ensure_repo
    if need_cmd python3; then
      setup_python_venv || true
    fi
    print_summary
    exit 0
  fi

  # 0) tree
  ensure_repo

  # 1) apt + base (no GPU)
  apt_update
  install_base_packages

  # 2) python / cmake / compilers FIRST
  install_python_and_build_tools

  # 3) driver
  install_nvidia_driver

  # 4) cuda toolkit only
  install_cuda_toolkit

  # 5) venv + requirements (after python system packages exist)
  setup_python_venv

  # 6) build engine last (needs cmake + nvcc)
  build_engine

  print_summary
}

main "$@"
