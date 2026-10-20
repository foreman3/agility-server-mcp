import { setTimeout as delay } from 'node:timers/promises';
import { authorization } from './credentials.js';
import { assertName, oidPath, assetXml, parseXml, parseAssets } from './xml.js';
import { itemSelection, withParent, prepareQueries, normalizeQueryItems, canonicalOid, isItemType } from './items.js';
import { getItemDetail, relatedItems } from './detail.js';

export class AgilityError extends Error {
  constructor(message, status) { super(message); this.name = 'AgilityError'; this.status = status; }
}
class Limiter {
  active = 0;
  queue = [];
  constructor(max) { this.max = max; }
  async run(fn, signal) {
    signal?.throwIfAborted();
    if (this.active >= this.max) {
      if (this.queue.length >= 64) throw new AgilityError('Too many pending Agility requests. Try again after current requests finish.');
      await new Promise((resolve, reject) => {
        const entry = { resolve: () => { signal?.removeEventListener('abort', abort); resolve(); } };
        const abort = () => { this.queue.splice(this.queue.indexOf(entry), 1); reject(new AgilityError('Agility request cancelled.')); };
        this.queue.push(entry);
        signal?.addEventListener('abort', abort, { once: true });
      });
    } else this.active++;
    try { return await fn(); }
    finally { const next = this.queue.shift(); if (next) next.resolve(); else this.active--; }
  }
}

async function boundedText(response, max) {
  if (Number(response.headers.get('content-length')) > max) { await response.body?.cancel(); throw new AgilityError('Agility response exceeds maxResponseBytes. Reduce the page size or selected fields.'); }
  if (!response.body) return '';
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > max) { await reader.cancel(); throw new AgilityError('Agility response exceeds maxResponseBytes. Reduce the page size or selected fields.'); }
      chunks.push(Buffer.from(value));
    }
  } finally { reader.releaseLock(); }
  return Buffer.concat(chunks).toString('utf8');
}
function retryDelay(header, attempt) {
  const seconds = Number(header);
  const supplied = header === null ? NaN : Number.isFinite(seconds) ? seconds * 1000 : Date.parse(header) - Date.now();
  return Number.isFinite(supplied) ? Math.max(0, Math.min(supplied, 30000)) : Math.min(500 * 2 ** attempt + Math.random() * 200, 10000);
}
function checkPage(size, start) {
  if (!Number.isInteger(size) || size < 1 || size > 1000 || !Number.isSafeInteger(start) || start < 0) throw new AgilityError('pageSize must be 1–1000 and start a nonnegative safe integer.');
}

