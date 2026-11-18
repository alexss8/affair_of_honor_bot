"""Метрики процесса и файлов для админ-команды /status. Без сторонних зависимостей."""

import ctypes
import os
import sys
import time
from dataclasses import dataclass

from bot.logger import BACKUP_COUNT, is_error_line

_LAST_ERROR_MAX_CHARS = 180


@dataclass(frozen=True)
class LogErrorsSummary:
    count: int
    last_error: str | None  # сырая строка лога (не экранирована)


def get_uptime(started_at: float, now: float | None = None) -> tuple[int, int, int]:
    """Возвращает время работы бота с момента started_at как (дни, часы, минуты)."""
    now = time.time() if now is None else now
    uptime_sec = int(now - started_at)
    days, rem = divmod(uptime_sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    return days, hours, minutes


def get_process_ram_mb() -> float:
    """Текущее потребление физической памяти (RSS) процессом в МБ."""
    try:
        if os.path.exists("/proc/self/status"):
            with open("/proc/self/status", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        return round(kb / 1024, 1)
        if sys.platform == "win32":
            return _get_windows_ram_mb()
    except Exception:
        pass
    return 0.0


def _get_windows_ram_mb() -> float:
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(ProcessMemoryCounters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return round(counters.WorkingSetSize / (1024 * 1024), 1)
    return 0.0


def get_disk_usage_kb(db_path: str, log_file: str) -> float:
    """Суммарный размер файлов БД (включая WAL/SHM) и логов (включая ротированные) в КБ."""
    candidates = [db_path, f"{db_path}-wal", f"{db_path}-shm", log_file]
    candidates += [f"{log_file}.{i}" for i in range(1, BACKUP_COUNT + 1)]
    total_bytes = 0
    for path in candidates:
        if path and os.path.exists(path):
            try:
                total_bytes += os.path.getsize(path)
            except OSError:
                pass
    return round(total_bytes / 1024, 1)


def summarize_log_errors(log_file: str) -> LogErrorsSummary:
    """Считает строки уровня ERROR в текущем файле лога и возвращает последнюю из них."""
    count = 0
    last_error = None
    if os.path.exists(log_file):
        with open(log_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if is_error_line(line):
                    count += 1
                    last_error = line.strip()[-_LAST_ERROR_MAX_CHARS:]
    return LogErrorsSummary(count=count, last_error=last_error)
