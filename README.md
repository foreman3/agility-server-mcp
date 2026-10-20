# Digital.ai Agility MCP for Windows

A JavaScript MCP server for Digital.ai Agility, formerly VersionOne. Runs on Node.js over stdio and ships as an `npx` executable. Authentication comes exclusively from **Windows Credential Manager**, using the current Windows user's Generic Credentials.

Requires Windows 10/11 or Windows Server, Node.js 22+, Windows PowerShell 5.1, and an Agility instance. No native Node addons, compilation step, `keytar`, or separately installed PowerShell modules. The bundled PowerShell helper calls `CredReadW`, `CredWriteW`, and `CredDeleteW` in the Windows API through a small in-memory C# interop declaration. Application-control policies must permit the helper and `Add-Type`; the server does not change execution policy.

## Quick start from this checkout

```powershell
npm ci
$env:AGILITY_BASE_URL = 'https://your-server/YourAgilityInstance'
node .\bin\agility-mcp.js credentials set
node .\bin\agility-mcp.js doctor
```

`credentials set` prompts for the access token **without echoing it**. Create the token in Agility's Member Applications/grant UI first; this server does not create grants. A new invocation overwrites the same saved credential, allowing token rotation. The default credential target is derived from the normalized instance URL, so different instances use different entries. Keep the instance path and its case consistent between setup and server configuration.

```powershell
node .\bin\agility-mcp.js credentials status
node .\bin\agility-mcp.js credentials delete
```

The helper stores a UTF-16 Generic Credential, persisted for the current user on this machine. You can also add it in Control Panel → Credential Manager → Windows Credentials → Add a generic credential. Use the exact target reported by `credentials status` (or set `AGILITY_CREDENTIAL_TARGET` explicitly), an arbitrary username for token auth, and the access token as the password. Basic auth uses the actual Agility username and password. Credential Manager limits secrets to 2,560 bytes: 1,280 UTF-16 code units.

Run the MCP host under the **same Windows user** as credential setup. A Windows service or scheduled task has its own logon/session requirements. A Linux, WSL, or remote-hosted MCP process cannot read this user's Windows vault.

## Run with npx

The checkout is ready to package. To create an archive and run it locally:

```powershell
npm pack
npx --yes --package .\agility-wincred-mcp-0.3.0.tgz agility-mcp --version
npx --yes --package .\agility-wincred-mcp-0.3.0.tgz agility-mcp credentials set --base-url https://your-server/YourAgilityInstance
npx --yes --package .\agility-wincred-mcp-0.3.0.tgz agility-mcp doctor --base-url https://your-server/YourAgilityInstance
```

The package is **not published to npm by this project setup**. Choose an available package name or your organization's npm scope before publishing. After you publish version `0.3.0`, the following form is available (replace the name if you rename it):

```powershell
npx --yes agility-wincred-mcp@0.3.0 credentials set --base-url https://your-server/YourAgilityInstance
npx --yes agility-wincred-mcp@0.3.0 serve --base-url https://your-server/YourAgilityInstance
```

Pin an exact version in your MCP configuration. A global installation is also supported with `npm install -g <package-or-tarball>` followed by `agility-mcp`.

## MCP host configuration

For a locally packed archive, a Windows MCP host that uses `mcpServers` can launch:

```json
{
  "mcpServers": {
    "agility": {
      "command": "cmd.exe",
      "args": ["/d", "/c", "npx", "--yes", "--package", "C:\\path\\to\\agility-wincred-mcp-0.3.0.tgz", "agility-mcp", "serve"],
      "env": {
        "AGILITY_BASE_URL": "https://your-server/YourAgilityInstance"
      }
    }
  }
}
```

`cmd.exe /d /c` runs the Windows `npx.cmd` shim; hosts that support launching `npx` directly may use that instead. The `--package` argument must be an absolute path visible to the MCP host. After publication, use `args: ["/d", "/c", "npx", "--yes", "agility-wincred-mcp@0.3.0", "serve"]` instead. For source development, use `command: "node"` and `args: ["C:\\path\\to\\bin\\agility-mcp.js", "serve"]`.

No token or password belongs in this JSON. Normal server output on stdout contains only MCP protocol messages; CLI diagnostics go to stderr.

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

Use the application base URL, not `/Default.aspx`, `/Account.mvc/Login`, or an API endpoint URL. HTTPS is required unless explicitly allowing HTTP. For enterprise certificate authorities, launch Node with `NODE_EXTRA_CA_CERTS` pointing to your trusted PEM certificate bundle. Never disable TLS verification. Proxy routing can be supplied using the selected Node version's supported proxy configuration; the server does not implement proxy authentication or a custom dispatcher.

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
- Per-attempt timeouts include response streaming. MCP cancellation propagates to credential retrieval, fetch, and retry waits. Credential retrieval has its own configured timeout and a 20-second helper safety cap. At most 64 requests can wait behind the configured concurrency limit. Each HTTP request body is capped at 1 MiB; responses are bounded while streaming.
- XML values are escaped, identifiers validated, and DTD/entity declarations rejected. Server error bodies are not returned to the model or logs. Tool failures return MCP `isError` without crashing the connection.
- Tool annotations identify read and destructive behavior. Agility's own permissions remain authoritative. Treat descriptions, comments, and other retrieved asset content as untrusted text.

## Validation and development

```powershell
npm ci
npm run check
npm test
npm run test:windows
npm pack --dry-run
```

The Windows GitHub Actions workflow runs these checks and launches the packed archive through `npx`. The normal suite uses fixture responses, in-memory MCP client/server sessions, and a local HTTP fixture with a real stdio child process. The Windows integration test creates a unique **synthetic** Generic Credential, verifies Unicode and rotation, and removes it in `finally`; it does not access existing Agility credentials. Restricted execution environments may need permission to run PowerShell, bind a local test socket, or access Credential Manager.

To validate a real tenant, store its credential under your Windows user, then opt in to the read-only test:

```powershell
$env:AGILITY_BASE_URL = 'https://your-server/YourAgilityInstance'
$env:AGILITY_LIVE_ITEM_OID = 'Story:1234'
$env:AGILITY_LIVE_TEST = '1'
npm run test:live
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
- [Official MCP JavaScript/TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk)
- [Windows CREDENTIALW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)

This is an independent integration, not an official Digital.ai product.
