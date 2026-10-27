"""The stdio server and credential/diagnostic commands."""

import asyncio
import getpass
import json
import re
import sys

from . import __version__
from .client import AgilityClient, create_http_client
from .config import load_config, parse_args
from .credentials import WindowsCredentialStore
from .errors import AgilityError
from .server import create_server

HELP = f"""agility-mcp {__version__} — Digital.ai Agility / VersionOne MCP server

Usage:
  agility-mcp [serve] [options]
  agility-mcp credentials set|status|delete [options]
  agility-mcp doctor [options]

Options (also supported in AGILITY_* environment variables or JSON config):
  --base-url URL                Instance URL, including its application path
  --config PATH                 JSON configuration (no secrets)
  --credential-target NAME      Generic Credential target (default: URL-derived)
  --auth-mode token|basic       Default: token
  --rest-endpoint NAME          rest-1.v1 (default) or rest-1.oauth.v1
  --enable-writes               Register create/update tools
  --allow-operations NAMES      Exact comma-separated names; requires writes
  --timeout-ms NUMBER           Per-attempt HTTP timeout (default: 30000)
  --retries NUMBER              Read retries (default: 2; never retries writes)
  --max-response-bytes NUMBER   HTTP response limit (default: 4194304)
  --max-concurrency NUMBER      Concurrent HTTP requests (default: 4)
  --allow-http                 Allow HTTP for trusted development only
  --verify-tls true|false        Verify TLS certificates (default: true)
  --help                       Show usage
  --version                    Show version

credentials set prompts for the token/password without echoing it.
Secrets are never accepted as command-line flags or environment variables.
"""


async def run(args: list[str]) -> None:
    options, positional = parse_args(args)
    if options.get("help"):
        sys.stderr.write(HELP)
        return
    if options.get("version"):
        sys.stderr.write(__version__ + "\n")
        return
    command = positional[0] if positional else "serve"
    if command not in ("serve", "credentials", "doctor") or len(positional) > (
        2 if command == "credentials" else 1
    ):
        raise AgilityError("Invalid command. Run --help.")
    config = load_config(options)
    store = WindowsCredentialStore()
    if command == "credentials":
        action = positional[1] if len(positional) > 1 else ""
        if action == "set":
            if not sys.stdin.isatty() or not sys.stderr.isatty():
                raise AgilityError("credentials set requires an interactive Windows terminal.")
            username = "agility-token"
            if config.authMode == "basic":
                sys.stderr.write("Agility username: ")
                username = input()
                if not username or re.search(r"[:\r\n\x00]", username):
                    raise AgilityError("Invalid Basic authentication username.")
            secret = getpass.getpass(
                "Agility access token (hidden): "
                if config.authMode == "token"
                else "Agility password (hidden): ",
                stream=sys.stderr,
            )
            if not secret or len(secret.encode("utf-16-le")) > 2560 or "\x00" in secret:
                raise AgilityError("Secret must contain 1 to 1280 UTF-16 characters, without NUL.")
            await store.set(config.credentialTarget, username, secret)
            sys.stderr.write(
                f"Credential stored for {config.baseUrl}\nTarget: {config.credentialTarget}\n"
            )
        elif action in ("status", "delete"):
            result = await getattr(store, action)(config.credentialTarget)
            sys.stderr.write(json.dumps({"target": config.credentialTarget, **result}) + "\n")
        else:
            raise AgilityError("Use credentials set, credentials status, or credentials delete.")
        return
    if command == "doctor":
        if not (await store.status(config.credentialTarget))["found"]:
            raise AgilityError("Credential not found. Run credentials set first.")
        async with create_http_client(config) as http:
            client = AgilityClient(config, store, http=http)
            await client.list(assetType="Scope", select=["Name"], pageSize=1)
        sys.stderr.write(f"Agility authentication and REST query succeeded for {config.baseUrl}\n")
        return
    if sys.platform != "win32":
        raise AgilityError(
            "This server requires Windows Credential Manager and must run on Windows."
        )
    await create_server(config, store=store).run_async(
        transport="stdio", show_banner=False, log_level="ERROR"
    )


def main() -> None:
    try:
        asyncio.run(run(sys.argv[1:]))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return
    except AgilityError as error:
        sys.stderr.write(f"agility-mcp: {error}\n")
        raise SystemExit(1) from None
    except Exception:
        sys.stderr.write(
            "agility-mcp: Agility operation failed. Check configuration and the Windows user session.\n"
        )
        raise SystemExit(1) from None
