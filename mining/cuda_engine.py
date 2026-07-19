"""ctypes bindings for native xen_cuda shared library (libxen_cuda.so on Linux)."""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if sys.platform == "win32":
    _DEFAULT_LIB_NAME = "xen_cuda.dll"
else:
    _DEFAULT_LIB_NAME = "libxen_cuda.so"

DEFAULT_DLL = ROOT / "native" / "build" / "bin" / _DEFAULT_LIB_NAME

XEN_CUDA_MAX_MATCHES = 32
XEN_CUDA_KEY_LEN = 65
XEN_CUDA_HASH_LEN = 256
XEN_CUDA_ERR_LEN = 256


class XenCudaMatch(ctypes.Structure):
    _fields_ = [
        ("key", ctypes.c_char * XEN_CUDA_KEY_LEN),
        ("hash", ctypes.c_char * XEN_CUDA_HASH_LEN),
        ("pattern", ctypes.c_char * 32),
        ("attempt_index", ctypes.c_uint64),
    ]


class XenCudaBatchResult(ctypes.Structure):
    _fields_ = [
        ("ok", ctypes.c_int),
        ("error", ctypes.c_char * XEN_CUDA_ERR_LEN),
        ("attempts", ctypes.c_uint64),
        ("hashrate", ctypes.c_double),
        ("elapsed_ms", ctypes.c_double),
        ("batch_size", ctypes.c_uint32),
        ("match_count", ctypes.c_uint32),
        ("matches", XenCudaMatch * XEN_CUDA_MAX_MATCHES),
    ]


class XenCudaDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("device_id", ctypes.c_int),
        ("name", ctypes.c_char * 128),
        ("total_vram_bytes", ctypes.c_uint64),
        ("free_vram_bytes", ctypes.c_uint64),
    ]


@dataclass
class CudaMatch:
    key: str
    hash_str: str
    pattern: str
    attempt_index: int


@dataclass
class CudaBatchResult:
    ok: bool
    error: str
    attempts: int
    hashrate: float
    elapsed_ms: float
    batch_size: int
    matches: list[CudaMatch]


