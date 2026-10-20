import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const script = fileURLToPath(new URL('../scripts/credential-manager.ps1', import.meta.url));

export class WindowsCredentialStore {
  constructor({ platform = process.platform, spawnProcess = spawn, windowsDirectory = process.env.SystemRoot ?? 'C:\\Windows' } = {}) {
    this.platform = platform;
    this.spawnProcess = spawnProcess;
    this.executable = join(windowsDirectory, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  }
  async run(action, target, values = {}, signal) {
    if (this.platform !== 'win32') throw new Error('Authentication requires Windows Credential Manager. Run this server on Windows under the user who stored the credential.');
    signal?.throwIfAborted();
    return new Promise((resolve, reject) => {
      const child = this.spawnProcess(this.executable, ['-NoLogo', '-NoProfile', '-NonInteractive', '-File', script], {
        windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], shell: false,
      });
      const chunks = [];
      let size = 0;
      let settled = false;
      const finish = (error, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
        error ? reject(error) : resolve(value);
      };
      const abort = () => { child.kill(); finish(new Error('Windows Credential Manager cancelled.')); };
      const timer = setTimeout(() => { child.kill(); finish(new Error('Windows Credential Manager timed out.')); }, 20000);
      signal?.addEventListener('abort', abort, { once: true });
      if (signal?.aborted) { abort(); return; }
      child.stdout.on('data', chunk => {
        size += chunk.length;
        if (size > 32768) { child.kill(); finish(new Error('Invalid credential helper response.')); }
        else chunks.push(chunk);
      });
      child.stderr.resume(); // Do not relay helper output; it may contain local details.
      child.stdin.on('error', () => finish(new Error('Cannot communicate with Windows Credential Manager.')));
      child.on('error', () => finish(new Error('Cannot start Windows PowerShell. Check the Windows installation and application policy.')));
      child.on('close', code => {
        if (code !== 0) return finish(new Error('Windows Credential Manager failed. Check your Windows user session and PowerShell policy.'));
        try { finish(null, JSON.parse(Buffer.concat(chunks).toString('utf8').replace(/^\uFEFF/, ''))); }
        catch { finish(new Error('Invalid credential helper response.')); }
      });
      child.stdin.end(JSON.stringify({ action, target, ...values }));
    });
  }
  async get(target, signal) {
    const value = await this.run('get', target, {}, signal);
    if (!value.found || !value.secret) throw new Error('Agility credential not found or empty. Run agility-mcp credentials set with the same instance URL and Windows user.');
    return { username: value.username ?? '', secret: value.secret };
  }
  status(target, signal) { return this.run('status', target, {}, signal); }
  set(target, username, secret) { return this.run('set', target, { username, secret }); }
  delete(target) { return this.run('delete', target); }
}

export async function authorization(store, config, signal) {
  const { username, secret } = await store.get(config.credentialTarget, signal);
  if (/[\r\n\x00]/.test(secret)) throw new Error('Credential contains invalid control characters.');
  if (config.authMode === 'token') return `Bearer ${secret}`;
  if (!username || /[:\r\n\x00]/.test(username)) throw new Error('Basic authentication requires a username without colon or control characters. Store it with credentials set.');
  return `Basic ${Buffer.from(`${username}:${secret}`, 'utf8').toString('base64')}`;
}
