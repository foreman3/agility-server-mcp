"""Configuration and stable, legacy-compatible credential target derivation."""

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from .errors import AgilityError

FIELDS = {
    "base-url": "baseUrl",
    "credential-target": "credentialTarget",
    "auth-mode": "authMode",
    "rest-endpoint": "restEndpoint",
    "timeout-ms": "timeoutMs",
    "retries": "retries",
    "max-response-bytes": "maxResponseBytes",
    "max-concurrency": "maxConcurrency",
    "enable-writes": "enableWrites",
    "allow-operations": "allowOperations",
    "allow-http": "allowHttp",
}


def normalize_base_url(value: Any, allow_http: bool = False) -> str:
    try:
        if not isinstance(value, str) or re.search(r"[\x00-\x20]", value):
            raise ValueError
        url = urlsplit(value.replace("\\", "/"))
        if not url.hostname:
            raise ValueError
        port = url.port
    except (ValueError, TypeError):
        raise AgilityError(
            "Set AGILITY_BASE_URL to the Agility instance URL, including its instance path."
        ) from None
    if url.scheme != "https" and not (allow_http and url.scheme == "http"):
        raise AgilityError(
            "Agility requires HTTPS. Use --allow-http only for a trusted development instance."
        )
    if url.username is not None or url.password is not None or "?" in value or "#" in value:
        raise AgilityError("The base URL cannot contain credentials, a query, or a fragment.")
    if re.search(r"%2f|%5c|%2e", url.path, re.IGNORECASE):
        raise AgilityError("Encoded path separators and dot segments are not allowed.")
    host = url.hostname.encode("idna").decode().lower()
    if ":" in host:
        host = f"[{host}]"
    if port and port != (443 if url.scheme == "https" else 80):
        host += f":{port}"
    # WHATWG URL removes literal dot segments without collapsing repeated separators.
    segments: list[str] = []
    for segment in url.path.split("/"):
        if segment == "..":
            if segments:
                segments.pop()
        elif segment != ".":
            segments.append(segment)
    path = quote("/".join(segments), safe="/%:@!$&'()*+,;=-._~")
    return urlunsplit((url.scheme, host, path, "", "")).rstrip("/")


def parse_args(args: list[str]) -> tuple[dict[str, Any], list[str]]:
    options: dict[str, Any] = {}
    positional = []
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if not arg.startswith("--"):
            positional.append(arg)
            continue
        key, sep, value = arg[2:].partition("=")
        if key in ("help", "version"):
            options[key] = True
            continue
        if key != "config" and key not in FIELDS:
            raise AgilityError(f"Unknown option --{key}. Run --help.")
        if key in ("enable-writes", "allow-http"):
            if sep:
                raise AgilityError(f"--{key} does not accept a value.")
            options[FIELDS[key]] = True
        else:
            if not sep:
                value = args[index] if index < len(args) else ""
                index += 1
            if not value or value.startswith("--"):
                raise AgilityError(f"Missing value for --{key}.")
            options[FIELDS.get(key, key)] = value
    return options, positional


def integer(value: Any, fallback: int, low: int, high: int, name: str) -> int:
    try:
        n = float(fallback if value is None else value)
        if isinstance(value, bool) or not n.is_integer() or not low <= n <= high:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise AgilityError(f"{name} must be an integer between {low} and {high}.") from None
    return int(n)


def boolean(value: Any, name: str) -> bool:
    if value is None or value is False or value == "false":
        return False
    if value is True or value == "true":
        return True
    raise AgilityError(f"{name} must be true or false.")


@dataclass(frozen=True)
class Config:
    baseUrl: str
    credentialTarget: str
    allowHttp: bool = False
    authMode: str = "token"
    restEndpoint: str = "rest-1.v1"
    enableWrites: bool = False
    allowOperations: tuple[str, ...] = ()
    timeoutMs: int = 30000
    retries: int = 2
    maxResponseBytes: int = 4 * 1024 * 1024
    maxConcurrency: int = 4


def load_config(
    options: Mapping[str, Any] | None = None, env: Mapping[str, str] | None = None
) -> Config:
    options = options or {}
    env = os.environ if env is None else env
    file: dict[str, Any] = {}
    path = options.get("config", env.get("AGILITY_CONFIG"))
    if path:
        try:
            file = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise AgilityError("Cannot read the configuration file as JSON.") from None
        if not isinstance(file, dict):
            raise AgilityError("Configuration must be an object.")
        unknown = [key for key in file if key not in FIELDS.values()]
        if unknown:
            raise AgilityError(
                f"Unknown configuration keys: {', '.join(unknown)}. Secrets belong in Credential Manager."
            )
    environment = {}
    for key in FIELDS.values():
        name = "AGILITY_" + re.sub(r"[A-Z]", lambda m: "_" + m[0], key).upper()
        if name in env:
            environment[key] = env[name]
    raw = file | environment | dict(options)
    allow_http = boolean(raw.get("allowHttp"), "allowHttp")
    base = normalize_base_url(raw.get("baseUrl"), allow_http)
    auth = raw.get("authMode", "token")
    endpoint = raw.get("restEndpoint", "rest-1.v1")
    if auth not in ("token", "basic"):
        raise AgilityError("authMode must be token or basic.")
    if endpoint not in ("rest-1.v1", "rest-1.oauth.v1"):
        raise AgilityError("restEndpoint must be rest-1.v1 or rest-1.oauth.v1.")
    target = raw.get(
        "credentialTarget", "agility-mcp:" + hashlib.sha256(base.encode()).hexdigest()[:32]
    )
    if (
        not isinstance(target, str)
        or not 1 <= len(target) <= 256
        or re.search(r"[\x00-\x1f]", target)
    ):
        raise AgilityError("Invalid Credential Manager target.")
    operations = raw.get("allowOperations", [])
    if isinstance(operations, str):
        operations = [op for op in operations.split(",") if op]
    if not isinstance(operations, list) or any(
        not isinstance(op, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", op)
        for op in operations
    ):
        raise AgilityError(
            "allowOperations must contain exact operation names separated by commas."
        )
    writes = boolean(raw.get("enableWrites"), "enableWrites")
    if operations and not writes:
        raise AgilityError("allowOperations requires enableWrites.")
    limits = {
        key: integer(raw.get(key), default, low, high, key)
        for key, default, low, high in (
            ("timeoutMs", 30000, 100, 300000),
            ("retries", 2, 0, 5),
            ("maxResponseBytes", 4194304, 1024, 33554432),
            ("maxConcurrency", 4, 1, 16),
        )
    }
    return Config(
        baseUrl=base,
        credentialTarget=target,
        allowHttp=allow_http,
        authMode=auth,
        restEndpoint=endpoint,
        enableWrites=writes,
        allowOperations=tuple(operations),
        **limits,
    )
