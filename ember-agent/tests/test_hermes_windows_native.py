"""Opt-in native proofs use synthetic canaries and the exact installed boundary primitives."""

import ctypes
import importlib
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "win32", reason="Native Windows AppContainer proof."),
    pytest.mark.skipif(
        not os.getenv("EMBER_HERMES_PROFILE"), reason="Explicit installed profile required."
    ),
]

_PROBE = r"""
import json,os,socket,sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).parent))
from hermes_windows import verify_appcontainer
verify_appcontainer()
args=json.loads(sys.stdin.readline())
result={'type':'boundary_probe','pid':os.getpid(),'task_identity':Path.cwd().name,
        'token_is_appcontainer':True,'capabilities':0}
for key in ['canonical','credential','sibling']:
 p=Path(args[key]); read=write=False
 try: p.read_bytes(); read=True
 except OSError: pass
 try: p.write_bytes(b'REPLACED'); write=True
 except OSError: pass
 result[key+'_read']=read; result[key+'_write']=write
sdk=Path(args['sdk_write']); writable=False
try: sdk.write_bytes(b'BAD'); writable=True
except OSError: pass
result['sdk_write']=writable
home=Path.cwd(); (home/'synthetic-context.txt').write_text('SYNTHETIC_PRIVATE_CONTEXT')
result['own_home_write']=(home/'synthetic-context.txt').read_text()=='SYNTHETIC_PRIVATE_CONTEXT'
for key in ['loopback','lan']:
 host,port=args[key]; s=socket.socket(); s.settimeout(.3)
 try: result[key+'_connected']=s.connect_ex((host,port))==0
 except OSError: result[key+'_connected']=False
 finally: s.close()
print(json.dumps(result),flush=True)
sys.stdin.read()
"""


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, object, str, ModuleType]:
    receipt = Path(os.environ["EMBER_HERMES_PROFILE"])
    bridge = receipt.parent / "bridge"
    monkeypatch.syspath_prepend(str(bridge))
    setup = importlib.import_module("hermes_setup")
    windows = importlib.import_module("hermes_windows")
    profile, digest = setup.load_profile(receipt)
    return receipt, bridge, profile, digest, windows


def _controller(receipt: Path, bridge: Path) -> tuple[subprocess.Popen[str], queue.Queue]:
    # Only this qualification process changes the verified flags to run a hostile probe.
    # Production load_profile accepts exactly [-B]; actual SDK proof runs those normal flags.
    code = (
        "import json,sys; from pathlib import Path; "
        f"sys.path.insert(0,{str(bridge)!r}); "
        "from hermes_setup import load_profile; from hermes_windows import launch; "
        f"p,d=load_profile(Path({str(receipt)!r})); "
        f"p=p.model_copy(update={{'python_flags':['-B','-c',{_PROBE!r}]}}); "
        "c,purged=launch(p,d); "
        "print(json.dumps({'type':'stopped','purged':purged,'returncode':c}),flush=True); "
        "raise SystemExit(0 if c==0 and purged else 1)"
    )
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    frames = queue.Queue()

    def read() -> None:
        for line in process.stdout:
            frames.put(json.loads(line))

    threading.Thread(target=read, daemon=True).start()
    return process, frames


