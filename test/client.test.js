import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadConfig } from '../src/config.js';
import { AgilityClient } from '../src/client.js';
import { authorization, WindowsCredentialStore } from '../src/credentials.js';

const xml = '<Assets total="2"><Asset id="Story:1"><Attribute name="Name">First</Attribute></Asset></Assets>';
const config = overrides => loadConfig({ baseUrl: 'https://example.test/Agility', retries: 0, ...overrides }, {});
const store = { get: async () => ({ username: 'demo', secret: 'test-secret' }) };

test('uses Credential Manager bearer auth and correctly encodes path, filters and pagination', async () => {
  const client = new AgilityClient(await config(), store, { fetchImpl: async (url, options) => {
    assert.equal(url.pathname, '/Agility/rest-1.v1/Data/Story');
    assert.equal(url.searchParams.get('page'), '1,0');
    assert.equal(url.searchParams.get('where'), "Name='A&B'");
    assert.equal(options.headers.Authorization, 'Bearer test-secret');
    assert.equal(options.redirect, 'manual');
    return new Response(xml);
  } });
  const result = await client.list({ assetType: 'Story', where: "Name='A&B'", pageSize: 1 });
  assert.equal(result.nextStart, 1);
});
test('continues a short page when Agility reports more rows and flags an empty stalled page', async () => {
  const responses = [
    '<Assets total="3"><Asset id="Story:1"/></Assets>',
    '<Assets total="3"><Asset id="Story:2"/></Assets>',
    '<Assets total="3"/>',
  ];
  const client = new AgilityClient(await config(), store, { fetchImpl: async () => new Response(responses.shift()) });
  const first = await client.list({ assetType: 'Story', pageSize: 50 });
  assert.equal(first.nextStart, 1);
  const second = await client.list({ assetType: 'Story', pageSize: 50, start: first.nextStart });
  assert.equal(second.nextStart, 2);
  const stalled = await client.list({ assetType: 'Story', pageSize: 50, start: second.nextStart });
  assert.equal(stalled.nextStart, null);
  assert.equal(stalled.paginationStatus, 'stalled');
});
test('continues a short page without a total until an empty page confirms completion', async () => {
  const responses = ['<Assets><Asset id="Story:1"/></Assets>', '<Assets/>'];
  const client = new AgilityClient(await config(), store, { fetchImpl: async () => new Response(responses.shift()) });
  const first = await client.list({ assetType: 'Story', pageSize: 50 });
  assert.equal(first.nextStart, 1);
  const last = await client.list({ assetType: 'Story', pageSize: 50, start: first.nextStart });
  assert.equal(last.nextStart, null);
});
test('reads credential anew for each request and supports basic auth', async () => {
  let reads = 0;
  const counted = { get: async () => { reads++; return { username: 'demo', secret: 'test-secret' }; } };
  const client = new AgilityClient(await config(), counted, { fetchImpl: async () => new Response(xml) });
  await client.list({ assetType: 'Story' }); await client.list({ assetType: 'Story' });
  assert.equal(reads, 2);
  assert.equal(await authorization(store, await config({ authMode: 'basic' })), 'Basic ' + Buffer.from('demo:test-secret').toString('base64'));
  await assert.rejects(authorization({ get: async () => ({ username: '', secret: 'x\ny' }) }, await config()));
  await assert.rejects(new WindowsCredentialStore({ platform: 'linux' }).get('target'), /Windows/);
});
test('metadata is anonymous and preserves instance path', async () => {
  const client = new AgilityClient(await config(), { get: () => assert.fail('must not read credentials') }, { fetchImpl: async (url, options) => {
    assert.equal(url.pathname, '/Agility/meta.v1/Story/Name');
    assert.equal(options.headers.Authorization, undefined);
    return new Response('<AssetType name="Story"/>');
  } });
  assert.equal((await client.metadata({ assetType: 'Story', member: 'Name' })).AssetType['@name'], 'Story');
});
test('read-only query POSTs can retry 429 responses', async () => {
  let calls = 0;
  const client = new AgilityClient(await config({ retries: 1 }), store, { fetchImpl: async (url, options) => {
    calls++;
    assert.equal(url.pathname, '/Agility/query.v1');
    assert.equal(options.method, 'POST');
    return calls === 1 ? new Response('', { status: 429, headers: { 'retry-after': '0' } }) : Response.json([[{ _oid: 'Story:1' }]]);
  } });
  const result = await client.query({ queries: [{ from: 'Story', page: { size: 1, start: 0 } }] });
  assert.equal(calls, 2);
  assert.equal(result[0][0]._oid, 'Story:1');
});
test('mutations and lifecycle operations never retry; allowlist checked in client', async () => {
  let calls = 0;
  const client = new AgilityClient(await config({ enableWrites: true, allowOperations: ['Close'], retries: 5 }), store, { fetchImpl: async (url, options) => {
    calls++;
    assert.equal(options.method, 'POST');
    return new Response('do not disclose test-secret', { status: 503 });
  } });
  await assert.rejects(client.mutate({ assetType: 'Story', attributes: { Name: 'Hello' } }), error => !error.message.includes('test-secret') && /503/.test(error.message));
  assert.equal(calls, 1);
  await assert.rejects(client.operation({ oid: 'Story:1', operation: 'Delete' }), /allowOperations/);
  assert.equal(calls, 1);
  const disabled = new AgilityClient(await config(), store);
  await assert.rejects(disabled.mutate({ assetType: 'Story', attributes: { Name: 'x' } }), /disabled/);
});
test('successfully creates XML assets and executes allowed operations with OAuth endpoint', async () => {
  const seen = [];
  const client = new AgilityClient(await config({ enableWrites: true, allowOperations: ['Close'], restEndpoint: 'rest-1.oauth.v1' }), store, { fetchImpl: async (url, options) => {
    seen.push({ url, options });
    return new Response('<Asset id="Story:5:8"><Attribute name="Name">Created</Attribute></Asset>');
  } });
  const result = await client.mutate({ assetType: 'Story', attributes: { Name: 'Created' }, relations: [{ name: 'Scope', action: 'set', targets: ['Scope:0'] }] });
  assert.equal(result.assets[0].oid, 'Story:5:8');
  assert.equal(seen[0].url.pathname, '/Agility/rest-1.oauth.v1/Data/Story');
  assert.match(seen[0].options.body, /<Relation name="Scope" act="set">/);
  await client.operation({ oid: 'Story:5', operation: 'Close' });
  assert.equal(seen.at(-1).url.searchParams.get('op'), 'Close');
});
test('blocks redirects and suppresses server error bodies', async () => {
  for (const status of [302, 401, 403, 404, 500]) {
    const client = new AgilityClient(await config(), store, { fetchImpl: async () => new Response('test-secret internal details', { status }) });
    await assert.rejects(client.get({ oid: 'Story:1' }), error => error.status === status && !error.message.includes('test-secret'));
  }
});
test('bounds streamed response bytes, validates JSON, and caps request bodies', async () => {
  const client = new AgilityClient(await config({ maxResponseBytes: 1024 }), store, { fetchImpl: async () => new Response('x'.repeat(1025)) });
  await assert.rejects(client.get({ oid: 'Story:1' }), /exceeds/);
  const invalid = new AgilityClient(await config(), store, { fetchImpl: async () => new Response('<html>login</html>') });
  await assert.rejects(invalid.query({ queries: [] }), /invalid JSON/);
  await assert.rejects(client.query({ queries: [{ x: 'x'.repeat(1024 * 1024) }] }), /Request body/);
});
test('propagates timeout and cancellation without disclosing transport errors', async () => {
  const fetchImpl = async (url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true });
  });
  const client = new AgilityClient(await config({ timeoutMs: 100 }), store, { fetchImpl });
  // Mock fetch does not hold a socket, so keep the test alive until AbortSignal.timeout fires.
  const keepAlive = setTimeout(() => {}, 1000);
  try { await assert.rejects(client.get({ oid: 'Story:1' }), /timed out/); } finally { clearTimeout(keepAlive); }
  const controller = new AbortController();
  const request = client.get({ oid: 'Story:1', signal: controller.signal });
  setImmediate(() => controller.abort());
  await assert.rejects(request, /cancelled/);
});
test('concurrency limit bounds simultaneous fetches', async () => {
  let active = 0, maximum = 0;
  const client = new AgilityClient(await config({ maxConcurrency: 2 }), store, { fetchImpl: async () => {
    active++; maximum = Math.max(maximum, active);
    await new Promise(resolve => setTimeout(resolve, 10));
    active--;
    return new Response(xml);
  } });
  await Promise.all(Array.from({ length: 6 }, () => client.list({ assetType: 'Story' })));
  assert.equal(maximum, 2);
});