class CudaEngine:
    def __init__(self, dll_path: Path | None = None) -> None:
        path = dll_path or DEFAULT_DLL
        if not path.exists():
            build_hint = "native/build.sh" if sys.platform != "win32" else "native/build.ps1"
            raise FileNotFoundError(
                f"CUDA engine library not found at {path}. Run {build_hint} first."
            )
        self._lib = ctypes.CDLL(str(path))
        self._parallel_lanes = False
        self._bind()

    def _bind(self) -> None:
        lib = self._lib
        lib.xen_cuda_init.argtypes = [ctypes.c_int, ctypes.c_uint64]
        lib.xen_cuda_init.restype = ctypes.c_int
        lib.xen_cuda_shutdown.argtypes = []
        lib.xen_cuda_shutdown.restype = None
        if hasattr(lib, "xen_cuda_set_lane_count"):
            lib.xen_cuda_set_lane_count.argtypes = [ctypes.c_int]
            lib.xen_cuda_set_lane_count.restype = ctypes.c_int
            self._parallel_lanes = True
        lib.xen_cuda_device_info.argtypes = [ctypes.c_int, ctypes.POINTER(XenCudaDeviceInfo)]
        lib.xen_cuda_device_info.restype = ctypes.c_int
        lib.xen_cuda_select_batch_size.argtypes = [
            ctypes.c_uint64,
            ctypes.c_uint32,
            ctypes.c_uint64,
        ]
        lib.xen_cuda_select_batch_size.restype = ctypes.c_uint64
        if self._parallel_lanes:
            lib.xen_cuda_run_lane_batch.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_uint32,
                ctypes.c_uint64,
                ctypes.c_int,
                ctypes.POINTER(XenCudaBatchResult),
            ]
            lib.xen_cuda_run_lane_batch.restype = ctypes.c_int
        lib.xen_cuda_run_batch.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_uint64,
            ctypes.c_int,
            ctypes.POINTER(XenCudaBatchResult),
        ]
        lib.xen_cuda_run_batch.restype = ctypes.c_int
        lib.xen_cuda_verify_known.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        lib.xen_cuda_verify_known.restype = ctypes.c_int

    def init(self, device_id: int = 0, reserve_bytes: int = 100 * 1024 * 1024) -> None:
        rc = self._lib.xen_cuda_init(device_id, reserve_bytes)
        if rc != 0:
            raise RuntimeError(f"xen_cuda_init failed ({rc})")

    def shutdown(self) -> None:
        self._lib.xen_cuda_shutdown()

    @property
    def parallel_lanes_supported(self) -> bool:
        return self._parallel_lanes

    def set_lane_count(self, lane_count: int) -> None:
        if not self._parallel_lanes:
            return
        rc = self._lib.xen_cuda_set_lane_count(int(lane_count))
        if rc != 0:
            raise RuntimeError(f"xen_cuda_set_lane_count failed ({rc})")

    def device_info(self, device_id: int = 0) -> XenCudaDeviceInfo:
        info = XenCudaDeviceInfo()
        rc = self._lib.xen_cuda_device_info(device_id, ctypes.byref(info))
        if rc != 0:
            raise RuntimeError(f"xen_cuda_device_info failed ({rc})")
        return info

    def select_batch_size(
        self,
        free_vram_bytes: int,
        difficulty: int,
        max_batch: int = 0,
    ) -> int:
        return int(
            self._lib.xen_cuda_select_batch_size(
                ctypes.c_uint64(free_vram_bytes),
                ctypes.c_uint32(difficulty),
                ctypes.c_uint64(max_batch),
            )
        )

    def run_lane_batch(
        self,
        lane_index: int,
        salt_hex: str,
        difficulty: int,
        batch_size: int,
        key_prefix: str = "",
        allow_xuni: bool = True,
    ) -> CudaBatchResult:
        out = XenCudaBatchResult()
        if self._parallel_lanes:
            rc = self._lib.xen_cuda_run_lane_batch(
                ctypes.c_int(lane_index),
                salt_hex.encode("ascii"),
                key_prefix.encode("ascii") if key_prefix else None,
                ctypes.c_uint32(difficulty),
                ctypes.c_uint64(batch_size),
                1 if allow_xuni else 0,
                ctypes.byref(out),
            )
        else:
            rc = self._lib.xen_cuda_run_batch(
                salt_hex.encode("ascii"),
                key_prefix.encode("ascii") if key_prefix else None,
                ctypes.c_uint32(difficulty),
                ctypes.c_uint64(batch_size),
                1 if allow_xuni else 0,
                ctypes.byref(out),
            )
        matches: list[CudaMatch] = []
        for i in range(out.match_count):
            m = out.matches[i]
            matches.append(
                CudaMatch(
                    key=m.key.decode("ascii", errors="replace"),
                    hash_str=m.hash.decode("ascii", errors="replace"),
                    pattern=m.pattern.decode("ascii", errors="replace"),
                    attempt_index=int(m.attempt_index),
                )
            )
        result = CudaBatchResult(
            ok=bool(out.ok),
            error=out.error.decode("ascii", errors="replace"),
            attempts=int(out.attempts),
            hashrate=float(out.hashrate),
            elapsed_ms=float(out.elapsed_ms),
            batch_size=int(out.batch_size),
            matches=matches,
        )
        if rc != 0 and not result.error:
            result.error = f"xen_cuda_run_lane_batch rc={rc}"
        return result

    def run_batch(
        self,
        salt_hex: str,
        difficulty: int,
        batch_size: int,
        key_prefix: str = "",
        allow_xuni: bool = True,
    ) -> CudaBatchResult:
        return self.run_lane_batch(
            0,
            salt_hex,
            difficulty,
            batch_size,
            key_prefix=key_prefix,
            allow_xuni=allow_xuni,
        )

    def verify_known(self, salt_hex: str, key_hex: str, difficulty: int) -> str:
        buf = ctypes.create_string_buffer(XEN_CUDA_HASH_LEN)
        rc = self._lib.xen_cuda_verify_known(
            salt_hex.encode("ascii"),
            key_hex.encode("ascii"),
            ctypes.c_uint32(difficulty),
            buf,
            XEN_CUDA_HASH_LEN,
        )
        if rc != 0:
            raise RuntimeError(f"xen_cuda_verify_known failed ({rc})")
        return buf.value.decode("ascii", errors="replace")
