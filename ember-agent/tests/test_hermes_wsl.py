"""Opt-in Windows-to-WSL namespace and lifecycle proofs with synthetic context only."""

import asyncio
import json
import os
import subprocess
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest

from ember_agent.runtime.config import load_config
from ember_agent.runtime.worker_process import owned_stdio

pytestmark = [pytest.mark.integration]

_PROBE = r"""
import json,os,socket,sys
from pathlib import Path
args=json.loads(sys.stdin.readline())
result={'type':'boundary_probe','network_namespace':os.readlink('/proc/self/ns/net')}
for key in ['canonical','credential','sibling','windows']:
 p=Path(args[key]); read=write=False
 try: p.read_bytes(); read=True
 except OSError: pass
 try: p.write_bytes(b'REPLACED'); write=True
 except OSError: pass
 result[key+'_read']=read; result[key+'_write']=write
p=Path(args['sdk_write']); writable=False
try: p.write_bytes(b'BAD'); writable=True
except OSError: pass
result['sdk_write']=writable
p=Path('/task/home/context.txt'); p.write_text('SYNTHETIC_PRIVATE_CONTEXT')
result['own_home_write']=p.read_text()=='SYNTHETIC_PRIVATE_CONTEXT'
for key in ['loopback','lan']:
 s=socket.socket(); s.settimeout(.3)
 try: result[key+'_connected']=s.connect_ex(tuple(args[key]))==0
 except OSError: result[key+'_connected']=False
 finally: s.close()
print(json.dumps(result),flush=True)
sys.stdin.read()
"""


def _installation() -> tuple[tuple[str, ...], str, str]:
    setting = os.getenv("EMBER_TEST_HERMES_CONFIG")
    if not setting or os.name != "nt":
        pytest.skip("Explicit Windows configuration with the selected WSL worker required.")
    settings = load_config(Path(setting), require_model_pin=True).selected_harness
    assert settings is not None
    command = settings.command
    if Path(command[0]).name.lower() != "wsl.exe":
        pytest.skip("Selected worker is not WSL.")
    index = command.index("--exec")
    assert command[index + 2] == "-B" and command[-2] == "--profile"
    return command[: index + 2], command[index + 3], command[-1]


def _controller(prefix: tuple[str, ...], bridge: str, receipt: str, nonce: str) -> tuple[str, ...]:
    # Override flags only inside qualification; production load_profile accepts exactly [-B].
    code = f"""
import json,os,socket,subprocess,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path({bridge!r}).parent))
from hermes_setup import load_profile
from hermes_worker import sandbox_command,relay
p,d=load_profile(Path({receipt!r}))
p=p.model_copy(update={{'python_flags':['-B','-c',{_PROBE!r},{nonce!r}]}})
with tempfile.TemporaryDirectory(prefix='ember-wsl-proof-') as home:
 args=json.loads(sys.stdin.readline())
 for key in ['canonical','credential','sibling']:
  path=Path(home)/key; path.write_bytes(b'SYNTHETIC_HOST_CANARY'); args[key]=str(path)
 args['sdk_write']=str(Path(p.source)/('denied-'+{nonce!r}))
 with socket.create_connection(tuple(args['lan']),timeout=2): pass
 listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen()
 args['loopback']=list(listener.getsockname())
 with socket.create_connection(listener.getsockname(),timeout=2): pass
 child=subprocess.Popen(sandbox_command(p,d),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,start_new_session=True)
 child.stdin.write((json.dumps(args)+'\\n').encode()); child.stdin.flush()
 code=relay(child,sys.stdin.buffer,sys.stdout.buffer)
 assert all((Path(home)/key).read_bytes()==b'SYNTHETIC_HOST_CANARY'
            for key in ['canonical','credential','sibling'])
 assert not Path(args['sdk_write']).exists()
 print(json.dumps({{'type':'stopped','purged':True,'returncode':code}}),flush=True)
"""
    return (*prefix, "-B", "-c", code)


def _assert_stopped(prefix: tuple[str, ...], nonce: str) -> None:
    code = """
import json,os,sys
from pathlib import Path
needle=sys.argv[1].encode(); found=[]
for path in Path('/proc').glob('[0-9]*/cmdline'):
 try:
  if int(path.parent.name)!=os.getpid() and needle in path.read_bytes():
   found.append(int(path.parent.name))
 except (FileNotFoundError,ProcessLookupError,PermissionError): pass
print(json.dumps(found))
"""
    deadline = monotonic() + 5
    while True:
        active = json.loads(subprocess.check_output([*prefix, "-B", "-c", code, nonce], timeout=10))
        if not active:
            return
        assert monotonic() < deadline, "WSL worker/context namespace survived its Windows owner"
        sleep(0.1)


@pytest.mark.parametrize("shutdown", ["eof", "cancel", "controller_crash"])
def test_wsl_denials_and_windows_owner_shutdown(tmp_path: Path, shutdown: str) -> None:
    prefix, bridge, receipt = _installation()
    nonce = "ember-proof-" + uuid4().hex
    command = _controller(prefix, bridge, receipt, nonce)
    canary = tmp_path / "windows-credential.txt"
    canary.write_bytes(b"SYNTHETIC_WINDOWS_CREDENTIAL")
    windows_path = "/mnt/" + canary.drive[0].lower() + canary.as_posix()[2:]
    payload = json.dumps({"windows": windows_path, "lan": ["10.254.254.10", 8095]}) + "\n"

    def verify(frame: dict) -> None:
        assert frame["type"] == "boundary_probe" and frame["own_home_write"] is True
        for key in ["canonical", "credential", "sibling", "windows"]:
            assert frame[key + "_read"] is False and frame[key + "_write"] is False
        assert frame["sdk_write"] is False
        assert frame["loopback_connected"] is False and frame["lan_connected"] is False

    started = monotonic()
    if shutdown == "cancel":

        async def run() -> None:
            nonlocal started
            async with owned_stdio(command) as process:
                assert process.stdin is not None and process.stdout is not None
                process.stdin.write(payload.encode())
                await process.stdin.drain()
                async with asyncio.timeout(30):
                    verify(json.loads(await process.stdout.readline()))
                started = monotonic()
                raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    else:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(payload)
            process.stdin.flush()
            verify(json.loads(process.stdout.readline()))
            started = monotonic()
            if shutdown == "controller_crash":
                process.kill()  # Kill the Windows wsl.exe controller, not a Linux PID.
            else:
                process.stdin.close()
                stopped = json.loads(process.stdout.readline())
                assert stopped["type"] == "stopped" and stopped["purged"] is True
            process.wait(timeout=6)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            for pipe in [process.stdin, process.stdout, process.stderr]:
                if pipe is not None:
                    pipe.close()
    _assert_stopped(prefix, nonce)
    assert canary.read_bytes() == b"SYNTHETIC_WINDOWS_CREDENTIAL"
    print(
        json.dumps(
            {
                "shutdown": shutdown,
                "cleanup_seconds": monotonic() - started,
                "denials": 11,
                "namespace_stopped": True,
                "context_tmpfs_removed": True,
            }
        )
    )
