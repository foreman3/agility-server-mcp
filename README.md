# Digital.ai Agility MCP for Windows

A Python 3.12+ MCP server for Digital.ai Agility, formerly VersionOne. Runs over stdio using standalone FastMCP 4.1.3. Authentication comes exclusively from **Windows Credential Manager**, using the current Windows user's Generic Credentials.

Requires Windows 10/11 or Windows Server, Python 3.12+, and an Agility instance. uv is an optional installation/launch tool; ordinary Python also works. No Node.js, PowerShell, C# compiler, or native extension is required by the credential worker. It calls `CredReadW`, `CredWriteW`, `CredDeleteW`, and `CredFree` directly through Python `ctypes` in a short-lived process.

## Offline ZIP for Python 3.14 on Windows x64

The `agility-wincred-mcp-0.4.1-offline-win-x64-py314.zip` bundle includes the server and every runtime dependency, including compiled extensions. Python itself is not included. The destination needs **CPython 3.14.x, Windows x64** and access to your internal Agility server; no pip, uv, internet access, or installation is needed. This specific ZIP does not support other Python minor versions or architectures.

Extract the entire ZIP, open PowerShell in its folder, and run:

```powershell
python -I .\agility-mcp.py --check-bundle
# Edit agility.json: set baseUrl and, if needed, restEndpoint.
python -I .\agility-mcp.py credentials set
python -I .\agility-mcp.py doctor
python -I .\agility-mcp.py serve
```

The launcher finds its bundled packages and `agility.json` relative to its own location. `credentials set` saves the token on the destination computer under the current Windows user; the ZIP contains no credentials. An existing matching credential on that computer can be reused. Use `mcp-host.example.json` for a host configuration with absolute Python and launcher paths. See the ZIP's `README.txt` for certificate settings and other details.

To rebuild on a connected Windows machine, using a Windows x64 Python 3.14 interpreter and uv:

```powershell
python scripts/build_offline.py --python C:\Path\To\Python314\python.exe
```

The builder uses `uv.lock`, checks dependency download hashes, retains package licenses, and writes the ZIP and its SHA-256 under `dist/`. Its `--offline` option uses an already populated uv cache. Only the build machine needs uv. The bundled `--check-bundle` verifies file hashes, dependency compatibility, and native imports without network or credential access.

## Quick start from this checkout

```powershell
uv sync --locked
$env:AGILITY_BASE_URL = 'https://your-server/YourAgilityInstance'
uv run agility-mcp credentials set
uv run agility-mcp doctor
uv run agility-mcp credentials status
```

`credentials set` prompts for the access token **without echoing it**. Create the token in Agility's Member Applications/grant UI first; this server does not create grants. Repeating setup overwrites the same saved credential, allowing rotation. The default credential target is derived from the normalized instance URL; keep its path and case consistent.

The worker stores a Generic Credential with machine-local persistence for the current Windows user (`Type=1`, `Persist=2`), using UTF-16LE. You can also use Control Panel → Credential Manager → Windows Credentials → Add a generic credential with the exact target reported by `credentials status`. Token authentication accepts an arbitrary username; Basic authentication requires the Agility username. The secret limit is 2,560 bytes (1,280 UTF-16 code units).

Run the MCP host under the **same Windows user** as credential setup. A Linux, WSL, or remote process cannot read this user's Windows vault. To remove an entry, run `uv run agility-mcp credentials delete` with the same configuration.

## Run with uvx

Version 0.4.1 is not published to PyPI. Build a wheel from this checkout:

```powershell
uv build
uvx --from .\dist\agility_wincred_mcp-0.4.1-py3-none-any.whl agility-mcp --version
uvx --from .\dist\agility_wincred_mcp-0.4.1-py3-none-any.whl agility-mcp credentials set --base-url https://your-server/YourAgilityInstance
uvx --from .\dist\agility_wincred_mcp-0.4.1-py3-none-any.whl agility-mcp doctor --base-url https://your-server/YourAgilityInstance
```

Or use a full, reviewed Git commit SHA containing the Python migration:

```powershell
uvx --from "git+https://github.com/foreman3/agility-server-mcp.git@<FULL_COMMIT_SHA>" agility-mcp serve --base-url https://your-server/YourAgilityInstance
```

