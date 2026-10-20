import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';

export function normalizeBaseUrl(value, allowHttp = false) {
  let url;
  try { url = new URL(value); } catch { throw new Error('Set AGILITY_BASE_URL to the Agility instance URL, including its instance path.'); }
  if (url.protocol !== 'https:' && !(allowHttp && url.protocol === 'http:')) {
    throw new Error('Agility requires HTTPS. Use --allow-http only for a trusted development instance.');
  }
  if (url.username || url.password || url.search || url.hash) throw new Error('The base URL cannot contain credentials, a query, or a fragment.');
  if (/%2f|%5c|%2e/i.test(url.pathname)) throw new Error('Encoded path separators and dot segments are not allowed.');
  return url.href.replace(/\/+$/, '');
}

const fields = {
  'base-url': 'baseUrl', 'credential-target': 'credentialTarget', 'auth-mode': 'authMode',
  'rest-endpoint': 'restEndpoint', 'timeout-ms': 'timeoutMs', retries: 'retries',
  'max-response-bytes': 'maxResponseBytes', 'max-concurrency': 'maxConcurrency',
  'enable-writes': 'enableWrites', 'allow-operations': 'allowOperations', 'allow-http': 'allowHttp',
};
const booleans = new Set(['enable-writes', 'allow-http']);

export function parseArgs(args) {
  const options = {};
  const positional = [];
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (!arg.startsWith('--')) { positional.push(arg); continue; }
    const [key, ...parts] = arg.slice(2).split('=');
    if (key === 'help' || key === 'version') { options[key] = true; continue; }
    if (key !== 'config' && !fields[key]) throw new Error(`Unknown option --${key}. Run --help.`);
    if (booleans.has(key)) {
      if (parts.length) throw new Error(`--${key} does not accept a value.`);
      options[fields[key]] = true;
    } else {
      const value = parts.length ? parts.join('=') : args[++i];
      if (!value || value.startsWith('--')) throw new Error(`Missing value for --${key}.`);
      options[fields[key] ?? key] = value;
    }
  }
  return { options, positional };
}

function integer(value, fallback, min, max, name) {
  const n = Number(value ?? fallback);
  if (!Number.isInteger(n) || n < min || n > max) throw new Error(`${name} must be an integer between ${min} and ${max}.`);
  return n;
}
function boolean(value, name) {
  if (value === undefined) return false;
  if (value === true || value === 'true') return true;
  if (value === false || value === 'false') return false;
  throw new Error(`${name} must be true or false.`);
}

export async function loadConfig(options = {}, env = process.env) {
  let file = {};
  const path = options.config ?? env.AGILITY_CONFIG;
  if (path) {
    try { file = JSON.parse(await readFile(path, 'utf8')); }
    catch { throw new Error('Cannot read the configuration file as JSON.'); }
    if (!file || Array.isArray(file) || typeof file !== 'object') throw new Error('Configuration must be an object.');
    const unknown = Object.keys(file).filter(key => !Object.values(fields).includes(key));
    if (unknown.length) throw new Error(`Unknown configuration keys: ${unknown.join(', ')}. Secrets belong in Credential Manager.`);
  }
  const environment = Object.fromEntries(Object.values(fields).map(key => {
    const name = 'AGILITY_' + key.replace(/[A-Z]/g, c => '_' + c).toUpperCase();
    return [key, env[name]];
  }).filter(([, value]) => value !== undefined));
  const raw = { ...file, ...environment, ...options };
  const allowHttp = boolean(raw.allowHttp, 'allowHttp');
  const baseUrl = normalizeBaseUrl(raw.baseUrl, allowHttp);
  const authMode = raw.authMode ?? 'token';
  const restEndpoint = raw.restEndpoint ?? 'rest-1.v1';
  if (!['token', 'basic'].includes(authMode)) throw new Error('authMode must be token or basic.');
  if (!['rest-1.v1', 'rest-1.oauth.v1'].includes(restEndpoint)) throw new Error('restEndpoint must be rest-1.v1 or rest-1.oauth.v1.');
  const credentialTarget = raw.credentialTarget ?? `agility-mcp:${createHash('sha256').update(baseUrl).digest('hex').slice(0, 32)}`;
  if (typeof credentialTarget !== 'string' || !credentialTarget.length || credentialTarget.length > 256 || /[\x00-\x1f]/.test(credentialTarget)) throw new Error('Invalid Credential Manager target.');
  const allowOperations = Array.isArray(raw.allowOperations) ? raw.allowOperations : (raw.allowOperations ?? '').split(',').filter(Boolean);
  if (allowOperations.some(op => typeof op !== 'string' || !/^[A-Za-z][A-Za-z0-9_]*$/.test(op))) throw new Error('allowOperations must contain exact operation names separated by commas.');
  const enableWrites = boolean(raw.enableWrites, 'enableWrites');
  if (allowOperations.length && !enableWrites) throw new Error('allowOperations requires enableWrites.');
  return Object.freeze({ baseUrl, allowHttp, authMode, restEndpoint, credentialTarget, enableWrites, allowOperations: Object.freeze(allowOperations),
    timeoutMs: integer(raw.timeoutMs, 30000, 100, 300000, 'timeoutMs'),
    retries: integer(raw.retries, 2, 0, 5, 'retries'),
    maxResponseBytes: integer(raw.maxResponseBytes, 4 * 1024 * 1024, 1024, 32 * 1024 * 1024, 'maxResponseBytes'),
    maxConcurrency: integer(raw.maxConcurrency, 4, 1, 16, 'maxConcurrency'),
  });
}
