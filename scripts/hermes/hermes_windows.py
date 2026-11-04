"""Native per-task AppContainer ownership for the optional, socket-free Hermes worker."""

import contextlib
import csv
import ctypes
import importlib
import io
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes as w
from pathlib import Path
from typing import BinaryIO, Protocol, cast
from uuid import uuid4

from hermes_setup import HermesProfile
from pydantic import BaseModel, ConfigDict, Field


class _Job(Protocol):
    def terminate_and_wait(self, timeout: float = 3.0) -> None: ...


class Reaping:
    """A failed first retained-job wait can never become success through a closed handle."""

    def __init__(self, job: _Job) -> None:
        self.job = job
        self.attempted = False
        self.confirmed = False

    def wait(self) -> bool:
        """Attempt the owned wait once and preserve its actual result across cleanup paths."""
        if not self.attempted:
            self.attempted = True
            self.job.terminate_and_wait(timeout=2)
            self.confirmed = True
        return self.confirmed


class _Security(ctypes.Structure):
    _fields_ = [("length", w.DWORD), ("descriptor", w.LPVOID), ("inherit", w.BOOL)]


class _Startup(ctypes.Structure):
    _fields_ = [
        ("size", w.DWORD),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
        ("x", w.DWORD),
        ("y", w.DWORD),
        ("width", w.DWORD),
        ("height", w.DWORD),
        ("columns", w.DWORD),
        ("rows", w.DWORD),
        ("fill", w.DWORD),
        ("flags", w.DWORD),
        ("show", w.WORD),
        ("reserved_size", w.WORD),
        ("reserved_data", ctypes.POINTER(w.BYTE)),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class _StartupEx(ctypes.Structure):
    _fields_ = [("startup", _Startup), ("attributes", w.LPVOID)]


class _Process(ctypes.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


class _Capabilities(ctypes.Structure):
    _fields_ = [
        ("sid", w.LPVOID),
        ("capabilities", w.LPVOID),
        ("count", w.DWORD),
        ("reserved", w.DWORD),
    ]


class _NativeFunction(Protocol):
    """Typed edge around ctypes' dynamically exported native function pointers."""

    argtypes: list[object]
    restype: object

    def __call__(self, *arguments: object) -> int | None: ...


class _NativeLibrary(Protocol):
    def __getattr__(self, name: str) -> _NativeFunction: ...


def _handle(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not value:
        raise OSError("Cannot acquire owned Windows handle.")
    return value


class WindowsAPI:
    """Pin the untyped Windows ABI once; no PID lookup confers process ownership."""

    kernel: _NativeLibrary
    userenv: _NativeLibrary
    security: _NativeLibrary
    ui: _NativeLibrary

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("This boundary requires native Windows.")
        factory = cast(Callable[..., _NativeLibrary], ctypes.WinDLL)
        self.kernel = factory("kernel32", use_last_error=True)
        self.userenv = factory("userenv", use_last_error=True)
        self.security = factory("advapi32", use_last_error=True)
        self.ui = factory("user32", use_last_error=True)
        k, u, a, y = self.kernel, self.userenv, self.security, self.ui
        u.CreateAppContainerProfile.argtypes = [
            w.LPCWSTR,
            w.LPCWSTR,
            w.LPCWSTR,
            w.LPVOID,
            w.DWORD,
            ctypes.POINTER(w.LPVOID),
        ]
        u.CreateAppContainerProfile.restype = ctypes.c_long
        u.DeleteAppContainerProfile.argtypes = [w.LPCWSTR]
        u.DeleteAppContainerProfile.restype = ctypes.c_long
        u.DeriveAppContainerSidFromAppContainerName.argtypes = [
            w.LPCWSTR,
            ctypes.POINTER(w.LPVOID),
        ]
        u.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
        a.ConvertSidToStringSidW.argtypes = [w.LPVOID, ctypes.POINTER(w.LPWSTR)]
        a.ConvertSidToStringSidW.restype = w.BOOL
        a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            w.LPCWSTR,
            w.DWORD,
            ctypes.POINTER(w.LPVOID),
            w.LPVOID,
        ]
        a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
        a.FreeSid.argtypes = [w.LPVOID]
        a.FreeSid.restype = w.LPVOID
        a.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        a.OpenProcessToken.restype = w.BOOL
        a.GetTokenInformation.argtypes = [
            w.HANDLE,
            ctypes.c_int,
            w.LPVOID,
            w.DWORD,
            ctypes.POINTER(w.DWORD),
        ]
        a.GetTokenInformation.restype = w.BOOL
        k.GetCurrentProcess.restype = w.HANDLE
        k.LocalFree.argtypes = [w.LPVOID]
        k.LocalFree.restype = w.LPVOID
        k.CloseHandle.argtypes = [w.HANDLE]
        k.CloseHandle.restype = w.BOOL
        k.InitializeProcThreadAttributeList.argtypes = [
            w.LPVOID,
            w.DWORD,
            w.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        k.InitializeProcThreadAttributeList.restype = w.BOOL
        k.UpdateProcThreadAttribute.argtypes = [
            w.LPVOID,
            w.DWORD,
            ctypes.c_size_t,
            w.LPVOID,
            ctypes.c_size_t,
            w.LPVOID,
            w.LPVOID,
        ]
        k.UpdateProcThreadAttribute.restype = w.BOOL
        k.DeleteProcThreadAttributeList.argtypes = [w.LPVOID]
        k.CreateProcessW.argtypes = [
            w.LPCWSTR,
            w.LPWSTR,
            w.LPVOID,
            w.LPVOID,
            w.BOOL,
            w.DWORD,
            w.LPVOID,
            w.LPCWSTR,
            ctypes.POINTER(_StartupEx),
            ctypes.POINTER(_Process),
        ]
        k.CreateProcessW.restype = w.BOOL
        k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        k.WaitForSingleObject.restype = w.DWORD
        k.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        k.GetExitCodeProcess.restype = w.BOOL
        k.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        k.TerminateProcess.restype = w.BOOL
        k.ResumeThread.argtypes = [w.HANDLE]
        k.ResumeThread.restype = w.DWORD
        y.CreateWindowStationW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(_Security)]
        y.CreateWindowStationW.restype = w.HANDLE
        y.GetProcessWindowStation.restype = w.HANDLE
        y.SetProcessWindowStation.argtypes = [w.HANDLE]
        y.SetProcessWindowStation.restype = w.BOOL
        y.CreateDesktopW.argtypes = [
            w.LPCWSTR,
            w.LPCWSTR,
            w.LPVOID,
            w.DWORD,
            w.DWORD,
            ctypes.POINTER(_Security),
        ]
        y.CreateDesktopW.restype = w.HANDLE
        y.CloseDesktop.argtypes = [w.HANDLE]
        y.CloseDesktop.restype = w.BOOL
        y.CloseWindowStation.argtypes = [w.HANDLE]
        y.CloseWindowStation.restype = w.BOOL

    def require(self, success: object) -> None:
        """Reject an unsuccessful native operation before publishing an ownership claim."""
        if not success:
            if sys.platform != "win32":
                raise OSError("Native Windows operation failed.")
            last_error = cast(Callable[[], int], vars(ctypes)["get_last_error"])
            exception = cast(Callable[[int], OSError], ctypes.WinError)
            raise exception(last_error())


def verify_appcontainer() -> None:
    """Require the actual kernel token and zero capabilities before receiving a task."""
    api = WindowsAPI()
    token = w.HANDLE()
    api.require(
        api.security.OpenProcessToken(api.kernel.GetCurrentProcess(), 8, ctypes.byref(token))
    )
    try:
        flag, size = w.DWORD(), w.DWORD()
        api.require(
            api.security.GetTokenInformation(token, 29, ctypes.byref(flag), 4, ctypes.byref(size))
        )
        if flag.value != 1:
            raise ValueError("Hermes must run inside its AppContainer.")
        api.security.GetTokenInformation(token, 30, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        api.require(
            api.security.GetTokenInformation(token, 30, buffer, size.value, ctypes.byref(size))
        )
        if ctypes.c_uint32.from_buffer(buffer).value != 0:
            raise ValueError("Unexpected AppContainer capabilities.")
    finally:
        api.kernel.CloseHandle(token)


def _acl(arguments: list[str]) -> None:
    subprocess.run(
        [str(Path(os.environ["SystemRoot"]) / "System32/icacls.exe"), *arguments],
        check=True,
        capture_output=True,
        timeout=30,
    )


def runtime_roots(profile: HermesProfile) -> list[Path]:
    """Accept only dedicated installation leaves, never user or canonical-data ancestors."""
    local, roaming = (
        Path(os.environ["LOCALAPPDATA"]).resolve(),
        Path(os.environ["APPDATA"]).resolve(),
    )
    source = Path(profile.source).resolve()
    base_value = profile.software.get("base_prefix")
    if not isinstance(base_value, str):
        raise ValueError("Missing worker base interpreter.")
    base = Path(base_value).resolve()
    bridge = Path(__file__).resolve().parent
    expected_source = local / "ember-hermes/candidate-71736c9f/source"
    if source != expected_source.resolve() or bridge != (local / "ember-hermes/bridge").resolve():
        raise ValueError("Use the dedicated native Hermes installation directories.")
    if base.parent != (roaming / "uv/python").resolve() or not base.name.startswith(
        "cpython-3.14."
    ):
        raise ValueError("Use the qualified separate Python installation leaf.")
    roots = [source, base, bridge]
    tasks = (local / "ember-hermes-tasks").resolve()
    if any(tasks.is_relative_to(root) or root.is_relative_to(tasks) for root in roots):
        raise ValueError("Worker homes must not inherit installation read grants.")
    return roots


def _owner_sid() -> str:
    return next(
        csv.reader(
            io.StringIO(
                subprocess.check_output(
                    [
                        str(Path(os.environ["SystemRoot"]) / "System32/whoami.exe"),
                        "/user",
                        "/fo",
                        "csv",
                        "/nh",
                    ],
                    text=True,
                )
            )
        )
    )[1]


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _acl(
        [
            str(path),
            "/inheritance:r",
            "/grant:r",
            "*" + _owner_sid() + ":(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
        ]
    )


class LeaseRecord(BaseModel):
    """Recovery authority contains only an exact task identity and dedicated public roots."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    identity: str = Field(pattern=r"^[0-9a-f]{32}$")
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    roots: list[str] = Field(min_length=3, max_length=3)


def job_name(identity: str) -> str:
    """Use a global unique name so recovery also works across Windows logon sessions."""
    if not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("Invalid task recovery identity.")
    return "Global\\EmberHermes." + identity


class Lease:
    """The controller holds a kernel-released byte lock until every owned resource is purged."""

    def __init__(self, path: Path, stream: BinaryIO, record: LeaseRecord) -> None:
        self.path, self.stream, self.record = path, stream, record

    @staticmethod
    def lock(stream: BinaryIO) -> None:
        """Only an unlocked ledger entry can confer orphan-recovery authority."""
        native = importlib.import_module("msvcrt")
        locking = cast(Callable[[int, int, int], object], native.locking)
        stream.seek(0)
        locking(stream.fileno(), 2, 1)  # LK_NBLCK; released automatically on controller death.

    @classmethod
    def create(cls, profile: HermesProfile, digest: str) -> "Lease":
        root = Path(os.environ["LOCALAPPDATA"]) / "ember-hermes-leases"
        _private_directory(root)
        record = LeaseRecord(
            identity=uuid4().hex,
            profile_sha256=digest,
            roots=[str(path) for path in runtime_roots(profile)],
        )
        path = root / (record.identity + ".json")
        stream = path.open("x+b", buffering=0)
        try:
            stream.write(b"\0" + record.model_dump_json().encode())
            cls.lock(stream)
        except BaseException:
            stream.close()
            path.unlink()
            raise
        return cls(path, stream, record)

    def close(self, *, purged: bool) -> None:
        """Retain uncertain recovery records; remove a confirmed record after releasing its lock."""
        self.stream.close()
        if purged:
            self.path.unlink(missing_ok=True)


def recover_orphans(profile: HermesProfile) -> None:
    """Drain recorded private jobs and purge only unlocked, strictly validated task resources."""
    from ember_agent.runtime.windows_job import WindowsJob

    root = Path(os.environ["LOCALAPPDATA"]) / "ember-hermes-leases"
    if not root.exists():
        return
    entries = list(root.iterdir())
    if len(entries) > 64:
        raise ValueError("Too many pending Hermes recovery records.")
    expected = runtime_roots(profile)
    for path in entries:
        if not re.fullmatch(r"[0-9a-f]{32}\.json", path.name) or not path.is_file():
            raise ValueError("Unexpected Hermes recovery record.")
        stream = path.open("r+b", buffering=0)
        try:
            try:
                Lease.lock(stream)
            except PermissionError:
                continue  # A different live controller still owns this exact task.
            stream.seek(1)
            raw = stream.read(4097)
            if len(raw) > 4096:
                raise ValueError("Hermes recovery record exceeds its bound.")
            record = LeaseRecord.model_validate_json(raw)
            if path.stem != record.identity or [Path(item) for item in record.roots] != expected:
                raise ValueError("Hermes recovery roots changed; reconcile the old installation.")
            job = WindowsJob.open_named(job_name(record.identity))
            if job is not None:
                job.terminate_and_wait(timeout=2)
            boundary = Boundary(profile, identity=record.identity)
            boundary.restore()
            if not boundary.close():
                raise OSError("Hermes orphan resources could not be purged.")
            stream.close()
            path.unlink(missing_ok=True)
        finally:
            stream.close()


class Boundary:
    """One actor's temporary SID, private home and hidden desktop; every grant has rollback."""

    def __init__(self, profile: HermesProfile, *, identity: str) -> None:
        self.api = WindowsAPI()
        job_name(identity)
        self.identity = identity
        self.name = "ember.hermes.task." + self.identity
        self.home = Path(os.environ["LOCALAPPDATA"]) / "ember-hermes-tasks" / self.identity
        self.roots = runtime_roots(profile)
        self.granted: list[Path] = []
        self.sid = w.LPVOID()
        self.sid_text = ""
        self.created = False
        self.station: int | None = None
        self.desktop: int | None = None
        self.station_name = "ember_" + self.identity

    def _sid_string(self) -> None:
        text = w.LPWSTR()
        self.api.require(self.api.security.ConvertSidToStringSidW(self.sid, ctypes.byref(text)))
        try:
            self.sid_text = cast(str, text.value)
        finally:
            self.api.kernel.LocalFree(text)

    def restore(self) -> None:
        """Derive recovery authority from the exact recorded profile, never from a PID."""
        if self.api.userenv.DeriveAppContainerSidFromAppContainerName(
            self.name, ctypes.byref(self.sid)
        ):
            raise OSError("Cannot derive recorded task SID.")
        self._sid_string()
        self.created = True
        self.granted = list(self.roots)

    def prepare(self) -> None:
        """Create only task-owned OS objects and exact installation read/execute ACEs."""
        a, y, u = self.api.security, self.api.ui, self.api.userenv
        result = u.CreateAppContainerProfile(
            self.name, self.name, "Isolated Ember delegated task", None, 0, ctypes.byref(self.sid)
        )
        if result:
            raise OSError("Cannot create task AppContainer profile.")
        self.created = True
        self._sid_string()
        owner = _owner_sid()
        self.home.mkdir(parents=True, exist_ok=False)
        _acl(
            [
                str(self.home),
                "/inheritance:r",
                "/grant:r",
                "*" + owner + ":(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
                "*" + self.sid_text + ":(OI)(CI)M",
            ]
        )
        _acl([str(self.home), "/setintegritylevel", "(OI)(CI)L"])
        for root in self.roots:
            self.granted.append(root)  # Partial native ACL updates also need rollback.
            _acl([str(root), "/grant", "*" + self.sid_text + ":(OI)(CI)RX"])
        descriptor = w.LPVOID()
        sddl = f"D:(A;;GA;;;{owner})(A;;GA;;;{self.sid_text})S:(ML;;NW;;;LW)"
        self.api.require(
            a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None
            )
        )
        try:
            security = _Security(ctypes.sizeof(_Security), descriptor, False)
            previous: object = y.GetProcessWindowStation()
            self.station = _handle(
                y.CreateWindowStationW(self.station_name, 0, 0x37F, ctypes.byref(security))
            )
            self.api.require(y.SetProcessWindowStation(self.station))
            try:
                self.desktop = _handle(
                    y.CreateDesktopW("worker", None, None, 0, 0x1FF, ctypes.byref(security))
                )
            finally:
                self.api.require(y.SetProcessWindowStation(previous))
        finally:
            self.api.kernel.LocalFree(descriptor)

    def close(self) -> bool:
        """Return true only after writable state, profile and every exact SID grant are removed."""
        success = True
        if self.desktop is not None:
            success = bool(self.api.ui.CloseDesktop(self.desktop)) and success
            self.desktop = None
        if self.station is not None:
            success = bool(self.api.ui.CloseWindowStation(self.station)) and success
            self.station = None
        if self.home.exists():
            try:
                shutil.rmtree(self.home)
            except OSError:
                success = False
        if self.created:
            success = self.api.userenv.DeleteAppContainerProfile(self.name) == 0 and success
            self.created = False
        for root in reversed(self.granted):
            try:
                _acl([str(root), "/remove:g", "*" + self.sid_text])
            except (OSError, subprocess.SubprocessError):
                success = False
        if self.sid.value:
            self.api.security.FreeSid(self.sid)
            self.sid = w.LPVOID()
        return success


def _environment(home: Path) -> str:
    required = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "LOCALAPPDATA")}
    required.update(
        HOME=str(home),
        USERPROFILE=str(home),
        APPDATA=str(home),
        TEMP=str(home),
        TMP=str(home),
        HERMES_HOME=str(home / ".hermes"),
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
        USERNAME="ember",
        PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
        NO_COLOR="1",
    )
    return (
        "\0".join(name + "=" + required[name] for name in sorted(required, key=str.upper)) + "\0\0"
    )


