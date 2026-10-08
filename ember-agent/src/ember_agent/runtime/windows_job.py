"""Windows job ownership; retained native handles prevent PID reuse and orphaned descendants."""

import ctypes
from collections.abc import Callable
from ctypes import wintypes
from typing import cast

from .config import ConfigurationError


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("per_process_time", ctypes.c_int64),
        ("per_job_time", ctypes.c_int64),
        ("limit_flags", wintypes.DWORD),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "read_operations",
            "write_operations",
            "other_operations",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class WindowsJob:
    """Kill only assigned children on handle close, including when the launcher crashes."""

    def __init__(self, process_handle: int) -> None:
        # ctypes' Win32 exports are an untyped native ABI; pin signatures at this boundary.
        library = ctypes.WinDLL("kernel32", use_last_error=True)
        create = library.CreateJobObjectW
        create.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        create.restype = wintypes.HANDLE
        configure = library.SetInformationJobObject
        configure.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        configure.restype = wintypes.BOOL
        assign = library.AssignProcessToJobObject
        assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        assign.restype = wintypes.BOOL
        close = library.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        self._close = cast(Callable[[int], int], close)
        handle: object = create(None, None)
        if not isinstance(handle, int) or handle == 0:
            raise ConfigurationError("Cannot acquire Windows service ownership.")
        self.handle: int | None = handle
        limits = _ExtendedLimits()
        limits.basic.limit_flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not configure(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not assign(
            handle, process_handle
        ):
            self.close()
            raise ConfigurationError("Cannot assign owned child to the Windows job.")

    def close(self) -> None:
        """Close the retained job capability; no saved PID is used."""
        if self.handle is not None:
            self._close(self.handle)
            self.handle = None
