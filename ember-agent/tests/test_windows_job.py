"""Native ownership errors must fail closed while still terminating the retained job."""

import ctypes
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ember_agent.runtime import windows_job
from ember_agent.runtime.config import ConfigurationError
from ember_agent.runtime.windows_job import WindowsJob


@pytest.mark.parametrize("failure", ["open", "membership"])
def test_shutdown_cannot_claim_success_on_native_ownership_error(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def query(handle: int, kind: int, buffer: object, *args: object) -> int:
        if kind == 3:
            struct.pack_into("II", buffer, 0, 1, 1)
            struct.pack_into("P", buffer, 8, 123)
        return 1

    terminate, close = Mock(return_value=1), Mock(return_value=1)
    library = SimpleNamespace(
        TerminateJobObject=terminate,
        QueryInformationJobObject=Mock(side_effect=query),
        OpenProcess=Mock(return_value=0 if failure == "open" else 456),
        IsProcessInJob=Mock(return_value=0),
        WaitForSingleObject=Mock(return_value=0),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=library), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    job = WindowsJob.__new__(WindowsJob)
    job.handle, job._close = 789, close
    with pytest.raises(ConfigurationError, match="owned Windows child"):
        job.terminate_and_wait()
    terminate.assert_called_once_with(789, 1)
    assert job.handle is None
    close.assert_any_call(789)
    if failure == "membership":
        close.assert_any_call(456)
    library.WaitForSingleObject.assert_not_called()


def test_named_job_collision_fails_before_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    library = SimpleNamespace(
        CreateJobObjectW=Mock(return_value=789),
        SetInformationJobObject=Mock(),
        AssignProcessToJobObject=Mock(),
        CloseHandle=Mock(return_value=1),
        LocalFree=Mock(),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=library), raising=False)
    monkeypatch.setattr(ctypes, "set_last_error", lambda *_: None, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 183, raising=False)
    attributes = windows_job._Security(0, None, False)
    monkeypatch.setattr(windows_job, "_private_job_security", lambda: (attributes, 456))
    with pytest.raises(ConfigurationError, match="already exists"):
        WindowsJob(123, name="Global\\EmberHermes." + "a" * 32)
    library.CloseHandle.assert_called_once_with(789)
    library.AssignProcessToJobObject.assert_not_called()
    library.SetInformationJobObject.assert_not_called()
