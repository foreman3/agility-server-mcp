"""Isolated Windows credential access. Secrets travel only over anonymous pipes."""

import asyncio
import base64
import json
import re
import subprocess
import sys
from typing import Any, Protocol

import anyio

from .config import Config
from .errors import AgilityError


class CredentialStore(Protocol):
    async def get(self, target: str) -> dict[str, Any]: ...
    async def status(self, target: str) -> dict[str, Any]: ...


class WindowsCredentialStore:
    def __init__(self, *, platform: str = sys.platform, timeout: float = 20):
        self.platform = platform
        self.timeout = timeout

    async def run(self, action: str, target: str, **values: str) -> dict[str, Any]:
        if self.platform != "win32":
            raise AgilityError(
                "Authentication requires Windows Credential Manager. Run this server on Windows under the user who stored the credential."
            )
        child = None
        try:
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "agility_mcp.credential_worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                limit=32769,
            )
            async with asyncio.timeout(self.timeout):
                assert child.stdin is not None and child.stdout is not None
                child.stdin.write(
                    json.dumps(
                        {"action": action, "target": target, **values}, ensure_ascii=True
                    ).encode("utf-8")
                )
                await child.stdin.drain()
                child.stdin.close()
                output = bytearray()
                while chunk := await child.stdout.read(4096):
                    output.extend(chunk)
                    if len(output) > 32768:
                        raise AgilityError("Invalid credential helper response.")
                if await child.wait() != 0:
                    raise AgilityError(
                        "Windows Credential Manager failed. Check your Windows user session and application policy."
                    )
                try:
                    result = json.loads(output.decode("utf-8-sig"))
                    if not isinstance(result, dict):
                        raise ValueError
                    return result
                except (ValueError, UnicodeError):
                    raise AgilityError("Invalid credential helper response.") from None
        except TimeoutError:
            raise AgilityError("Windows Credential Manager timed out.") from None
        except (OSError, BrokenPipeError):
            raise AgilityError("Cannot communicate with Windows Credential Manager.") from None
        finally:
            if child is not None and child.returncode is None:
                try:
                    child.kill()
                except ProcessLookupError:
                    pass
                with anyio.CancelScope(shield=True):
                    await asyncio.shield(child.wait())

    async def get(self, target: str) -> dict[str, Any]:
        value = await self.run("get", target)
        if not value.get("found") or not value.get("secret"):
            raise AgilityError(
                "Agility credential not found or empty. Run agility-mcp credentials set with the same instance URL and Windows user."
            )
        return {"username": value.get("username", ""), "secret": value["secret"]}

    async def status(self, target: str) -> dict[str, Any]:
        return await self.run("status", target)

    async def set(self, target: str, username: str, secret: str) -> dict[str, Any]:
        return await self.run("set", target, username=username, secret=secret)

    async def delete(self, target: str) -> dict[str, Any]:
        return await self.run("delete", target)


async def authorization(store: CredentialStore, config: Config) -> str:
    value = await store.get(config.credentialTarget)
    username, secret = value.get("username", ""), value["secret"]
    if re.search(r"[\r\n\x00]", secret):
        raise AgilityError("Credential contains invalid control characters.")
    if config.authMode == "token":
        return f"Bearer {secret}"
    if not username or re.search(r"[:\r\n\x00]", username):
        raise AgilityError(
            "Basic authentication requires a username without colon or control characters. Store it with credentials set."
        )
    return "Basic " + base64.b64encode(f"{username}:{secret}".encode()).decode("ascii")
