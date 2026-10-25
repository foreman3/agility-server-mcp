"""Launch the offline bundle using the host's CPython 3.14, without installation."""

import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import site
import struct
import sys
from pathlib import Path


def prepare() -> Path:
    if (
        sys.platform != "win32"
        or sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 14)
        or struct.calcsize("P") != 8
        or platform.machine().upper() not in ("AMD64", "X86_64")
    ):
        raise SystemExit(
            "This bundle requires CPython 3.14.x for Windows x64. "
            "Select that interpreter explicitly: C:\\path\\to\\python.exe agility-mcp.py"
        )
    root = Path(__file__).resolve().parent
    packages = root / "packages"
    if not (packages / "agility_mcp" / "__main__.py").is_file():
        raise SystemExit("Bundle packages are missing. Extract the entire ZIP before running.")
    # Exclude installed third-party packages so the bundle stands on its own.
    sys.path[:] = [
        path
        for path in sys.path
        if not {"site-packages", "dist-packages"}.intersection(Path(path).parts)
    ]
    sys.path.insert(0, str(packages))
    sys.dont_write_bytecode = True
    # Process pywin32's DLL/bootstrap paths as well as ordinary Python packages.
    site.addsitedir(str(packages))
    # The ctypes credential worker is a separate `python -m` process.
    os.environ["PYTHONPATH"] = str(packages)
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["FASTMCP_CHECK_FOR_UPDATES"] = "off"
    os.environ["FASTMCP_TELEMETRY_MODE"] = "off"
    os.environ.setdefault("AGILITY_CONFIG", str(root / "agility.json"))
    return root


def check_bundle(root: Path) -> None:
    """Verify transfer integrity, dependency closure, and native imports offline."""
    manifest = json.loads((root / "bundle-manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        path = root / name
        if not path.is_file():
            raise SystemExit(f"Bundle file missing or changed: {name}")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise SystemExit(f"Bundle file missing or changed: {name}")

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    distributions = {
        canonicalize_name(distribution.metadata["Name"]): distribution
        for distribution in importlib.metadata.distributions(path=[str(root / "packages")])
    }
    pending = [Requirement("agility-wincred-mcp")]
    checked = set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        distribution = distributions.get(name)
        if distribution is None or not requirement.specifier.contains(distribution.version):
            raise SystemExit(f"Missing or incompatible bundled dependency: {requirement}")
        for extra in {"", *requirement.extras}:
            if (name, extra) in checked:
                continue
            checked.add((name, extra))
            for dependency in distribution.requires or []:
                dependency = Requirement(dependency)
                if dependency.marker is None or dependency.marker.evaluate({"extra": extra}):
                    pending.append(dependency)

    for module in (
        "agility_mcp.server",
        "fastmcp",
        "httpx",
        "defusedxml",
        "jsonschema",
        "pydantic_core",
        "yaml",
        "_cffi_backend",
        "cryptography.hazmat.bindings._rust",
        "rpds",
        "watchfiles",
        "win32api",
        "pythoncom",
    ):
        importlib.import_module(module)
    print(
        f"OK: {len(distributions)} bundled distributions; file hashes, dependencies, "
        f"and native imports verified on Python {platform.python_version()} x64."
    )
    print("No network or Windows credentials were used. agility.json is editable and not hashed.")


if __name__ == "__main__":
    bundle_root = prepare()
    if sys.argv[1:] == ["--check-bundle"]:
        check_bundle(bundle_root)
    else:
        from agility_mcp.cli import main

        main()
