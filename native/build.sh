#!/usr/bin/env bash
# Build the native CUDA engine shared library for Linux.
# Output: native/build/bin/libxen_cuda.so
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_DIR="${ROOT}/engine"
BUILD_DIR="${ROOT}/build"

if ! command -v cmake >/dev/null 2>&1; then
  echo "ERROR: cmake not found. Install cmake (3.18+) and retry." >&2
  exit 1
fi

if ! command -v nvcc >/dev/null 2>&1; then
  echo "WARNING: nvcc not on PATH. Ensure CUDA Toolkit is installed." >&2
  echo "  e.g. export PATH=/usr/local/cuda/bin:\$PATH" >&2
fi

# Default arches: Ada/Hopper-class + Blackwell (sm_90, sm_120).
# Override:  CMAKE_CUDA_ARCHITECTURES=89 ./native/build.sh
ARCHS="${CMAKE_CUDA_ARCHITECTURES:-90;120}"

mkdir -p "${BUILD_DIR}"

GENERATOR=()
if command -v ninja >/dev/null 2>&1; then
  GENERATOR=(-G Ninja)
fi

echo "Configuring xen_cuda (CUDA arches: ${ARCHS})..."
cmake -S "${ENGINE_DIR}" -B "${BUILD_DIR}" \
  "${GENERATOR[@]}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES="${ARCHS}"

echo "Building..."
cmake --build "${BUILD_DIR}" --config Release -j"$(nproc 2>/dev/null || echo 4)"

SO="${BUILD_DIR}/bin/libxen_cuda.so"
if [[ ! -f "${SO}" ]]; then
  # Some generators place the .so next to other libs
  FOUND="$(find "${BUILD_DIR}" -name 'libxen_cuda.so' -type f | head -n 1 || true)"
  if [[ -n "${FOUND}" ]]; then
    mkdir -p "${BUILD_DIR}/bin"
    cp -f "${FOUND}" "${SO}"
  fi
fi

if [[ -f "${SO}" ]]; then
  echo "OK: ${SO}"
  ls -lh "${SO}"
else
  echo "ERROR: libxen_cuda.so not found under ${BUILD_DIR}" >&2
  exit 1
fi
