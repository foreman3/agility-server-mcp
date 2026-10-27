"""Build a relocatable dependency-inclusive ZIP for Windows CPython 3.14 x64.

Run on a connected Windows build machine with uv and a Python 3.14 interpreter.
Only the build machine needs uv; the destination needs Python and this ZIP.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", default="uv", help="uv executable (build machine only)")
    parser.add_argument("--python", required=True, help="Windows x64 CPython 3.14 executable")
    parser.add_argument("--offline", action="store_true", help="Use only the existing uv cache")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    python = str(Path(args.python).resolve())
    subprocess.run(
        [
            python,
            "-I",
            "-c",
            "import sys,struct,platform; "
            "assert sys.platform == 'win32' and sys.implementation.name == 'cpython' "
            "and sys.version_info[:2] == (3,14) and struct.calcsize('P') == 8 "
            "and platform.machine().upper() in ('AMD64','X86_64'), "
            "'Build target must be CPython 3.14 for Windows x64'",
        ],
        check=True,
    )
    uv = [args.uv, "--offline"] if args.offline else [args.uv]
    env = {**os.environ, "UV_PYTHON": python, "UV_PYTHON_DOWNLOADS": "never"}
    env.setdefault("UV_CACHE_DIR", str(root / ".package-validation" / "uv-cache"))

    def run(*arguments: str) -> None:
        subprocess.run([*uv, *arguments], cwd=root, env=env, check=True)

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    name = f"agility-wincred-mcp-{project['version']}-offline-win-x64-py314"
    output = root / "dist"
    output.mkdir(exist_ok=True)
    # TemporaryDirectory owns a unique child of this repository's dist directory.
    with tempfile.TemporaryDirectory(prefix="offline-build-", dir=output) as temporary:
        work = Path(temporary)
        bundle = work / name
        bundle.mkdir()
        packages = bundle / "packages"
        requirements = bundle / "requirements-runtime.txt"
        run(
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--no-editable",
            "--format",
            "requirements-txt",
            "--output-file",
            str(requirements),
            "--quiet",
        )
        run("build", "--wheel", "--out-dir", str(work / "wheel"))
        run(
            "pip",
            "install",
            "--python",
            python,
            "--target",
            str(packages),
            "--only-binary",
            ":all:",
            "--require-hashes",
            "--no-deps",
            "--link-mode",
            "copy",
            "--requirements",
            str(requirements),
        )
        wheel = next((work / "wheel").glob("*.whl"))
        # Wheels use package-relative paths. Extract only the server wheel itself;
        # do not include generated console scripts with build-machine paths.
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(packages)
        for source in (root / "offline").iterdir():
            if source.is_file():
                shutil.copy2(source, bundle / source.name)
        shutil.copy2(root / "agility.example.json", bundle / "agility.json")
        shutil.copy2(root / "README.md", bundle / "SERVER-README.md")
        shutil.copy2(root / "LICENSE", bundle / "LICENSE")
        # Only module entry points are used. Generated executable wrappers are
        # interpreter-specific and may contain build-machine absolute paths.
        scripts = packages / "Scripts"
        if scripts.exists():
            if scripts.resolve().parent != packages.resolve():
                raise RuntimeError("Unexpected generated scripts directory")
            shutil.rmtree(scripts)
        inventory = sorted(
            [
                {
                    "name": d.metadata["Name"],
                    "version": d.version,
                    "license": d.metadata.get("License-Expression") or d.metadata.get("License"),
                    "license_files": d.metadata.get_all("License-File", []),
                }
                for d in importlib.metadata.distributions(path=[str(packages)])
            ],
            key=lambda item: item["name"].lower(),
        )
        (bundle / "package-inventory.json").write_text(
            json.dumps(inventory, indent=2) + "\n", encoding="utf-8"
        )
        manifest = {
            "application": project["name"],
            "version": project["version"],
            "target": "CPython 3.14.x / Windows x64",
            "python_included": False,
            "files": {
                path.relative_to(bundle).as_posix(): sha256(path)
                for path in sorted(bundle.rglob("*"))
                if path.is_file()
                and path.name != "agility.json"
                and "__pycache__" not in path.parts
                and path.suffix != ".pyc"
            },
        }
        (bundle / "bundle-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [python, "-I", str(bundle / "agility-mcp.py"), "--check-bundle"],
            cwd=work,
            env=env,
            check=True,
        )
        destination = output / f"{name}.zip"
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(bundle.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    archive.write(path, path.relative_to(work))
        checksum = destination.with_suffix(".zip.sha256")
        checksum.write_text(f"{sha256(destination)}  {destination.name}\n", encoding="ascii")
        print(f"Created {destination} ({destination.stat().st_size / 1024**2:.1f} MiB)")
        print(f"SHA-256: {sha256(destination)}")


if __name__ == "__main__":
    main()
