import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { WindowsCredentialStore } from '../src/credentials.js';
import { AgilityClient } from '../src/client.js';
import { loadConfig } from '../src/config.js';

test('credential helper is killed promptly when its request is cancelled', async () => {
  let child;
  let kills = 0;
  const store = new WindowsCredentialStore({ platform: 'win32', spawnProcess: () => {
    child = new EventEmitter();
    child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
    child.kill = () => { kills++; queueMicrotask(() => child.emit('close', null)); };
    return child;
  } });
  const controller = new AbortController();
  const pending = store.get('test-only', controller.signal);
  controller.abort();
  await assert.rejects(pending, /cancelled/);
  assert.equal(kills, 1);
  const already = new AbortController(); already.abort();
  await assert.rejects(store.get('test-only', already.signal), /aborted/);
  assert.equal(kills, 1);
});

test('Agility request stops before HTTP when credential retrieval is cancelled', async () => {
  const config = await loadConfig({ baseUrl: 'https://example.test/Agility' }, {});
  let fetched = false;
  const store = { get: async (_target, signal) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  }) };
  const client = new AgilityClient(config, store, { fetchImpl: async () => { fetched = true; throw new Error('unexpected'); } });
  const controller = new AbortController();
  const pending = client.get({ oid: 'Story:1', signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, /cancelled/);
  assert.equal(fetched, false);
});
