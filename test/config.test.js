import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadConfig, parseArgs, normalizeBaseUrl } from '../src/config.js';

test('config normalizes URL, derives stable credential target, and has safe defaults', async () => {
  const a = await loadConfig({ baseUrl: 'https://EXAMPLE.test/Agility/' }, {});
  const b = await loadConfig({ baseUrl: 'https://example.test/Agility' }, {});
  assert.equal(a.credentialTarget, b.credentialTarget);
  assert.equal(a.enableWrites, false);
  assert.equal(a.authMode, 'token');
  assert.deepEqual(a.allowOperations, []);
  assert.notEqual(a.credentialTarget, (await loadConfig({ baseUrl: 'https://example.test/Other' }, {})).credentialTarget);
});
test('rejects unsafe URL configuration', () => {
  for (const url of ['http://example.test', 'https://user:secret@example.test', 'https://example.test/?token=x', 'https://example.test/#x', 'file:///tmp', 'https://example.test/a%2fb']) assert.throws(() => normalizeBaseUrl(url));
  assert.equal(normalizeBaseUrl('http://localhost:1234', true), 'http://localhost:1234');
});
test('CLI overrides env and validates configuration', async () => {
  const { options, positional } = parseArgs(['serve', '--base-url=https://example.test/v1', '--enable-writes', '--allow-operations', 'Close,Delete']);
  const config = await loadConfig(options, { AGILITY_BASE_URL: 'https://elsewhere.test', AGILITY_ENABLE_WRITES: 'false' });
  assert.deepEqual(positional, ['serve']);
  assert.equal(config.baseUrl, 'https://example.test/v1');
  assert.deepEqual(config.allowOperations, ['Close', 'Delete']);
  for (const options of [{ retries: -1 }, { maxConcurrency: 0 }, { enableWrites: 'yes' }, { allowOperations: 'Delete' }, { restEndpoint: '../bad' }]) await assert.rejects(loadConfig({ baseUrl: 'https://example.test', ...options }, {}));
  assert.throws(() => parseArgs(['--token', 'secret']));
  assert.throws(() => parseArgs(['--enable-writes=false']));
  assert.throws(() => parseArgs(['--base-url']));
});