export class AgilityClient {
  constructor(config, store, { fetchImpl = fetch } = {}) {
    this.config = config;
    this.store = store;
    this.fetch = fetchImpl;
    this.limiter = new Limiter(config.maxConcurrency);
  }
  async request(path, { method = 'GET', params, body, json = false, readOnly = method === 'GET', signal, authenticated = true, beforeAttempt } = {}) {
    if (!/^(?:meta\.v1(?:\/[A-Za-z0-9_]+){0,2}|query\.v1|rest-1\.(?:oauth\.)?v1\/(?:Data|Hist|New)\/[A-Za-z0-9_]+(?:\/\d+){0,2})$/.test(path)) throw new AgilityError('Invalid API path.');
    const url = new URL(`${this.config.baseUrl}/${path}`);
    for (const [key, value] of Object.entries(params ?? {})) if (value !== undefined) url.searchParams.set(key, String(value));
    return this.limiter.run(async () => {
      const headers = { Accept: json ? 'application/json' : 'application/xml', 'User-Agent': 'agility-wincred-mcp/0.3.0' };
      if (authenticated) {
        const credentialTimeout = AbortSignal.timeout(this.config.timeoutMs);
        const credentialSignal = signal ? AbortSignal.any([signal, credentialTimeout]) : credentialTimeout;
        try { headers.Authorization = await authorization(this.store, this.config, credentialSignal); }
        catch (error) {
          if (signal?.aborted) throw new AgilityError('Agility request cancelled.');
          if (credentialTimeout.aborted) throw new AgilityError('Windows Credential Manager timed out while loading the Agility credential.');
          throw error;
        }
      }
      if (body !== undefined) headers['Content-Type'] = json ? 'application/json' : 'application/xml; charset=utf-8';
      if (body !== undefined && Buffer.byteLength(body) > 1024 * 1024) throw new AgilityError('Request body exceeds 1 MiB.');
      const retries = readOnly ? this.config.retries : 0;
      for (let attempt = 0; ; attempt++) {
        signal?.throwIfAborted();
        beforeAttempt?.();
        const timeout = AbortSignal.timeout(this.config.timeoutMs);
        const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
        let response;
        try {
          response = await this.fetch(url, { method, headers, body, redirect: 'manual', signal: combined });
          if (readOnly && [429, 502, 503, 504].includes(response.status) && attempt < retries) {
            await response.body?.cancel();
            await delay(retryDelay(response.headers.get('retry-after'), attempt), undefined, { signal });
            continue;
          }
          if (!response.ok) {
            await response.body?.cancel();
            const hint = response.status === 401 ? 'Check the stored credential, auth mode, and REST endpoint.'
              : response.status === 403 ? 'The Agility account lacks permission.'
              : response.status === 404 ? 'The asset or endpoint was not found, or is not visible to this account.'
              : response.status >= 300 && response.status < 400 ? 'Redirects are blocked. Check the instance URL and SSO/API configuration.'
              : 'Check asset metadata, required fields, and query syntax.';
            throw new AgilityError(`Agility HTTP ${response.status}. ${hint}${!readOnly ? ' Verify the asset before retrying the mutation.' : ''}`, response.status);
          }
          const text = await boundedText(response, this.config.maxResponseBytes);
          if (!text.trim()) return null;
          if (json) {
            try { return JSON.parse(text); } catch { throw new AgilityError('Agility returned invalid JSON. Check the instance URL and authentication configuration.'); }
          }
          return text;
        } catch (error) {
          if (error instanceof AgilityError) throw error;
          if (signal?.aborted) throw new AgilityError('Agility request cancelled.');
          if (timeout.aborted) throw new AgilityError(`Agility request timed out.${!readOnly ? ' The mutation may have completed; verify the asset before retrying.' : ''}`);
          // Only transport errors are retried; mutation outcomes can be ambiguous.
          if (readOnly && !response && attempt < retries) { await delay(retryDelay(null, attempt), undefined, { signal }); continue; }
          throw new AgilityError(`Agility network request failed. Check connectivity and TLS trust.${!readOnly ? ' The mutation may have completed; verify the asset before retrying.' : ''}`);
        }
      }
    }, signal);
  }
  async metadata({ assetType, member, signal } = {}) {
    if (member && !assetType) throw new AgilityError('assetType is required when member is specified.');
    const path = ['meta.v1', assetType && assertName(assetType), member && assertName(member)].filter(Boolean).join('/');
    return parseXml(await this.request(path, { authenticated: false, signal }));
  }
  async list({ assetType, select = ['Name'], where, sort, find, findIn, pageSize = 100, start = 0, asOf, signal }) {
    assertName(assetType); checkPage(pageSize, start);
    const result = parseAssets(await this.request(`${this.config.restEndpoint}/${asOf ? 'Hist' : 'Data'}/${assetType}`, {
      params: { sel: itemSelection(assetType, select).join(','), where, sort: sort?.join(','), find, findin: findIn?.join(','), page: `${pageSize},${start}`, asof: asOf }, signal,
    }));
    const end = start + result.assets.length;
    // Without a total, keep paging until the API returns an empty page: the
    // server may cap a requested page at a smaller size.
    const more = result.total === undefined ? result.assets.length > 0 : end < result.total;
    return { ...result, assets: result.assets.map(item => withParent(item)), start, pageSize,
      nextStart: more && result.assets.length ? end : null,
      ...(more && !result.assets.length ? { paginationStatus: 'stalled', paginationWarning: 'Agility reported more assets but returned an empty page.' } : {}) };
  }
  async get({ oid, select = ['Name'], signal }) {
    const result = parseAssets(await this.request(`${this.config.restEndpoint}/Data/${oidPath(oid)}`, { params: { sel: itemSelection(oid.split(':')[0], select).join(',') }, signal }));
    return { ...result, assets: result.assets.map(item => withParent(item)) };
  }
  async history({ oid, select = ['Name', 'ChangeDateUTC', 'ChangedBy.Name'], pageSize = 100, start = 0, signal }) {
    checkPage(pageSize, start);
    const result = parseAssets(await this.request(`${this.config.restEndpoint}/Hist/${oidPath(oid, false)}`, { params: { sel: itemSelection(oid.split(':')[0], select).join(','), page: `${pageSize},${start}` }, signal }));
    return { ...result, assets: result.assets.map(item => withParent(item)) };
  }
  async template({ assetType, context, signal }) {
    if (context) oidPath(context, false);
    const result = parseAssets(await this.request(`${this.config.restEndpoint}/New/${assertName(assetType)}`, { params: { ctx: context }, signal }));
    return { ...result, assets: result.assets.map(item => withParent(item, assetType)) };
  }
  async query({ queries, signal }) {
    return normalizeQueryItems(await this.request('query.v1', { method: 'POST', body: JSON.stringify(prepareQueries(queries)), json: true, readOnly: true, signal }));
  }
  detail(args) { return getItemDetail(this, args); }
  related(args) { return relatedItems(this, args); }
  ensureWrites() { if (!this.config.enableWrites) throw new AgilityError('Writes are disabled. Start the server with --enable-writes.'); }
  async mutate({ assetType, oid, attributes, relations, signal }) {
    this.ensureWrites();
    const path = oid ? oidPath(oid, false) : assertName(assetType);
    const text = await this.request(`${this.config.restEndpoint}/Data/${path}`, { method: 'POST', body: assetXml(attributes, relations), readOnly: false, signal });
    const result = this.mutationResult(text);
    // Mutation responses often contain only changed fields. Read the resulting item
    // to resolve its parent, without turning an enrichment failure into a failed write.
    if (result.assets?.length === 1) {
      const item = result.assets[0];
      const current = canonicalOid(item.oid);
      if (current && isItemType(current.split(':')[0]) && item.parentStatus === 'unavailable') {
        try {
          const read = (await this.get({ oid: current, select: ['Name', 'Number'], signal })).assets[0];
          if (read && canonicalOid(read.oid) === current) result.assets[0] = { ...read, attributes: { ...item.attributes, ...read.attributes }, relations: { ...item.relations, ...read.relations } };
          if (result.assets[0].parentStatus === 'unavailable') result.hierarchyWarnings = ['Mutation succeeded, but the API did not return parent information.'];
        } catch { result.hierarchyWarnings = ['Mutation succeeded, but parent enrichment failed. Read the item to resolve its hierarchy; do not repeat the mutation.']; }
      }
    }
    return result;
  }
  async operation({ oid, operation, signal }) {
    this.ensureWrites(); assertName(operation);
    if (!this.config.allowOperations.includes(operation)) throw new AgilityError('This operation is not in the operator-configured allowOperations list.');
    const text = await this.request(`${this.config.restEndpoint}/Data/${oidPath(oid, false)}`, { method: 'POST', params: { op: operation }, readOnly: false, signal });
    return this.mutationResult(text);
  }
  mutationResult(text) {
    if (!text) return { completed: true };
    try { const result = parseAssets(text); return { ...result, assets: result.assets.map(item => withParent(item)) }; }
    catch { throw new AgilityError('Agility accepted the mutation but returned an unexpected result. Verify the asset before retrying.'); }
  }
}