def launch(profile: HermesProfile, digest: str) -> tuple[int, bool]:
    """Assign a suspended child to a retained job, relay stdio, then reap and purge before ACK."""
    from ember_agent.runtime.windows_job import WindowsJob

    os_handle = cast(Callable[[int], int], importlib.import_module("msvcrt").get_osfhandle)

    recover_orphans(profile)
    lease = Lease.create(profile, digest)
    boundary = Boundary(profile, identity=lease.record.identity)
    api = boundary.api
    process = _Process()
    job: WindowsJob | None = None
    reaping: Reaping | None = None
    descriptors: list[int] = []
    attributes: object = None
    code, purged = 1, False
    try:
        boundary.prepare()
        child_read, parent_write = os.pipe()
        parent_read, child_write = os.pipe()
        descriptors.extend([child_read, parent_write, parent_read, child_write])
        with open(os.devnull, "wb") as discard:
            handles = (w.HANDLE * 3)(
                os_handle(child_read),
                os_handle(child_write),
                os_handle(discard.fileno()),
            )
            for descriptor in (child_read, child_write, discard.fileno()):
                os.set_inheritable(descriptor, True)
            size = ctypes.c_size_t()
            api.kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            candidate = ctypes.cast(buffer, w.LPVOID)
            api.require(
                api.kernel.InitializeProcThreadAttributeList(candidate, 2, 0, ctypes.byref(size))
            )
            attributes = candidate  # Only initialized attribute lists may be deleted in cleanup.
            capabilities = _Capabilities(boundary.sid, None, 0, 0)
            api.require(
                api.kernel.UpdateProcThreadAttribute(
                    attributes,
                    0,
                    0x20009,
                    ctypes.byref(capabilities),
                    ctypes.sizeof(capabilities),
                    None,
                    None,
                )
            )
            api.require(
                api.kernel.UpdateProcThreadAttribute(
                    attributes,
                    0,
                    0x20002,
                    ctypes.byref(handles),
                    ctypes.sizeof(handles),
                    None,
                    None,
                )
            )
            startup = _StartupEx()
            startup.startup.size = ctypes.sizeof(_StartupEx)
            startup.startup.desktop = boundary.station_name + "\\worker"
            startup.startup.flags = 0x100
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = handles
            startup.attributes = attributes
            arguments = [
                profile.python,
                *profile.python_flags,
                str(Path(__file__).with_name("hermes_worker.py")),
                "--child",
                "--profile-sha256",
                digest,
            ]
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
            environment = ctypes.create_unicode_buffer(_environment(boundary.home))
            api.require(
                api.kernel.CreateProcessW(
                    profile.python,
                    command,
                    None,
                    None,
                    True,
                    0x80000 | 0x400 | 0x8000000 | 4,
                    environment,
                    str(boundary.home),
                    ctypes.byref(startup),
                    ctypes.byref(process),
                )
            )
        process_handle = _handle(process.process)
        job = WindowsJob(process_handle, name=job_name(boundary.identity))
        reaping = Reaping(job)
        if api.kernel.ResumeThread(process.thread) != 1:
            raise OSError("Cannot resume the owned suspended worker.")
        os.close(child_read)
        descriptors.remove(child_read)
        os.close(child_write)
        descriptors.remove(child_write)
        eof, output_done, stopped = threading.Event(), threading.Event(), threading.Event()

        def input_thread() -> None:
            try:
                while not stopped.is_set():
                    data = os.read(sys.stdin.fileno(), 65536)
                    if not data:
                        break
                    os.write(parent_write, data)
            except OSError:
                pass
            finally:
                eof.set()

        def output_thread() -> None:
            try:
                while data := os.read(parent_read, 65536):
                    os.write(sys.stdout.fileno(), data)
            except OSError:
                pass
            finally:
                output_done.set()

        threading.Thread(target=input_thread, daemon=True).start()
        threading.Thread(target=output_thread, daemon=True).start()
        deadline = time.monotonic() + 210
        while api.kernel.WaitForSingleObject(process_handle, 20) == 258:
            if eof.is_set() or time.monotonic() >= deadline:
                if not reaping.wait():
                    raise OSError("Owned worker shutdown was not confirmed.")
                break
        value = w.DWORD()
        api.require(api.kernel.GetExitCodeProcess(process_handle, ctypes.byref(value)))
        code = value.value
        if not reaping.wait():
            raise OSError("Owned worker shutdown was not confirmed.")
        stopped.set()
        if not output_done.wait(timeout=2):
            raise OSError("Worker output did not reach an owned terminal boundary.")
    except Exception:
        code = 1
    finally:
        reaped = not process.process
        try:
            if reaping is not None:
                reaped = reaping.wait()
            elif process.process:
                api.require(api.kernel.TerminateProcess(process.process, 1))
                reaped = api.kernel.WaitForSingleObject(process.process, 2000) == 0
        except Exception:
            reaped = False
        finally:
            for descriptor in descriptors:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
            if process.thread:
                api.kernel.CloseHandle(process.thread)
            if process.process:
                api.kernel.CloseHandle(process.process)
            if attributes is not None:
                api.kernel.DeleteProcThreadAttributeList(attributes)
            try:
                purged = boundary.close() and reaped
            except Exception:
                purged = False
            lease.close(purged=purged)
    return code, purged
