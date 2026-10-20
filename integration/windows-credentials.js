// Explicit opt-in integration test: only creates a unique synthetic credential.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { WindowsCredentialStore } from '../src/credentials.js';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
if (process.platform !== 'win32') throw new Error('Run this integration test on Windows.');
const target = `agility-mcp-test:${randomUUID()}`;
const store = new WindowsCredentialStore();
try {
  assert.equal((await store.status(target)).found, false);
  const secret = 'synthetic-only-✓-密码-🔒';
  await store.set(target, 'test-user', secret);
  assert.equal((await store.status(target)).found, true);
  assert.deepEqual(await store.get(target), { username: 'test-user', secret });
  await store.set(target, 'test-user', 'rotated-synthetic-token');
  assert.equal((await store.get(target)).secret, 'rotated-synthetic-token');
  const http = createServer((request, response) => {
    assert.equal(request.headers.authorization, 'Bearer rotated-synthetic-token');
    const url = new URL(request.url, 'http://localhost');
    assert.equal(url.pathname, '/instance/rest-1.v1/Data/Story');
    assert.equal(url.searchParams.get('sel'), 'Name,Super,Super.Name,Super.Number');
    response.end('<Assets total="1"><Asset id="Story:10"><Attribute name="Name">Vault authenticated</Attribute><Attribute name="Super.Name">Parent feature</Attribute><Attribute name="Super.Number">E-1</Attribute><Relation name="Super"><Asset idref="Epic:1"/></Relation></Asset></Assets>');
  });
  await new Promise(resolve => http.listen(0, '127.0.0.1', resolve));
  const client = new Client({ name: 'vault-integration', version: '1.0.0' });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [fileURLToPath(new URL('../bin/agility-mcp.js', import.meta.url)), 'serve', '--allow-http', '--base-url', `http://127.0.0.1:${http.address().port}/instance`, '--credential-target', target],
    env: Object.fromEntries(Object.entries(process.env).filter(([key, value]) => !key.startsWith('AGILITY_') && value !== undefined)),
    stderr: 'pipe',
  });
  try {
    await client.connect(transport);
    const result = await client.callTool({ name: 'agility_list_assets', arguments: { assetType: 'Story' } });
    assert.equal(result.isError, undefined, JSON.stringify(result));
    assert.equal(result.structuredContent.data.assets[0].attributes.Name, 'Vault authenticated');
    assert.equal(result.structuredContent.data.assets[0].parentId, 'Epic:1');
    assert.equal(result.structuredContent.data.assets[0].parentName, 'Parent feature');
    assert.ok(!JSON.stringify(result).includes('rotated-synthetic-token'));
  } finally { await client.close(); await new Promise(resolve => http.close(resolve)); }
  assert.equal((await store.delete(target)).deleted, true);
  assert.equal((await store.status(target)).found, false);
  await assert.rejects(store.get(target), /not found/);
  console.log('Windows Credential Manager round-trip, Unicode, rotation, authenticated MCP stdio request, and deletion passed.');
} finally { await store.delete(target); }
