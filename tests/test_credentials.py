import asyncio
import json

import pytest

from agility_mcp.credentials import WindowsCredentialStore
from agility_mcp.errors import AgilityError


class FakeInput:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        pass


class FakeOutput:
    def __init__(self, data=None):
        self.data = data

    async def read(self, count):
        if self.data is None:
            await asyncio.sleep(100)
        data, self.data = self.data, b""
        return data


class Child:
    def __init__(self, output=None):
        self.stdin, self.stdout = FakeInput(), FakeOutput(output)
        self.returncode = None
        self.killed = False

    def kill(self):
        self.killed = True
        self.returncode = -1

    async def wait(self):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


async def test_worker_cancel_kills_child(monkeypatch):
    child = Child()
    started = asyncio.Event()

    async def spawn(*args, **kwargs):
        assert "synthetic" not in " ".join(args)
        started.set()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(
        WindowsCredentialStore(platform="win32").run(
            "set", "target", username="user", secret="synthetic"
        )
    )
    await started.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert child.killed
    assert json.loads(child.stdin.data)["secret"] == "synthetic"


async def test_worker_timeout_and_output_cap(monkeypatch):
    child = Child()

    async def spawn(*args, **kwargs):
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(AgilityError, match="timed out"):
        await WindowsCredentialStore(platform="win32", timeout=0.01).status("target")
    assert child.killed
    child = Child(b"x" * 32769)
    with pytest.raises(AgilityError, match="Invalid credential helper"):
        await WindowsCredentialStore(platform="win32").status("target")
    assert child.killed


async def test_cancelling_credential_load_never_issues_http(make_client):
    client = make_client()
    entered = asyncio.Event()

    class Waiting:
        async def get(self, target):
            entered.set()
            await asyncio.sleep(100)

    client.store = Waiting()
    task = asyncio.create_task(client.get(oid="Story:10"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.requests == []
