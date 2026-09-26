import { spawn, execSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const isWindows = process.platform === 'win32';
const python = path.join(root, 'backend', 'venv', isWindows ? 'Scripts/python.exe' : 'bin/python');

if (!existsSync(python)) {
  console.error(`Backend Python was not found at ${python}`);
  console.error('Create the virtual environment and install backend/requirements.txt first.');
  process.exit(1);
}

async function checkBackendHealth() {
  try {
    const response = await fetch('http://127.0.0.1:8000/api/health', { signal: AbortSignal.timeout(1500) });
    if (response.status === 503) {
      // Backend is running but MongoDB is not connected
      let detail = 'MongoDB is not connected.';
      try {
        const body = await response.json();
        if (body.detail) detail = body.detail;
      } catch { /* ignore */ }
      return { running: false, mongoError: true, detail };
    }
    if (response.ok) {
      const health = await response.json();
      if (health.status === 'ok' && health.database === 'connected') {
        const expectedDir = path.resolve(root, 'backend').toLowerCase();
        const runningDir = health.app_dir ? path.resolve(health.app_dir).toLowerCase() : '';
        if (!runningDir || runningDir === expectedDir) return { running: true };
      }
    }
  } catch { /* not up yet */ }
  return { running: false, mongoError: false };
}

async function hasRunningBackend() {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const result = await checkBackendHealth();
    if (result.running) return true;
    if (attempt < 4) await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return false;
}

async function waitForBackend() {
  const deadline = Date.now() + 60_000;
  let lastMongoError = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetch('http://127.0.0.1:8000/api/health', { signal: AbortSignal.timeout(2000) });
      if (response.status === 503) {
        let detail = 'MongoDB is not connected.';
        try {
          const body = await response.json();
          if (body.detail) detail = body.detail;
        } catch { /* ignore */ }
        lastMongoError = detail;
        // Fast-fail: MongoDB is definitely down, no point waiting 60s
        console.error('');
        console.error('╔══════════════════════════════════════════════════════════════╗');
        console.error('║          ⚠  MongoDB is not running or not reachable          ║');
        console.error('╠══════════════════════════════════════════════════════════════╣');
        console.error(`║  ${detail.slice(0, 60).padEnd(60)}  ║`);
        console.error('╠══════════════════════════════════════════════════════════════╣');
        console.error('║  To fix:                                                     ║');
        console.error('║    1. Start MongoDB:  net start MongoDB                      ║');
        console.error('║       or:             mongod --dbpath C:\\data\\db             ║');
        console.error('║    2. Check MONGODB_URI in backend/.env                      ║');
        console.error('║    3. Re-run:         npm run dev                            ║');
        console.error('╚══════════════════════════════════════════════════════════════╝');
        console.error('');
        return false;
      }
      if (response.ok) {
        const health = await response.json();
        if (health.status === 'ok' && health.database === 'connected') {
          const expectedDir = path.resolve(root, 'backend').toLowerCase();
          const runningDir = health.app_dir ? path.resolve(health.app_dir).toLowerCase() : '';
          if (!runningDir || runningDir === expectedDir) return true;
        }
      }
    } catch { /* still starting */ }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  if (lastMongoError) return false;
  console.error('Backend did not become healthy within 60 seconds. Check the Uvicorn error above.');
  return false;
}

async function hasRunningFrontend() {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch('http://127.0.0.1:5173', { signal: AbortSignal.timeout(1500) });
      const html = response.ok ? await response.text() : '';
      if (html.includes('<title>TalentVerifyAI</title>') && html.includes('id="root"')) return true;
    } catch { /* Vite may still be starting. */ }
    if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return false;
}

const services = [];
let stopping = false;

function killProcessTree(pid) {
  if (!pid) return;
  try {
    if (isWindows) {
      execSync(`taskkill /pid ${pid} /T /F`, { stdio: 'ignore' });
    } else {
      process.kill(-pid, 'SIGTERM');
    }
  } catch { /* process may have already exited */ }
}

function stop(exitCode = 0) {
  if (stopping) return;
  stopping = true;
  for (const service of services) {
    if (isWindows) {
      killProcessTree(service.pid);
    } else {
      if (!service.killed) service.kill('SIGTERM');
    }
  }
  setTimeout(() => process.exit(exitCode), 800);
}

if (await hasRunningBackend()) {
  console.log('TalentVerify backend is already running at http://127.0.0.1:8000; reusing it.');
} else {
  const uvicornArgs = ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000'];
  if (process.argv.includes('--reload')) {
    uvicornArgs.push('--reload');
  }
  const backend = spawn(python, uvicornArgs, {
    cwd: path.join(root, 'backend'),
    stdio: 'inherit',
  });
  services.push(backend);

  if (!await waitForBackend()) {
    stop(1);
    await new Promise(() => {});
  }
}

if (await hasRunningFrontend()) {
  console.log('TalentVerify frontend is already running at http://127.0.0.1:5173; reusing it.');
} else {
  services.push(spawn(isWindows ? (process.env.ComSpec || 'cmd.exe') : 'npm', isWindows ? ['/d', '/s', '/c', 'npm.cmd run dev'] : ['run', 'dev'], {
    cwd: path.join(root, 'frontend'),
    stdio: 'inherit',
  }));
}

if (services.length === 0) {
  console.log('');
  console.log('╔══════════════════════════════════════════════════════════════╗');
  console.log('║   ✅  TalentVerifyAI is ready at http://127.0.0.1:5173      ║');
  console.log('║   Both backend and frontend are already running.              ║');
  console.log('║   Press Ctrl+C to exit this watcher.                         ║');
  console.log('╚══════════════════════════════════════════════════════════════╝');
  console.log('');
  // Keep process alive so the terminal stays open and Ctrl+C works
  setInterval(() => {}, 60_000);
}

for (const service of services) {
  service.on('error', (error) => {
    console.error(error.message);
    stop(1);
  });
  service.on('exit', (code, signal) => {
    if (!stopping && code !== null) {
      console.error(`A development service stopped with exit code ${code}.`);
      stop(code || 1);
    } else if (!stopping && signal) {
      stop(0);
    }
  });
}

process.on('SIGINT', () => stop(0));
process.on('SIGTERM', () => stop(0));