test('cancelling a queued request frees its queue entry without issuing an HTTP request', async () => {
  let release;
  let calls = 0;
  const client = new AgilityClient(await config({ maxConcurrency: 1 }), store, { fetchImpl: async () => {
    calls++;
    if (calls === 1) await new Promise(resolve => { release = resolve; });
    return new Response(xml);
  } });
  const first = client.list({ assetType: 'Story' });
  await new Promise(resolve => setImmediate(resolve));
  const controller = new AbortController();
  const queued = client.list({ assetType: 'Story', signal: controller.signal });
  controller.abort();
  await assert.rejects(queued, /cancelled/);
  release();
  await first;
  await client.list({ assetType: 'Story' });
  assert.equal(calls, 2);
});

test('ambiguous mutation responses instruct the caller to verify before retrying', async () => {
  const client = new AgilityClient(await config({ enableWrites: true }), store, { fetchImpl: async () => new Response('<html>unexpected</html>') });
  await assert.rejects(client.mutate({ assetType: 'Story', attributes: { Name: 'Hello' } }), /Verify the asset before retrying/);
  const empty = new AgilityClient(await config({ enableWrites: true }), store, { fetchImpl: async () => new Response(null, { status: 204 }) });
  assert.deepEqual(await empty.mutate({ oid: 'Story:1', attributes: { Name: 'Hello' } }), { completed: true });
});