@pytest.mark.parametrize("shutdown", ["eof", "controller_crash"])
def test_native_denial_and_owned_shutdown(
    native: tuple[Path, Path, object, str, ModuleType], tmp_path: Path, shutdown: str
) -> None:
    receipt, bridge, profile, digest, windows = native
    api = windows.WindowsAPI()
    outside = tmp_path / "private-host"
    windows._private_directory(outside)
    canonical, credential = outside / "canonical.sqlite", outside / "credential.txt"
    canonical.write_bytes(b"SYNTHETIC_CANONICAL")
    credential.write_bytes(b"SYNTHETIC_CREDENTIAL")
    sibling = windows.Boundary(profile, identity=uuid4().hex)
    process = None
    child_handle = None
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    lan = ("10.254.254.10", 8095)
    with socket.create_connection(lan, timeout=2):
        pass  # Host positive control: the worker's network failure must not be an offline host.
    with socket.create_connection(listener.getsockname(), timeout=2):
        pass
    try:
        sibling.prepare()
        sibling_canary = sibling.home / "private-other-actor.txt"
        sibling_canary.write_bytes(b"SYNTHETIC_OTHER_ACTOR")
        sdk_write = Path(profile.source) / ("denied-probe-" + uuid4().hex + ".txt")
        process, frames = _controller(receipt, bridge)
        assert process.stdin is not None
        process.stdin.write(
            json.dumps(
                {
                    "canonical": str(canonical),
                    "credential": str(credential),
                    "sibling": str(sibling_canary),
                    "sdk_write": str(sdk_write),
                    "loopback": listener.getsockname(),
                    "lan": lan,
                }
            )
            + "\n"
        )
        process.stdin.flush()
        frame = frames.get(timeout=35)
        assert frame["type"] == "boundary_probe"
        assert frame["token_is_appcontainer"] and frame["capabilities"] == 0
        assert frame["own_home_write"]
        for key in [
            "canonical_read",
            "canonical_write",
            "credential_read",
            "credential_write",
            "sibling_read",
            "sibling_write",
            "sdk_write",
            "loopback_connected",
            "lan_connected",
        ]:
            assert frame[key] is False, key
        assert canonical.read_bytes() == b"SYNTHETIC_CANONICAL"
        assert credential.read_bytes() == b"SYNTHETIC_CREDENTIAL"
        assert not sdk_write.exists()
        leases = Path(os.environ["LOCALAPPDATA"]) / "ember-hermes-leases"
        live = {item.name for item in leases.glob("*.json")}
        assert live
        own_lease = leases / (frame["task_identity"] + ".json")
        with own_lease.open("rb") as ledger:
            ledger.seek(1)  # The controller deliberately locks byte zero for its lifetime.
            record = json.loads(ledger.read())
        recovered = windows.Boundary(profile, identity=frame["task_identity"])
        recovered.restore()  # Derive SID identity without creating any new OS object.
        sid_text = recovered.sid_text
        api.security.FreeSid(recovered.sid)
        api.userenv.GetAppContainerFolderPath.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        api.userenv.GetAppContainerFolderPath.restype = ctypes.c_long
        folder_pointer = ctypes.c_wchar_p()
        assert api.userenv.GetAppContainerFolderPath(sid_text, ctypes.byref(folder_pointer)) == 0
        profile_folder = Path(folder_pointer.value)
        ole = ctypes.WinDLL("ole32")
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole.CoTaskMemFree(folder_pointer)
        assert profile_folder.exists()
        windows.recover_orphans(profile)  # A locked live controller must be skipped.
        assert own_lease.exists()
        assert process.poll() is None
        api.kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        api.kernel.OpenProcess.restype = ctypes.c_void_p
        child_handle = api.kernel.OpenProcess(0x100000, False, frame["pid"])
        assert child_handle
        started = time.monotonic()
        if shutdown == "eof":
            process.stdin.close()
            stopped = frames.get(timeout=4)
            assert stopped["type"] == "stopped" and stopped["purged"]
            process.wait(timeout=1)
        else:
            process.kill()  # Popen's retained native controller handle, never a saved PID.
            process.wait(timeout=2)
            windows.recover_orphans(profile)
        assert api.kernel.WaitForSingleObject(child_handle, 0) == 0
        elapsed = time.monotonic() - started
        assert elapsed < 4
        assert not own_lease.exists()
        assert not profile_folder.exists()
        for root in record["roots"]:
            acl = subprocess.check_output(
                [str(Path(os.environ["SystemRoot"]) / "System32/icacls.exe"), root], text=True
            )
            assert sid_text not in acl
        homes = Path(os.environ["LOCALAPPDATA"]) / "ember-hermes-tasks"
        assert not (homes / frame["task_identity"]).exists()
        assert sibling.home.exists()
        print(
            json.dumps(
                {
                    "shutdown": shutdown,
                    "cleanup_seconds": elapsed,
                    "child_signalled": True,
                    "canary_denials": 9,
                    "purged": True,
                    "profile_folder_absent": True,
                    "sid_grants_absent": True,
                }
            )
        )
    finally:
        listener.close()
        if child_handle:
            api.kernel.CloseHandle(child_handle)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        windows.recover_orphans(profile)
        assert sibling.close()


@pytest.mark.parametrize("model_rounds,broker", [(1, False), (2, False), (4, False), (2, True)])
def test_actual_sdk_socket_free_structured_completion(
    native: tuple, model_rounds: int, broker: bool, tmp_path: Path
) -> None:
    from .test_hermes_sdk import check_sdk_completion

    receipt, bridge, profile, digest, windows = native
    try:
        check_sdk_completion(
            (sys.executable, "-B", str(bridge / "hermes_worker.py"), "--profile", str(receipt)),
            digest,
            model_rounds,
            broker,
            tmp_path,
        )
    finally:
        windows.recover_orphans(profile)