Replace `<FULL_COMMIT_SHA>` with the exact revision you reviewed. Do not use an unpinned branch in a host configuration. `python -m agility_mcp` supports the same commands after installation.

## MCP host configuration

A Windows MCP host that uses `mcpServers` can launch the built wheel:

```json
{
  "mcpServers": {
    "agility": {
      "command": "uvx",
      "args": ["--python", "3.12", "--from", "C:\\path\\to\\agility_wincred_mcp-0.4.1-py3-none-any.whl", "agility-mcp", "serve"],
      "env": {
        "AGILITY_BASE_URL": "https://your-server/YourAgilityInstance"
      }
    }
  }
}
```

Use an absolute wheel path visible to the host. For a pinned source install, replace the `--from` argument with the Git URL above. For development use `command: "uv"` and `args: ["run", "--project", "C:\\path\\to\\checkout", "agility-mcp", "serve"]`. Ensure `uv`/`uvx` is on the host's PATH or specify the full executable path.

## Migration from 0.3.0

Replace the Node/npx host launch command with uvx. JSON configuration keys, CLI flags, `AGILITY_*` variables, precedence, tool names, camelCase arguments, and result envelopes are preserved. Existing URL-derived credential targets and explicitly configured targets are unchanged; **you do not need to re-enter existing credentials**. Both credential implementations were checked with synthetic Unicode credentials in each direction.

Replace `NODE_EXTRA_CA_CERTS` with `SSL_CERT_FILE` pointing to a PEM bundle containing the certificate authorities needed by your instance. Python HTTPX uses its default CA bundle unless `SSL_CERT_FILE` or `SSL_CERT_DIR` overrides it; it does not automatically inherit Node's certificate settings. See [HTTPX certificate configuration](https://www.python-httpx.org/advanced/ssl/). HTTPX respects `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY`; configure `NO_PROXY` for local development fixtures. TLS verification is enabled by default and can be disabled with `verifyTls: false`.

## Configuration

Configuration precedence: CLI flags → environment → optional JSON file → defaults. Pass `--config C:\path\agility.json` or set `AGILITY_CONFIG`. Unknown JSON keys are rejected to catch typos and misplaced secrets. No credential secret is accepted through CLI flags, environment variables, tool arguments, or config files.

| JSON property | Environment variable | Default |
| --- | --- | --- |
| `baseUrl` | `AGILITY_BASE_URL` | Required; include instance path |
| `credentialTarget` | `AGILITY_CREDENTIAL_TARGET` | `agility-mcp:` + first 32 hex chars of SHA-256(base URL) |
| `authMode` | `AGILITY_AUTH_MODE` | `token`; optional `basic` |
| `restEndpoint` | `AGILITY_REST_ENDPOINT` | `rest-1.v1`; optional `rest-1.oauth.v1` |
| `enableWrites` | `AGILITY_ENABLE_WRITES` | `false` |
| `allowOperations` | `AGILITY_ALLOW_OPERATIONS` | Empty; JSON array or comma-separated env/CLI list |
| `timeoutMs` | `AGILITY_TIMEOUT_MS` | `30000` per HTTP attempt; range 100–300000 |
| `retries` | `AGILITY_RETRIES` | `2` additional read attempts; range 0–5 |
| `maxResponseBytes` | `AGILITY_MAX_RESPONSE_BYTES` | 4 MiB decompressed; range 1 KiB–32 MiB |
| `maxConcurrency` | `AGILITY_MAX_CONCURRENCY` | `4`; range 1–16 |
| `allowHttp` | `AGILITY_ALLOW_HTTP` | `false`; development only |
| `verifyTls` | `AGILITY_VERIFY_TLS` | `true`; set to `false` to disable TLS certificate and hostname verification |

The corresponding flags use kebab case, e.g. `--base-url`, `--max-response-bytes`, `--enable-writes`. Boolean environment variables accept exactly `true` or `false`; boolean CLI flags enable the option without a value.

Example configuration:

```json
{
  "baseUrl": "https://your-server/YourAgilityInstance",
  "restEndpoint": "rest-1.oauth.v1",
  "enableWrites": true,
  "allowOperations": ["Close", "Reactivate"],
  "timeoutMs": 30000
}
```

