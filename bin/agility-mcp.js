#!/usr/bin/env node
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import { createInterface } from 'node:readline/promises';
import { stdin, stderr } from 'node:process';
import { parseArgs, loadConfig } from '../src/config.js';
import { WindowsCredentialStore } from '../src/credentials.js';
import { createServer } from '../src/server.js';
import { AgilityClient } from '../src/client.js';

const help = `agility-mcp 0.3.0 — Digital.ai Agility / VersionOne MCP server

Usage:
  agility-mcp [serve] [options]
  agility-mcp credentials set|status|delete [options]
  agility-mcp doctor [options]

Options (also supported in AGILITY_* environment variables or JSON config):
  --base-url URL                Instance URL, including its application path
  --config PATH                 JSON configuration (no secrets)
  --credential-target NAME      Generic Credential target (default: URL-derived)
  --auth-mode token|basic       Default: token
  --rest-endpoint NAME          rest-1.v1 (default) or rest-1.oauth.v1
  --enable-writes               Register create/update tools
  --allow-operations NAMES      Comma-separated exact operation names; requires writes
  --timeout-ms NUMBER           Per-attempt HTTP timeout (default: 30000)
  --retries NUMBER              Read retries (default: 2; never retries writes)
  --max-response-bytes NUMBER   HTTP response limit (default: 4194304)
  --max-concurrency NUMBER      Concurrent HTTP requests (default: 4)
  --allow-http                 Allow unencrypted HTTP for trusted development only
  --help                       Show usage
  --version                    Show version

credentials set prompts for the token/password without echoing it.
Secrets are never accepted as command-line flags or environment variables.
`;

async function readSecret(label) {
  if (!stdin.isTTY || !stderr.isTTY) throw new Error('credentials set requires an interactive Windows terminal.');
  stderr.write(label);
  const wasRaw = stdin.isRaw;
  stdin.setEncoding('utf8');
  stdin.setRawMode(true);
  stdin.resume();
  return new Promise((resolve, reject) => {
    let value = '';
    const finish = error => {
      stdin.removeListener('data', onData);
      stdin.setRawMode(wasRaw ?? false);
      stdin.pause();
      stderr.write('\n');
      error ? reject(error) : resolve(value);
    };
    const onData = chunk => {
      for (const char of chunk.toString('utf8')) {
        if (char === '\u0003') return finish(new Error('Credential entry cancelled.'));
        if (char === '\r' || char === '\n') return finish(value.length ? undefined : new Error('Secret cannot be empty.'));
        if (char === '\u007f' || char === '\b') value = [...value].slice(0, -1).join('');
        else if (char >= ' ' && char !== '\u001b') value += char;
        if (value.length > 1280) return finish(new Error('Secret exceeds the Windows Credential Manager limit of 1280 UTF-16 characters.'));
      }
    };
    stdin.on('data', onData);
  });
}

async function main() {
  const { options, positional } = parseArgs(process.argv.slice(2));
  if (options.help) { stderr.write(help); return; }
  if (options.version) { stderr.write('0.3.0\n'); return; }
  const command = positional[0] ?? 'serve';
  if (!['serve', 'credentials', 'doctor'].includes(command) || positional.length > (command === 'credentials' ? 2 : 1)) throw new Error('Invalid command. Run --help.');
  const config = await loadConfig(options);
  const store = new WindowsCredentialStore();
  if (command === 'credentials') {
    const action = positional[1];
    if (action === 'set') {
      let username = 'agility-token';
      if (config.authMode === 'basic') {
        if (!stdin.isTTY) throw new Error('credentials set requires an interactive terminal.');
        const prompt = createInterface({ input: stdin, output: stderr });
        try { username = await prompt.question('Agility username: '); } finally { prompt.close(); }
        if (!username || /[:\r\n\x00]/.test(username)) throw new Error('Invalid Basic authentication username.');
      }
      const secret = await readSecret(config.authMode === 'token' ? 'Agility access token (hidden): ' : 'Agility password (hidden): ');
      await store.set(config.credentialTarget, username, secret);
      stderr.write(`Credential stored for ${config.baseUrl}\nTarget: ${config.credentialTarget}\n`);
    } else if (action === 'status' || action === 'delete') {
      const result = await store[action](config.credentialTarget);
      stderr.write(JSON.stringify({ target: config.credentialTarget, ...result }) + '\n');
    } else throw new Error('Use credentials set, credentials status, or credentials delete.');
    return;
  }
  if (command === 'doctor') {
    const credential = await store.status(config.credentialTarget);
    if (!credential.found) throw new Error('Credential not found. Run credentials set first.');
    const client = new AgilityClient(config, store);
    await client.list({ assetType: 'Scope', select: ['Name'], pageSize: 1 });
    stderr.write(`Agility authentication and REST query succeeded for ${config.baseUrl}\n`);
    return;
  }
  if (process.platform !== 'win32') throw new Error('This server requires Windows Credential Manager and must run on Windows.');
  const handle = serveStdio(() => createServer(config, { store }));
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { Promise.resolve(handle.close()).catch(() => {}).finally(() => { process.exitCode = 0; }); });
}

main().catch(error => { stderr.write(`agility-mcp: ${error.message}\n`); process.exitCode = 1; });
