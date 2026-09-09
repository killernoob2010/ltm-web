"""Container resource admission checks for the shared Web + Agent service."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import time
from typing import Callable


@dataclass(frozen=True)
class ResourceSnapshot:
    memory_fraction: float | None
    cpu_fraction: float | None
    source: str

    @property
    def reliable(self) -> bool:
        return all(
            isinstance(value, (int, float)) and math.isfinite(float(value)) and 0 <= float(value)
            for value in (self.memory_fraction, self.cpu_fraction)
        )


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    reason: str
    snapshot: ResourceSnapshot


@dataclass(frozen=True)
class ResourceThresholds:
    pause_memory: float = 0.75
    stop_memory: float = 0.85
    resume_memory: float = 0.65
    pause_cpu: float = 0.80
    stop_cpu: float = 0.90
    resume_cpu: float = 0.70


class CgroupResourceProvider:
    """Read cgroup v2 memory and CPU usage without adding a runtime dependency."""

    def __init__(self, root: str | os.PathLike[str] = "/sys/fs/cgroup", *, clock: Callable[[], float] = time.monotonic):
        self.root = Path(root)
        self.clock = clock
        self._previous_usage = None
        self._previous_time = None

    def _read(self, name: str) -> str | None:
        try:
            return (self.root / name).read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return None

    def __call__(self) -> ResourceSnapshot:
        memory_current = self._read("memory.current")
        memory_max = self._read("memory.max")
        memory_fraction = None
        try:
            if memory_current is not None and memory_max not in (None, "max"):
                limit = int(memory_max)
                if limit > 0:
                    memory_fraction = int(memory_current) / limit
        except ValueError:
            memory_fraction = None

        cpu_fraction = None
        cpu_max = self._read("cpu.max")
        cpu_stat = self._read("cpu.stat")
        timestamp = self.clock()
        usage = None
        try:
            quota, period = cpu_max.split()[:2] if cpu_max else (None, None)
            usage = next(
                int(line.split()[1])
                for line in (cpu_stat or "").splitlines()
                if line.startswith("usage_usec ")
            )
            if quota != "max" and period and int(period) > 0 and self._previous_usage is not None:
                elapsed = timestamp - self._previous_time
                quota_cpus = int(quota) / int(period)
                if elapsed > 0 and quota_cpus > 0:
                    cpu_fraction = ((usage - self._previous_usage) / 1_000_000) / (elapsed * quota_cpus)
        except (AttributeError, StopIteration, TypeError, ValueError):
            cpu_fraction = None
        self._previous_usage = usage
        self._previous_time = timestamp
        return ResourceSnapshot(memory_fraction, cpu_fraction, "cgroup-v2")


class ResourceGuard:
    """Fail closed when usage cannot be measured and pause under pressure."""

    def __init__(self, provider: Callable[[], ResourceSnapshot], *,
                 thresholds: ResourceThresholds | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 stable_seconds: float = 60.0):
        self.provider = provider
        self.thresholds = thresholds or ResourceThresholds()
        self.clock = clock
        self.stable_seconds = max(0.0, float(stable_seconds))
        self._paused = False
        self._healthy_since = None

    def admit(self) -> AdmissionDecision:
        try:
            snapshot = self.provider()
        except Exception:
            snapshot = ResourceSnapshot(None, None, "error")
        if not snapshot.reliable:
            self._paused = True
            self._healthy_since = None
            return AdmissionDecision(False, "metrics_unavailable", snapshot)

        memory = float(snapshot.memory_fraction)
        cpu = float(snapshot.cpu_fraction)
        critical = memory >= self.thresholds.stop_memory or cpu >= self.thresholds.stop_cpu
        pressure = memory >= self.thresholds.pause_memory or cpu >= self.thresholds.pause_cpu
        if critical:
            self._paused = True
            self._healthy_since = None
            return AdmissionDecision(False, "resource_critical", snapshot)
        if pressure:
            self._paused = True
            self._healthy_since = None
            return AdmissionDecision(False, "resource_pressure", snapshot)
        if self._paused:
            healthy = memory <= self.thresholds.resume_memory and cpu <= self.thresholds.resume_cpu
            if not healthy:
                self._healthy_since = None
                return AdmissionDecision(False, "resource_recovering", snapshot)
            now = self.clock()
            if self._healthy_since is None:
                self._healthy_since = now
                return AdmissionDecision(False, "resource_recovering", snapshot)
            if now - self._healthy_since < self.stable_seconds:
                return AdmissionDecision(False, "resource_recovering", snapshot)
            self._paused = False
            self._healthy_since = None
        return AdmissionDecision(True, "ok", snapshot)


def default_resource_guard() -> ResourceGuard:
    return ResourceGuard(CgroupResourceProvider())
