#!/usr/bin/env bash
# XenBlocks Miner by Tony.x1 — Linux launcher
# Usage:  ./start-miner.sh
set -euo pipefail

MINER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${MINER_ROOT}"

# Prefer install.sh venv and CUDA toolkit paths
if [[ -f /etc/profile.d/xnminer-cuda.sh ]]; then
  # shellcheck disable=SC1091
  source /etc/profile.d/xnminer-cuda.sh
fi
if [[ -d /usr/local/cuda/bin ]]; then
  export PATH="/usr/local/cuda/bin${PATH:+:$PATH}"
fi
if [[ -d /usr/local/cuda/lib64 ]]; then
  export LD_LIBRARY_PATH="/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

PYTHON=(python3)
if [[ -x "${MINER_ROOT}/.venv/bin/python" ]]; then
  # shellcheck disable=SC1091
  source "${MINER_ROOT}/.venv/bin/activate"
  PYTHON=("${MINER_ROOT}/.venv/bin/python")
fi

read_ini() {
  local section="$1" key="$2" path="$3"
  local in_section=0
  [[ -f "${path}" ]] || return 0
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%%$'\r'}"
    local trimmed
    trimmed="$(echo "${line}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [[ "${trimmed}" =~ ^\[(.+)\]$ ]]; then
      if [[ "${BASH_REMATCH[1]}" == "${section}" ]]; then
        in_section=1
      else
        in_section=0
      fi
      continue
    fi
    if [[ ${in_section} -eq 1 && "${trimmed}" =~ ^${key}[[:space:]]*=[[:space:]]*(.*)$ ]]; then
      echo "${BASH_REMATCH[1]}"
      return 0
    fi
  done < "${path}"
}

CONFIG_PATH="${MINER_ROOT}/miner.ini"
EXAMPLE_PATH="${MINER_ROOT}/miner.ini.example"

if [[ ! -f "${CONFIG_PATH}" ]]; then
  if [[ -f "${EXAMPLE_PATH}" ]]; then
    cp "${EXAMPLE_PATH}" "${CONFIG_PATH}"
    echo "Created miner.ini from miner.ini.example"
  else
    echo "miner.ini missing — Python will create a default on first run."
  fi
fi

WALLET="$(read_ini account address "${CONFIG_PATH}" || true)"
BACKEND="$(read_ini mining backend "${CONFIG_PATH}" || true)"
BACKEND="${BACKEND:-cuda}"
CUDA_LIB="$(read_ini cuda dll_path "${CONFIG_PATH}" || true)"
CUDA_LIB="${CUDA_LIB:-native/build/bin/libxen_cuda.so}"

if ! command -v "${PYTHON[0]}" >/dev/null 2>&1 && [[ ! -x "${PYTHON[0]}" ]]; then
  echo "ERROR: python3 not found. Run ./install.sh or install Python 3.10+." >&2
  exit 1
fi

PY_VERSION="$("${PYTHON[@]}" --version 2>&1)"

# Install deps into venv if missing (never silent-fail on system pip)
if [[ -f "${MINER_ROOT}/requirements.txt" ]]; then
  if ! "${PYTHON[@]}" -c "import argon2, pynvml, psutil, rich" 2>/dev/null; then
    echo "Installing Python packages into environment..."
    if [[ ! -d "${MINER_ROOT}/.venv" ]]; then
      python3 -m venv "${MINER_ROOT}/.venv"
      # shellcheck disable=SC1091
      source "${MINER_ROOT}/.venv/bin/activate"
      PYTHON=("${MINER_ROOT}/.venv/bin/python")
    fi
    "${PYTHON[@]}" -m pip install --upgrade pip
    "${PYTHON[@]}" -m pip install -r "${MINER_ROOT}/requirements.txt"
  fi
fi

LIB_FULL="${MINER_ROOT}/${CUDA_LIB}"
if [[ "${BACKEND}" == "cuda" && ! -f "${LIB_FULL}" ]]; then
  echo "WARNING: CUDA engine not found: ${LIB_FULL}"
  echo "  Build with:  ./native/build.sh"
  echo "  Or set backend = cpu in miner.ini"
fi

# Stop another instance of THIS install only
MAIN_PY="${MINER_ROOT}/main.py"
if command -v pgrep >/dev/null 2>&1; then
  while read -r pid; do
    [[ -z "${pid}" ]] && continue
    [[ "${pid}" == "$$" ]] && continue
    echo "Stopping existing miner process: ${pid}"
    kill -TERM "${pid}" 2>/dev/null || true
  done < <(pgrep -f "${MAIN_PY}" 2>/dev/null || true)
  sleep 1
fi
rm -f "${MINER_ROOT}/data/miner.lock"

if [[ -z "${WALLET}" || "${WALLET}" == "0x" ]]; then
  echo "XenBlocks Miner by Tony.x1  —  first-run setup  —  ${PY_VERSION}"
  echo "You will be asked for your EVM wallet (0x...). It is saved to miner.ini."
else
  if [[ ${#WALLET} -gt 18 ]]; then
    WALLET_SHORT="${WALLET:0:10}...${WALLET: -6}"
  else
    WALLET_SHORT="${WALLET}"
  fi
  echo "XenBlocks Miner by Tony.x1  —  ${WALLET_SHORT}  —  ${BACKEND}  —  ${PY_VERSION}"
fi
echo "Starting... (Ctrl+C to stop)  log: data/session.log"
echo

set +e
"${PYTHON[@]}" "${MAIN_PY}"
EXIT_CODE=$?
set -e

if [[ ${EXIT_CODE} -ne 0 ]]; then
  echo "Miner stopped (exit code ${EXIT_CODE}). Check data/session.log"
else
  echo "Miner stopped."
fi
exit "${EXIT_CODE}"
