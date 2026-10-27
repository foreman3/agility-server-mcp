Digital.ai Agility MCP - offline bundle for CPython 3.14 / Windows x64
====================================================================

Everything needed by the application is already in packages/, including native
extensions, dependency metadata, and third-party license files. Python itself is
NOT included. Use CPython 3.14.x (64-bit x86/AMD64), Windows 10/11 or compatible
Windows Server. This particular bundle does not support Python 3.12/3.13/3.15,
32-bit Python, native ARM64 Python, WSL, or Linux.

No pip install, uv, uvx, internet access, virtual environment, administrator
rights, or build tools are needed. Extract the ENTIRE ZIP to a local folder;
keep agility-mcp.py, agility.json, and packages/ together. Do not run inside the
ZIP viewer. Python must be available as 'python', or use its full path below.

1. Open PowerShell in the extracted folder and verify the copy:

   python -I .\agility-mcp.py --check-bundle

   This checks file hashes, dependency versions, and native imports without any
   network or credential access. The Python version guard catches mismatches.

2. Edit agility.json and replace baseUrl with your real Agility instance URL,
   including its application path. For a Windows Integrated Authentication
   deployment using bearer tokens, use restEndpoint: "rest-1.oauth.v1".
   Configuration precedence is CLI flags, AGILITY_* environment variables,
   agility.json, then defaults. This launcher automatically finds agility.json
   beside itself, even when started from another working directory. An explicit
   --config or AGILITY_CONFIG overrides that default.

3. Store your token ON THE DESTINATION COMPUTER under your Windows user:

   python -I .\agility-mcp.py credentials set

   The prompt hides the token and saves it in Windows Credential Manager.
   Tokens are not included in this ZIP. Credential entries from a different
   computer are not transferred with the application. If an existing credential
   on this computer matches the URL/target and user, you can reuse it.

4. Check access to your internal Agility server:

   python -I .\agility-mcp.py doctor

5. Run the stdio MCP server:

   python -I .\agility-mcp.py serve

   serve waits for an MCP client on standard input; it has no interactive
   command prompt. Your internal Agility server must be reachable, but no public
   internet connection is required. Update checks and telemetry are disabled
   by this launcher. Write tools are disabled in the example configuration.

You can also use .\agility-mcp.cmd with the same arguments. To select another
Python installation for that wrapper in PowerShell:

   $env:AGILITY_PYTHON = 'C:\Path\To\Python314\python.exe'
   .\agility-mcp.cmd --check-bundle

Or invoke Python directly (the most reliable choice for an MCP host):

   & 'C:\Path\To\Python314\python.exe' -I 'D:\Agility\agility-mcp.py' serve

MCP client configuration
------------------------
Edit the absolute paths in mcp-host.example.json, then copy the server entry
into your MCP client's configuration. Use the Python executable as command;
no shell or uv runner is required. Run the MCP host under the same Windows user
who stored the credential. Move the folder later by updating the script path.

Certificates and proxies
-------------------------
For an internal certificate authority, provide your organization's PEM trust
bundle through SSL_CERT_FILE. HTTPS_PROXY and NO_PROXY are supported. Keep TLS
verification enabled. See SERVER-README.md for all configuration and tools.

Contents and provenance
-------------------------
packages/                 Server plus all 72 runtime dependencies
requirements-runtime.txt  Exact dependency versions/hashes exported from uv.lock
bundle-manifest.json      SHA-256 for shipped files (except editable agility.json)
package-inventory.json    Installed package names, versions, and license metadata
LICENSE                   Server license; dependency licenses stay in packages/
SERVER-README.md           Full project documentation

--check-bundle detects missing/changed files after transfer. A SHA-256 for the ZIP
is also supplied alongside it. Do not mix packages from different Python
versions. Rebuild a fresh bundle when updating the server or its dependencies.
