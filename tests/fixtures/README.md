# JavaScript compatibility baseline

Captured from commit `1754972` (release 0.3.0) before deleting the JavaScript implementation.

- `tools.json`: actual MCP `tools/list` response with writes enabled and operations `Close,Delete`.
- `agility.json`: original synthetic metadata and graph data from `test/detail.test.js`.
- `detail-baseline.json`: original client responses for normal item detail, portfolio traversal, dependency cycles, and node/edge/request truncation. Only `traversal.elapsedMs` was removed.

Python tests compare semantic JSON, preserving array order and all fields. Public schemas, descriptions and annotations are compared through FastMCP's in-memory client. `src/agility_mcp/tool_contracts.json` freezes the public schemas separately from this regression fixture.

The original suite had 47 tests. Its baseline run passed 46; its local stdio HTTP fixture was inaccessible in the restricted environment. The Python replacement verifies both anonymous metadata and vault-authenticated requests through real stdio processes against local HTTP fixtures. Credential interoperability was verified with the original PowerShell worker in both directions using a unique synthetic Unicode credential, then deleted in `finally`.