Agility recommends bearer access tokens. For an instance configured with Windows Integrated Authentication, token-based REST access uses **`rest-1.oauth.v1`**. The credential source is the Windows vault; this server does **not** implement NTLM/Kerberos negotiation. Basic authentication is optional for deployments that support it; it is not supported for on-premises Windows Integrated Authentication or on-demand SSO. `query.v1` has no configurable OAuth REST variant; if your deployment rejects it, use `agility_list_assets`, which uses the configured REST endpoint.

Use the application base URL, not `/Default.aspx`, `/Account.mvc/Login`, or an API endpoint URL. HTTPS is required unless explicitly allowing HTTP. For enterprise certificate authorities, set `SSL_CERT_FILE` to a trusted PEM certificate bundle as described in the migration section. HTTPX supports standard proxy environment variables.

For a self-signed server, you can disable TLS verification in your JSON configuration (including the offline bundle's `agility.json`):

```json
{
  "baseUrl": "https://your-server/YourAgilityInstance",
  "verifyTls": false
}
```

The same setting is available as `AGILITY_VERIFY_TLS=false` or `--verify-tls false`, using the normal configuration precedence. It applies to MCP tools, `doctor`, and the offline launcher. Setting it to `false` disables certificate-chain and hostname checks; HTTPS traffic remains encrypted. Set it back to `true` to enable verification.

## Tools

| Tool | Purpose |
| --- | --- |
| `agility_status` | Configuration and credential presence; `checkApi: true` verifies a small authenticated REST query |
| `agility_metadata` | All asset types, or one type/attribute/operation definition |
| `agility_list_assets` | REST queries with `select`, `where`, text search, sort, paging, optional `asOf` |
| `agility_get_asset` | Read an OID with explicit selected fields; historical moment OIDs are supported |
| `agility_get_item_detail` | Detailed fields, schema metadata, hierarchy, upstream/downstream dependency graph, blockers, and cycle detection |
| `agility_related_items` | A resumable page of a metadata-verified relationship |
| `agility_asset_history` | Paged historical versions of one asset |
| `agility_new_asset_template` | Read defaults/template in an optional parent context |
| `agility_query` | Up to 10 read-only `query.v1` JSON queries, including nested relations and variables |
| `agility_create_asset` | Create with scalar attributes and relationship changes; requires writes |
| `agility_update_asset` | Change scalar attributes and relationships; requires writes |
| `agility_execute_operation` | Lifecycle operations; requires writes and a nonempty exact operation allowlist |

The first nine tools are always registered. Creation and update tools are only registered with writes enabled. The operation tool's schema contains only explicitly allowed operation names. To permit deletion, an operator must deliberately include `Delete` in `allowOperations`. Disabling writes removes the write tools entirely; the API client also enforces the settings.

Generic tools support your instance's standard and custom asset types instead of assuming a particular workflow. Common internal names: project = `Scope`, sprint = `Timebox`, backlog item = `Story`, defect = `Defect`, portfolio item = `Epic`, user = `Member`. Discover actual attributes and valid state values through metadata and queries. OIDs such as `Story:1234` differ from display numbers such as `S-01234`.

Example: search a project's stories:

```json
{
  "assetType": "Story",
  "select": ["Number", "Name", "Estimate", "Status.Name", "Owners.Name"],
  "where": "Scope='Scope:1000'",
  "sort": ["Number"],
  "pageSize": 100,
  "start": 0
}
```

REST list results contain `assets`, optional API `total`, `pageSize`, `start`, and `nextStart`. Pass `nextStart` as `start` until it is null. `nextStart` may require one final empty request if the API does not supply a total. Other read tools return normalized assets or API metadata. Attribute values remain strings as supplied by Agility XML; related OIDs appear under `relations`.

### Parent IDs and names on item results

Portfolio and backlog item reads automatically add `Super`, `Super.Name`, and `Super.Number` to the requested fields, even when you select only `Name`. This applies to `Epic`, `Story`, `Defect`, `TestSet`, and the `Workitem`/`PrimaryWorkitem` collections. List, get, history, and `query.v1` responses expose:

```json
{
  "oid": "Story:1234",
  "parentId": "Epic:1000",
  "parentName": "Checkout experience",
  "parentNumber": "E-01000",
  "parentStatus": "resolved",
  "parent": {
    "oid": "Epic:1000",
    "id": "Epic:1000",
    "name": "Checkout experience",
    "number": "E-01000",
    "relation": "Super"
  }
}
```

Parent IDs are internal OIDs; the display number is separate. `parentStatus` distinguishes `resolved`, `none` (an explicitly empty relationship), `unresolved` (a parent OID is known but its name is unavailable), and `unavailable` (the API did not supply sufficient relationship data). Missing information is never represented as a known root. Selecting an historical item uses its historical projections; parent enrichment does not fetch current names for a historical read.

Agility's portfolio hierarchy is `Super`/`Subs`. Its `Parent` relation can describe a theme or another model-specific association and is preserved separately. `query.v1` retains its array-of-arrays format and adds the normalized fields to item rows, including nested selections using `Subs`, `SubsAndDown`, `Dependencies`, `Dependants`, or `Workitems`. Those nested results are still subject to Agility's lack of nested paging. Relation-only references outside these recognized selections can have `parentStatus: unavailable`.

Create/update responses often contain only modified fields. After a successful write that returns one item, the client performs a separate read to resolve its parent. If that read fails, the write remains successful and `hierarchyWarnings` explains the missing enrichment. Do not repeat a successful write to repair its hierarchy output. Operation responses and new-item templates normalize whatever hierarchy data the API returns; lifecycle operations such as Delete can make the item unavailable for further reading.

### Detailed item and dependency graph

Call `agility_get_item_detail` with a current OID:

```json
{
  "oid": "Story:1234",
  "hierarchyDepth": 3,
  "dependencyDepth": 2,
  "pageSize": 50,
  "maxNodes": 150,
  "maxEdges": 750,
  "maxRequests": 100,
  "maxDurationMs": 30000,
  "includeMetadata": true
}
```

The result includes:

- `item`: description, estimates, lifecycle/date fields, common single-valued context relations, custom scalar fields, and normalized parent information. Only fields found in instance metadata are selected. An explicit `select` replaces the default detail selection; parent fields are still added. Binary fields are excluded from defaults. Use `agility_related_items` for multivalue context such as Owners.
- `metadata`: field definitions, required/read-only/custom/multivalue flags, related asset types, reciprocal relation names, supported operations, and inheritance. The reader handles singleton XML definitions and inherited fields, with an operation-local cache and an inheritance limit. `includeMetadata: false` suppresses returned schema data; discovery still uses metadata.
- `hierarchy.parent`, `hierarchy.ancestors`, and `hierarchy.descendants`: item OIDs, names, numbers, parent data, and shortest-path `distance` from the root. Ancestors and descendants are traversed separately, so siblings are not mistaken for descendants.
- `dependencies.upstream`: prerequisites needed by the requested item; `dependencies.downstream`: items that depend on the requested item. `distance: 1` is direct; greater values are transitive. `blockingIssues` is separate from delivery dependencies.
- `impact.affected`, `impact.directDependants`, and `impact.blockingChains`: affected downstream items and prerequisite paths. Every path is an ordered list of OIDs backed by explicit dependency edges in `graph.edges`. `impact.cycles` identifies dependency loops; `impact.completeWithinRequestedDepth` follows the traversal coverage flag. Use larger `dependencyDepth` to see farther along a chain.
- `graph.nodes`, `graph.edges`, and `graph.cycles`: deduplicated nodes and directed edges. Hierarchy edges point **parent → child**; dependency edges point **prerequisite → dependent**. True cycles are reported, while shared dependencies in a diamond are not treated as cycles. At most 25 detected cycles per edge kind are reported.
- `coverage`, `warnings`, and `traversal`: explicit availability, errors, paging, pending work, and the limits used. Always inspect these before treating the result as complete.

The reader recognizes instance-supported `Dependencies`/`Dependants` fields (and the explicit upstream/downstream naming variants when present). It verifies the relationship target and reciprocal in metadata, then queries the target collection with a reciprocal filter. This provides actual root-level API paging instead of downloading an unlimited nested relationship array. Missing or unverifiable metadata produces `unsupported` or `unavailable` coverage; permission/network failures produce `error` coverage. An empty result never silently stands in for a failed relationship query.

| Detail option | Default | Range |
| --- | --- | --- |
| `hierarchyDepth` | 2 | 0–10; 0 disables hierarchy traversal |
| `dependencyDepth` | 1 | 0–10; 0 disables dependency traversal |
| `pageSize` | 50 | 1–200 |
| `maxNodes` | 100 | 1–500, including the root |
| `maxEdges` | 500 | 1–5000 |
| `maxRequests` | 80 | 5–300 HTTP attempts, including metadata and retries |
| `maxDurationMs` | 30000 | 1000–120000 across the entire operation |

The traversal measures the complete serialized detail result against `maxResponseBytes` as it grows; the existing HTTP and tool result size limits remain enforced. A limit returns partial data with `traversal.truncated: true`, reasons, pending work, and resumable relationship pages. If metadata alone pushes the final result over the limit, it is omitted with `metadataStatus: omitted_for_size`. A root item or traversal bookkeeping that cannot fit returns an explicit size error; reduce `select` or raise the configured limit. A deadline reached before the root can be loaded returns a tool error. `completeWithinRequestedDepth` describes completion within the selected depths, not the entire connected graph. `depthBoundaries` lists nodes that were returned but not expanded farther.

For example, resume a partially fetched upstream dependency collection with `agility_related_items`:

```json
{
  "oid": "Story:1234",
  "relation": "Dependencies",
  "pageSize": 50,
  "start": 50
}
```

The response includes `nextStart` and a ready-to-use `continuation` object with the next tool arguments. The same tool supports `Super`, `Subs`, `Dependants`, `BlockingIssues`, and other metadata-verified reciprocal relationships. A standalone related-items call verifies the source item is visible before querying its related collection.

Graphs reflect assets visible to the authenticated account. Reads are live and nontransactional; concurrent edits can change relationships while paging. Repeated rows or changed totals are flagged as `pagination_changed`. Dependency traversal follows explicit relationships; it does not invent inherited scheduling constraints between descendants of portfolio dependencies. Query ancestors and inspect their dependencies separately when analyzing those constraints.

Example: parameterized query with nested owners:

```json
{
  "queries": [{
    "from": "Story",
    "select": ["Number", "Name", { "from": "Owners", "select": ["Name"] }],
    "filter": ["Scope=$project"],
    "with": { "$project": "Scope:1000" },
    "page": { "size": 50, "start": 0 }
  }]
}
```

Query results preserve the API array-of-arrays format. Every root query receives a default page of 100, capped at 1000. Page nested relationships by querying the related asset type separately; Agility does not support paging nested selections. Nested selections are limited to three levels. Batch size, nesting, and response bytes are bounded; these limits do not impose a server-side query execution budget on Agility.

Example: create a story:

```json
{
  "assetType": "Story",
  "attributes": { "Name": "Add audit trail", "Estimate": 3 },
  "relations": [{ "name": "Scope", "action": "set", "targets": ["Scope:1000"] }]
}
```

Example: update owners without replacing the multivalue relationship:

```json
{
  "oid": "Story:1234",
  "attributes": { "Description": "Updated description" },
  "relations": [
    { "name": "Owners", "action": "add", "targets": ["Member:20"] },
    { "name": "Owners", "action": "remove", "targets": ["Member:21"] }
  ]
}
```

`set` changes a single-valued relation and accepts zero or one target; an empty target list clears it. Use `add`/`remove` for multivalue relations. `null` writes an empty scalar value. Updates are partial, last-writer-wins changes without an optimistic concurrency guarantee. Read the asset first and verify it afterward when concurrent edits matter. Historical moment OIDs are rejected for mutations.

## Reliability and security behavior

- Credentials are loaded for each authenticated request, so rotation takes effect without restarting. Credential secrets necessarily exist briefly in process memory and the helper's pipe; they are never passed as process arguments or written to project files. Credential Manager protects storage, not processes already running as your Windows user.
- Only fixed Agility endpoints under the configured base URL can be called. MCP tools cannot change the host, credential target, authentication mode, or HTTP method. Metadata is requested anonymously.
- Redirects are blocked, including same-origin redirects. This prevents accidental credential forwarding and exposes incorrect instance paths or login redirects as errors.
- Reads retry transport failures before a response and HTTP 429/502/503/504, with bounded exponential backoff/jitter or `Retry-After` (capped at 30 seconds). `query.v1` POST is read-only and eligible for retries. Authentication, authorization, validation, and XML/JSON errors are not retried. Timeouts and failed response streams are not retried.
- Creates, updates, and lifecycle operations are never retried automatically. A network failure can leave a write outcome unknown; check the asset before repeating it. There are no multi-asset transactions, bulk write tools, or automatic rollback.
- Per-attempt timeouts include response streaming. MCP cancellation propagates to credential retrieval, HTTP requests, and retry waits. Credential retrieval has its own configured timeout and a 20-second helper safety cap. At most 64 requests can wait behind the configured concurrency limit. Each HTTP request body is capped at 1 MiB; responses are bounded while streaming.
- XML values are escaped, identifiers validated, and DTD/entity declarations rejected. Server error bodies are not returned to the model or logs. Tool failures return MCP `isError` without crashing the connection.
- Tool annotations identify read and destructive behavior. Agility's own permissions remain authoritative. Treat descriptions, comments, and other retrieved asset content as untrusted text.

## Validation and development

```powershell
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
uv run pytest integration/test_windows_credentials.py -q
uv build
```

Windows CI runs Python 3.12 and 3.14, Ruff, type checks, unit/contract tests, synthetic vault integration, wheel/sdist builds, packaged uvx launches, and installation from the exact Git commit. No publishing step is configured.

The normal suite uses synthetic HTTP responses, captured JavaScript schemas and semantic outputs, FastMCP in-memory sessions, cancellation tests, and a real stdio subprocess against a local HTTP fixture. The Windows integration tests create a unique synthetic Generic Credential, verify Unicode/rotation, authenticate a local stdio request, test worker cancellation, and delete the credential in `finally`. They never access existing tenant credentials. See [baseline provenance](tests/fixtures/README.md).

To validate a real tenant, store its credential under your Windows user, then opt in to the read-only test:

```powershell
$env:AGILITY_BASE_URL = 'https://your-server/YourAgilityInstance'
$env:AGILITY_LIVE_ITEM_OID = 'Story:1234'
$env:AGILITY_LIVE_TEST = '1'
uv run pytest integration/test_live_tenant.py -q -s
```

Set `AGILITY_LIVE_EXPECT_PARENT_OID`, `AGILITY_LIVE_EXPECT_UPSTREAM_OID`, or `AGILITY_LIVE_EXPECT_DOWNSTREAM_OID` to verify known relationships. `AGILITY_LIVE_RELATION` checks an additional paged relation. The test reads metadata, the item, and its bounded detail graph; it never changes Agility data. It reports incomplete traversal reasons when permissions or deployment metadata prevent discovery.

No live Agility tenant is bundled. Run `doctor` against your instance to verify its token, endpoint choice, permissions, certificates, and deployment-specific behavior. Metadata and workflows differ across versions and installations. Attachment uploads/downloads, ActivityStream, tags endpoints, full administrative workflows, HTTP hosting, and automatic token grant creation are outside this version's scope.

## API references

- [Agility API authentication methods](https://docs.digital.ai/agility/docs/developerlibrary/api-authentication-methods)
- [Bearer access tokens and creating grants](https://docs.digital.ai/agility/docs/developerlibrary/access-token-authentication)
- [Data API](https://docs.digital.ai/agility/docs/developerlibrary/data-api)
- [Updating scalar values and relationships](https://docs.digital.ai/agility/docs/developerlibrary/update-asset)
- [Tour of query.v1](https://docs.digital.ai/agility/docs/developerlibrary/tour-query-v-1)
- [Meta API](https://docs.digital.ai/agility/docs/developerlibrary/meta-api)
- [History queries and pagination](https://docs.digital.ai/agility/docs/developerlibrary/learn-by-example-history-queries)
- [Portfolio hierarchy and parent epics](https://docs.digital.ai/agility/docs/developerlibrary/determine-parent-epics-with-api)
- [Portfolio children and descendants](https://docs.digital.ai/agility/docs/developerlibrary/query-for-progress-of-epic)
- [Relationship metadata and reciprocals](https://docs.digital.ai/agility/docs/developerlibrary/retrieve-metadata-for-all-system-assets)
- [Managing upstream and downstream dependencies](https://docs.digital.ai/agility/docs/agility/adding-portfolio-item-dependencies)
- [Standalone FastMCP tools and ToolResult](https://gofastmcp.com/servers/tools)
- [uv documentation](https://docs.astral.sh/uv/)
- [Windows CREDENTIALW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)

This is an independent integration, not an official Digital.ai product.
