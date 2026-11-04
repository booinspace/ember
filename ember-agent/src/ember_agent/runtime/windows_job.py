"""Windows job ownership; retained native handles prevent PID reuse and orphaned descendants."""

import ctypes
from collections.abc import Callable
from ctypes import wintypes
from time import monotonic, sleep
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


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("user", ctypes.c_int64),
        ("kernel", ctypes.c_int64),
        ("period_user", ctypes.c_int64),
        ("period_kernel", ctypes.c_int64),
        ("page_faults", wintypes.DWORD),
        ("total_processes", wintypes.DWORD),
        ("active_processes", wintypes.DWORD),
        ("terminated_processes", wintypes.DWORD),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("thread_id", wintypes.DWORD),
        ("process_id", wintypes.DWORD),
        ("base_priority", wintypes.LONG),
        ("delta_priority", wintypes.LONG),
        ("flags", wintypes.DWORD),
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

    def terminate_and_wait(self, timeout: float = 3.0) -> None:
        """Drain the retained job's children before reporting an owned shutdown complete."""
        if self.handle is None:
            return
        library = ctypes.WinDLL("kernel32", use_last_error=True)
        terminate = library.TerminateJobObject
        terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
        terminate.restype = wintypes.BOOL
        query = library.QueryInformationJobObject
        query.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        query.restype = wintypes.BOOL
        open_process = library.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        belongs = library.IsProcessInJob
        belongs.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        belongs.restype = wintypes.BOOL
        wait = library.WaitForSingleObject
        wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        wait.restype = wintypes.DWORD
        handles: list[int] = []
        deadline = monotonic() + timeout
        try:
            # Job accounting can reach zero before descendant teardown has signalled.
            members = ctypes.create_string_buffer(8 + 4096 * ctypes.sizeof(ctypes.c_size_t))
            if not query(self.handle, 3, members, ctypes.sizeof(members), None):
                raise ConfigurationError("Cannot retain owned Windows job members.")
            count = ctypes.c_uint32.from_buffer(members, 4).value
            if count > 4096:
                raise ConfigurationError("Owned Windows job member limit exceeded.")
            identities = (ctypes.c_size_t * count).from_buffer(members, 8)
            for identity in identities:
                handle: object = open_process(0x101000, False, identity)
                if not isinstance(handle, int) or not handle:
                    if ctypes.get_last_error() != 87:  # ERROR_INVALID_PARAMETER: member exited.
                        raise ConfigurationError("Cannot retain owned Windows child.")
                    continue
                owned = wintypes.BOOL()
                if not belongs(handle, self.handle, ctypes.byref(owned)):
                    self._close(handle)
                    raise ConfigurationError("Cannot verify owned Windows child.")
                if not owned.value:
                    self._close(handle)
                    continue
                handles.append(handle)
            if not terminate(self.handle, 1):
                raise ConfigurationError("Cannot stop owned Windows job.")
            for handle in handles:
                remaining = max(0, int((deadline - monotonic()) * 1000))
                if wait(handle, remaining) != 0:
                    raise ConfigurationError("Owned Windows child shutdown timed out.")
            while True:
                accounting = _Accounting()
                if not query(
                    self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
                ):
                    raise ConfigurationError("Cannot verify owned Windows job shutdown.")
                if accounting.active_processes == 0:
                    return
                if monotonic() >= deadline:
                    raise ConfigurationError("Owned Windows job shutdown timed out.")
                sleep(0.01)
        finally:
            terminate(self.handle, 1)
            for handle in handles:
                self._close(handle)
            self.close()

    def resume_suspended(self, process_handle: int) -> None:
        """Resume a newly suspended owned child only after job assignment, before it can fork."""
        library = ctypes.WinDLL("kernel32", use_last_error=True)
        process_id = library.GetProcessId
        process_id.argtypes = [wintypes.HANDLE]
        process_id.restype = wintypes.DWORD
        snapshot = library.CreateToolhelp32Snapshot
        snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        snapshot.restype = wintypes.HANDLE
        first, next_thread = library.Thread32First, library.Thread32Next
        for function in (first, next_thread):
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
            function.restype = wintypes.BOOL
        open_thread = library.OpenThread
        open_thread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_thread.restype = wintypes.HANDLE
        thread_process_id = library.GetProcessIdOfThread
        thread_process_id.argtypes = [wintypes.HANDLE]
        thread_process_id.restype = wintypes.DWORD
        resume = library.ResumeThread
        resume.argtypes = [wintypes.HANDLE]
        resume.restype = wintypes.DWORD
        identity: int = process_id(process_handle)
        if self.handle is None or not identity:
            raise ConfigurationError("Cannot resume an unowned child.")
        threads: object = snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if not isinstance(threads, int) or threads in {0, ctypes.c_void_p(-1).value}:
            raise ConfigurationError("Cannot inspect owned child threads.")
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            more = first(threads, ctypes.byref(entry))
            while more:
                if entry.process_id == identity:
                    handle: object = open_thread(0x802, False, entry.thread_id)
                    # Retain and verify the thread before resuming; snapshot IDs can be reused.
                    if not isinstance(handle, int) or not handle:
                        raise ConfigurationError("Cannot retain owned child thread.")
                    try:
                        if thread_process_id(handle) != identity:
                            raise ConfigurationError("Owned child thread identity changed.")
                        if resume(handle) != 1:
                            raise ConfigurationError("Owned child was not initially suspended.")
                    finally:
                        self._close(handle)
                    return
                more = next_thread(threads, ctypes.byref(entry))
            raise ConfigurationError("Owned child thread was unavailable.")
        finally:
            self._close(threads)
