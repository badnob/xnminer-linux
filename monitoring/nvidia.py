"""NVIDIA driver access via NVML (pynvml / nvidia-ml-py)."""

from __future__ import annotations

from monitoring.logger import SessionLogger
from core.models import GpuSnapshot

try:
    import pynvml
except ImportError:
    pynvml = None  # type: ignore


class NvidiaMonitor:
    def __init__(self, device_index: int = 0, logger: SessionLogger | None = None) -> None:
        self.device_index = device_index
        self.logger = logger
        self._ready = False
        if pynvml is None:
            if logger:
                logger.warn("pynvml not installed — GPU monitoring disabled")
            return
        try:
            pynvml.nvmlInit()
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
            self._ready = True
            name = pynvml.nvmlDeviceGetName(self._handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            if logger:
                logger.info(f"NVML ready: GPU{device_index} {name}")
        except Exception as exc:
            if logger:
                logger.warn(f"NVML init failed: {exc}")

    @property
    def available(self) -> bool:
        return self._ready

    def snapshot(self) -> GpuSnapshot | None:
        if not self._ready or pynvml is None:
            return None
        try:
            mem = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(self._handle)
            power_mw = pynvml.nvmlDeviceGetPowerUsage(self._handle)
            temp = pynvml.nvmlDeviceGetTemperature(
                self._handle, pynvml.NVML_TEMPERATURE_GPU
            )
            name = pynvml.nvmlDeviceGetName(self._handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            return GpuSnapshot(
                index=self.device_index,
                name=name,
                total_mib=mem.total // (1024 * 1024),
                used_mib=mem.used // (1024 * 1024),
                free_mib=mem.free // (1024 * 1024),
                util_pct=int(util.gpu),
                power_w=power_mw / 1000.0,
                temperature_c=int(temp),
            )
        except Exception as exc:
            if self.logger:
                self.logger.warn(f"NVML snapshot failed: {exc}")
            return None

    def within_budget(self, target_used_mib: int) -> bool:
        snap = self.snapshot()
        return snap is not None and snap.used_mib <= target_used_mib

    def get_power_limits_mw(self) -> tuple[int, int, int] | None:
        """Return (current_mw, min_mw, max_mw) or None if unavailable."""
        if not self._ready or pynvml is None:
            return None
        try:
            current = int(pynvml.nvmlDeviceGetPowerManagementLimit(self._handle))
            min_mw, max_mw = pynvml.nvmlDeviceGetPowerManagementLimitConstraints(
                self._handle
            )
            return current, int(min_mw), int(max_mw)
        except Exception as exc:
            if self.logger:
                self.logger.warn(f"NVML power limits read failed: {exc}")
            return None

    def set_power_limit_mw(self, limit_mw: int) -> bool:
        if not self._ready or pynvml is None:
            return False
        try:
            pynvml.nvmlDeviceSetPowerManagementLimit(self._handle, int(limit_mw))
            return True
        except Exception as exc:
            if self.logger:
                self.logger.warn(f"NVML set power limit failed: {exc}")
            return False

    def shutdown(self) -> None:
        if self._ready and pynvml is not None:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
            self._ready = False