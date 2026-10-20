import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer as createHttpServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { Client, InMemoryTransport } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { loadConfig } from '../src/config.js';
import { createServer } from '../src/server.js';

async function connect(options = {}, fakeClient = {}) {
  const config = await loadConfig({ baseUrl: 'https://example.test/v1', ...options }, {});
  const server = createServer(config, { store: { status: async () => ({ found: true }) }, client: fakeClient });
  const client = new Client({ name: 'agility-test', version: '1.0.0' });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  return { client, close: async () => { await client.close(); await server.close(); } };
}
test('MCP handshake, tool discovery and strict argument validation', async () => {
  let called = false;
  const session = await connect({}, { list: async args => { called = true; assert.equal(args.pageSize, 100); return { assets: [], nextStart: null }; } });
  try {
    const tools = (await session.client.listTools()).tools;
    assert.equal(tools.length, 9);
    assert.ok(tools.every(tool => tool.annotations.readOnlyHint));
    assert.ok(tools.every(tool => !tool.name.includes('update') && !tool.name.includes('create')));
    const status = await session.client.callTool({ name: 'agility_status', arguments: {} });
    assert.equal(status.structuredContent.data.credential.found, true);
    const invalid = await session.client.callTool({ name: 'agility_list_assets', arguments: { assetType: 'Story', pageSize: 1001 } });
    assert.equal(invalid.isError, true);
    assert.equal(called, false);
    const unknown = await session.client.callTool({ name: 'agility_list_assets', arguments: { assetType: 'Story', op: 'Delete' } });
    assert.equal(unknown.isError, true);
    const valid = await session.client.callTool({ name: 'agility_list_assets', arguments: { assetType: 'Story' } });
    assert.equal(valid.isError, undefined);
    assert.deepEqual(valid.structuredContent.data.assets, []);
    assert.equal(called, true);
  } finally { await session.close(); }
});
test('MCP write tools only registered when enabled, with operation enum restricted to allowlist', async () => {
  const session = await connect({ enableWrites: true, allowOperations: ['Close'] }, { operation: async args => ({ operation: args.operation }) });
  try {
    const tools = (await session.client.listTools()).tools;
    assert.equal(tools.length, 12);
    const operation = tools.find(tool => tool.name === 'agility_execute_operation');
    assert.equal(operation.annotations.destructiveHint, true);
    const forbidden = await session.client.callTool({ name: operation.name, arguments: { oid: 'Story:1', operation: 'Delete' } });
    assert.equal(forbidden.isError, true);
    const valid = await session.client.callTool({ name: operation.name, arguments: { oid: 'Story:1', operation: 'Close' } });
    assert.equal(valid.structuredContent.data.operation, 'Close');
    const emptyRelation = await session.client.callTool({ name: 'agility_update_asset', arguments: { oid: 'Story:1', relations: [{ name: 'Scope', action: 'set', targets: ['Scope:1', 'Scope:2'] }] } });
    assert.equal(emptyRelation.isError, true);
  } finally { await session.close(); }
});
test('MCP query schemas insert page bounds and restrict raw endpoint/operation injection', async () => {
  const session = await connect({}, { query: async args => ({ queries: args.queries }) });
  try {
    const result = await session.client.callTool({ name: 'agility_query', arguments: { queries: [{ from: 'Story', select: ['Name', { from: 'Owners', select: ['Name'] }] }] } });
    assert.deepEqual(result.structuredContent.data.queries[0].page, { size: 100, start: 0 });
    const bad = await session.client.callTool({ name: 'agility_query', arguments: { queries: [{ from: 'Story', op: 'Delete' }] } });
    assert.equal(bad.isError, true);
  } finally { await session.close(); }
});
test('tool failures return isError rather than breaking the MCP connection', async () => {
  const session = await connect({}, { get: async () => { throw new Error('Agility HTTP 401. Check credential.'); } });
  try {
    const failure = await session.client.callTool({ name: 'agility_get_asset', arguments: { oid: 'Story:1' } });
    assert.equal(failure.isError, true);
    assert.match(failure.content[0].text, /401/);
    assert.equal((await session.client.listTools()).tools.length, 9);
  } finally { await session.close(); }
});
test('MCP detailed-item and continuation tools validate graph limits and pass defaults', async () => {
  const session = await connect({}, {
    detail: async args => { assert.equal(args.maxNodes, 100); assert.equal(args.dependencyDepth, 1); return { item: { oid: args.oid }, traversal: { truncated: false } }; },
    related: async args => ({ relation: args.relation, start: args.start }),
  });
  try {
    const invalid = await session.client.callTool({ name: 'agility_get_item_detail', arguments: { oid: 'Story:10', maxNodes: 501 } });
    assert.equal(invalid.isError, true);
    const moment = await session.client.callTool({ name: 'agility_get_item_detail', arguments: { oid: 'Story:10:11' } });
    assert.equal(moment.isError, true);
    const valid = await session.client.callTool({ name: 'agility_get_item_detail', arguments: { oid: 'Story:10' } });
    assert.equal(valid.structuredContent.data.item.oid, 'Story:10');
    const continuation = await session.client.callTool({ name: 'agility_related_items', arguments: { oid: 'Story:10', relation: 'Dependencies', start: 50 } });
    assert.equal(continuation.structuredContent.data.start, 50);
  } finally { await session.close(); }
});
test('real CLI process speaks MCP over stdio and fetches metadata from local fixture', { skip: process.platform !== 'win32', timeout: 15000 }, async () => {
  const http = createHttpServer((request, response) => {
    assert.equal(request.url, '/instance/meta.v1/Story');
    assert.equal(request.headers.authorization, undefined);
    response.setHeader('Content-Type', 'application/xml');
    response.end('<AssetType name="Story"><AttributeDefinition name="Name"/></AssetType>');
  });
  await new Promise(resolve => http.listen(0, '127.0.0.1', resolve));
  const client = new Client({ name: 'stdio-test', version: '1.0.0' });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [fileURLToPath(new URL('../bin/agility-mcp.js', import.meta.url)), 'serve', '--allow-http', '--base-url', `http://127.0.0.1:${http.address().port}/instance`],
    stderr: 'pipe',
    env: Object.fromEntries(Object.entries(process.env).filter(([key, value]) => !key.startsWith('AGILITY_') && value !== undefined)),
  });
  let stderr = '';
  transport.stderr?.on('data', chunk => { stderr += chunk.toString(); });
  try {
    await client.connect(transport);
    assert.equal((await client.listTools()).tools.length, 9);
    const result = await client.callTool({ name: 'agility_metadata', arguments: { assetType: 'Story' } });
    assert.equal(result.isError, undefined, JSON.stringify(result));
    assert.equal(result.structuredContent.data.AssetType['@name'], 'Story');
    assert.equal(stderr, '');
  } finally { await client.close(); await new Promise(resolve => http.close(resolve)); }
});
