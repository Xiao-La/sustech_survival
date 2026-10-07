// main.js — Electron main process for sustech_survival
//
// Responsibilities:
//   1. Spawn a bundled portable Python (python-build-standalone) running
//      `python -m sustech_survival.webui._desktop --port <free>`.
//   2. Open a BrowserWindow pointed at http://127.0.0.1:<port>/.
//   3. Expose IPC handlers via preload.js for:
//        - safeStorage credential vault (OS keychain/DPAPI/libsecret)
//        - settings persistence (electron-store)
//        - auto-update check (electron-updater, GitHub Releases)
//        - in-app Python module upgrade (`pip install --upgrade`)
//   4. Shut the Python child down cleanly on quit.
//
// This wrapper does NOT replace the webui module — Flask + Jinja skins
// remain the source of truth. Electron is purely a friendlier launcher
// for non-technical users who don't want a terminal.

'use strict';

const { app, BrowserWindow, ipcMain, safeStorage, dialog, shell, Menu } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { autoUpdater } = require('electron-updater');

const desktop = require('./desktop');
let storePromise;
const getStore = () => (storePromise ||= desktop.loadStore());

const isDev = !app.isPackaged;
const RESOURCES = isDev ? path.join(__dirname) : process.resourcesPath;

// -- Find a free port for the bundled Flask webui -------------------------

function findFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

// -- Locate the Python interpreter -----------------------------------------
//
// Packaged: the bundled portable Python (python-build-standalone) under
// resources/python/<platform>-<arch>/ — always used, env-independent.
//
// Dev: we need SOME Python with `sustech_survival` importable. Resolution:
//   1. $SUSTECH_PYTHON override, then this project's conda env
//      (ai-sustech-dev), then plain Python on PATH — the first candidate
//      that ALREADY has the module wins (fast path; nothing is installed
//      into any env).
//   2. If none of them has it, the app creates an ISOLATED venv at
//      electron/.venv and pip-installs the repo (editable) into it — the
//      user's conda/system envs are NEVER touched. First run needs network
//      and takes about a minute; afterwards it is instant.

function bundledPythonDir() {
  // Bundled portable Python (python-build-standalone) lives under
  // resources/python/<platform>-<arch>/ — e.g. python/win32-x64/python.exe.
  return path.join(RESOURCES, 'python', `${process.platform}-${process.arch}`);
}

const DEV_VENV_DIR = path.join(__dirname, '.venv');

function venvPythonPath() {
  return process.platform === 'win32'
    ? path.join(DEV_VENV_DIR, 'Scripts', 'python.exe')
    : path.join(DEV_VENV_DIR, 'bin', 'python');
}

function basePythonBinary() {
  // Dev candidates, in order. Only existence is checked here; module
  // presence is probed by ensureDevPython().
  const envCandidates = [
    process.env.SUSTECH_PYTHON,                       // explicit override
    'D:\\dumix\\Applications\\conda\\envs\\ai-sustech-dev\\python.exe',
    path.join(process.env.USERPROFILE || '', 'Applications', 'conda',
              'envs', 'ai-sustech-dev', 'python.exe'),
  ].filter(Boolean);
  for (const c of envCandidates) {
    if (fs.existsSync(c)) return c;
  }
  return process.platform === 'win32' ? 'python.exe' : 'python3';
}

function runQuiet(py, args) {
  // Resolve true when the command exits 0. Failure (missing interpreter,
  // crash, exit != 0) resolves false. Output is discarded.
  return new Promise((resolve) => {
    const proc = spawn(py, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    proc.stdout.resume();
    proc.stderr.resume();
    proc.on('error', () => resolve(false));
    proc.on('exit', (code) => resolve(code === 0));
  });
}

function runLogged(py, args, tag) {
  // Run a longer bootstrap step (venv create / pip install) with its
  // output streamed to the terminal under [tag].
  return new Promise((resolve) => {
    const proc = spawn(py, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    proc.stdout.on('data', (b) => console.log(`[${tag}]`, b.toString().trimEnd()));
    proc.stderr.on('data', (b) => console.error(`[${tag}:err]`, b.toString().trimEnd()));
    proc.on('error', (e) => {
      console.error(`[${tag}] failed to launch:`, e);
      resolve(false);
    });
    proc.on('exit', (code) => resolve(code === 0));
  });
}

async function ensureDevPython() {
  // Fast path: reuse a Python that already has the module. Nothing is
  // installed anywhere — the user's envs stay exactly as they are.
  const base = basePythonBinary();
  if (await runQuiet(base, ['-c', 'import sustech_survival.webui._desktop'])) {
    return base;
  }

  // Isolated fallback: electron/.venv, created FROM `base` (the base
  // interpreter is only used to create the venv, never polluted).
  const venvPy = venvPythonPath();
  if (fs.existsSync(venvPy)) {
    if (await runQuiet(venvPy, ['-c', 'import sustech_survival.webui._desktop'])) {
      console.log(`[venv] using existing ${venvPy}`);
      return venvPy;
    }
    console.log('[venv] existing .venv is stale — re-creating');
    fs.rmSync(DEV_VENV_DIR, { recursive: true, force: true });
  }

  console.log(`[venv] creating isolated venv at ${DEV_VENV_DIR} …`);
  if (!await runLogged(base, ['-m', 'venv', DEV_VENV_DIR], 'venv')) {
    throw new Error(`failed to create venv with ${base} (python -m venv)`);
  }
  console.log('[venv] installing sustech_survival[webui] (editable, from the repo) …');
  const repoRoot = path.join(__dirname, '..');
  if (!await runLogged(venvPy,
                       ['-m', 'pip', 'install', '--disable-pip-version-check',
                        '-e', `${repoRoot}[webui]`], 'venv')) {
    throw new Error('failed to pip install sustech_survival[webui] into the venv');
  }
  console.log(`[venv] ready — using ${venvPy}`);
  return venvPy;
}

async function pythonBinary() {
  if (isDev) return ensureDevPython();
  const exe = process.platform === 'win32' ? 'python.exe' : 'bin/python3';
  return path.join(bundledPythonDir(), exe);
}

// -- Spawn the webui as a child process -----------------------------------

let webuiProc = null;
let webuiPort = null;
let mainWindow = null;
let settingsWindow = null;
const settingsPath = path.join(__dirname, 'renderer', 'index.html');

function windowIcon() {
  // Torch-only logo as the window/taskbar icon. Packaged: resources
  // folder; dev: electron/build/.
  const candidates = [
    path.join(RESOURCES, 'icon.png'),
    path.join(__dirname, 'build', 'icon.png'),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return undefined;
}

async function startWebui() {
  webuiPort = await findFreePort();
  const py = await pythonBinary();

  if (!isDev && !fs.existsSync(py)) {
    dialog.showErrorBox(
      'Bundled Python missing',
      `Could not find portable Python at ${py}.\n` +
      'This build was not packaged with a Python runtime — ' +
      'reinstall sustech_survival from the official installer, or ' +
      'report this issue.'
    );
    app.quit();
    return;
  }

  const store = await getStore();
  const launch = desktop.backendLaunch(webuiPort, store.get('active_skin'),
                                       desktop.readCredentials(store, safeStorage));
  const child = spawn(py, launch.args, {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  });

  webuiProc = child;
  child.stdin.on('error', () => console.error('[webui] could not deliver desktop configuration'));
  child.stdin.end(launch.input);
  child.on('error', () => console.error('[webui] backend failed to launch'));
  child.stdout.on('data', (b) => console.log('[webui]', b.toString().trimEnd()));
  child.stderr.on('data', (b) => console.error('[webui:err]', b.toString().trimEnd()));
  child.on('exit', (code) => {
    console.log(`[webui] exited with code ${code}`);
    if (webuiProc === child) webuiProc = null;
  });

  // Wait up to 10s for the webui to start serving.
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    try {
      await new Promise((resolve, reject) => {
        const sock = net.connect(webuiPort, '127.0.0.1');
        sock.once('connect', () => { sock.end(); resolve(); });
        sock.once('error', reject);
        setTimeout(() => { sock.destroy(); reject(new Error('timeout')); }, 500);
      });
      console.log(`[webui] up on http://127.0.0.1:${webuiPort}/`);
      return;
    } catch { /* not ready yet */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  stopWebui();
  throw new Error(`webui did not start within 10s`);
}

function stopWebui() {
  if (!webuiProc) return;
  const child = webuiProc;
  webuiProc = null;
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(child.pid), '/t', '/f']);
    } else {
      child.kill('SIGTERM');
    }
  } catch (e) {
    console.error('[webui] kill failed:', e);
  }
}

// -- BrowserWindow --------------------------------------------------------

async function createWindow() {
  try {
    await startWebui();
  } catch (e) {
    console.error('[webui] failed to start:', e);
    dialog.showErrorBox(
      'sustech_survival could not start',
      'The built-in web service failed to start.\n\n' +
      String(e && e.message ? e.message : e) + '\n\n' +
      'If this is a development build: the app auto-installs the module ' +
      'into electron/.venv when no Python has it (first run needs network; ' +
      'see the terminal log). Otherwise install it into the Python being ' +
      'used (pip install -e ".[webui]").'
    );
    app.quit();
    return;
  }

  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    title: 'sustech_survival',
    icon: windowIcon(),
    backgroundColor: '#0f1115',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  await mainWindow.loadURL(`http://127.0.0.1:${webuiPort}/`);

  // External links open in the user's browser, not inside Electron.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

// -- Local Settings window and backend configuration ----------------------

async function openSettings() {
  if (settingsWindow) { settingsWindow.focus(); return; }
  settingsWindow = new BrowserWindow({
    width: 600, height: 760, title: 'sustech_survival Settings',
    icon: windowIcon(),
    webPreferences: { preload: path.join(__dirname, 'preload.js'),
                      contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  settingsWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  settingsWindow.on('closed', () => { settingsWindow = null; });
  await settingsWindow.loadFile(settingsPath);
}

function requireSettings(event) {
  const { pathToFileURL } = require('node:url');
  if (!settingsWindow || event.sender !== settingsWindow.webContents
      || event.senderFrame?.url !== pathToFileURL(settingsPath).href) {
    throw new Error('This operation is available from the local Settings window');
  }
}

let backendChanges = Promise.resolve();
function applyDesktopChanges() {
  const change = backendChanges.catch(() => {}).then(async () => {
    if (!mainWindow) return createWindow();
    stopWebui();
    await startWebui();
    await mainWindow.loadURL(`http://127.0.0.1:${webuiPort}/`);
  });
  backendChanges = change;
  return change;
}

ipcMain.handle('vault:set', async (event, { sid, password }) => {
  requireSettings(event);
  desktop.saveCredentials(await getStore(), safeStorage, sid, password);
  await applyDesktopChanges();
  return { ok: true };
});

ipcMain.handle('vault:get', async (event) => {
  requireSettings(event);
  return desktop.readCredentials(await getStore(), safeStorage) || { sid: '', password: '' };
});

ipcMain.handle('vault:clear', async (event) => {
  requireSettings(event);
  desktop.clearCredentials(await getStore());
  if (mainWindow) await applyDesktopChanges();
  return { ok: true };
});

ipcMain.handle('settings:get', async (event, key) => {
  requireSettings(event);
  return (await getStore()).get(key);
});

ipcMain.handle('settings:set', async (event, { key, value }) => {
  requireSettings(event);
  if (key !== 'active_skin' || !['default', 'default_zh'].includes(value)) {
    throw new Error('Choose a supported desktop skin');
  }
  (await getStore()).set(key, value);
  if (mainWindow) await applyDesktopChanges();
  return { ok: true };
});

ipcMain.handle('app:openWebui', async (event) => {
  requireSettings(event);
  if (mainWindow) { mainWindow.focus(); return { ok: true }; }
  await applyDesktopChanges();
  return { ok: true };
});

function setupMenu() {
  const template = [];
  if (process.platform === 'darwin') template.push({ role: 'appMenu' });
  template.push({ label: 'File', submenu: [
    { label: 'Settings…', accelerator: 'CmdOrCtrl+,', click: openSettings },
    { type: 'separator' }, { role: 'quit' },
  ] }, { role: 'editMenu' }, { role: 'viewMenu' });
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// -- IPC: Python module upgrade (`pip install --upgrade`) -----------------

ipcMain.handle('python:upgrade', async (event) => {
  requireSettings(event);
  const py = await pythonBinary();
  const result = await new Promise((resolve) => {
    const proc = spawn(py, ['-m', 'pip', 'install', '--upgrade', '--force-reinstall', desktop.MODULE_SOURCE], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '', err = '';
    proc.stdout.on('data', (b) => { out += b.toString(); });
    proc.stderr.on('data', (b) => { err += b.toString(); });
    proc.on('error', () => resolve({ code: 1, stdout: out, stderr: 'Python upgrade could not start' }));
    proc.on('close', (code) => resolve({ code, stdout: out, stderr: err }));
  });
  if (result.code === 0 && mainWindow) await applyDesktopChanges();
  return result;
});

// -- IPC: open log directory ----------------------------------------------

ipcMain.handle('app:openLogs', (event) => {
  requireSettings(event);
  const logDir = isDev
    ? path.join(__dirname, '..', 'logs')
    : path.join(app.getPath('userData'), 'logs');
  fs.mkdirSync(logDir, { recursive: true });
  shell.openPath(logDir);
  return { logDir };
});

// -- Auto-update (electron-updater, GitHub Releases) ----------------------

function setupAutoUpdater() {
  if (isDev) return; // never auto-update during local dev
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on('update-available', (info) => {
    if (settingsWindow) settingsWindow.webContents.send('update:available', info);
  });
  autoUpdater.on('update-downloaded', (info) => {
    if (settingsWindow) settingsWindow.webContents.send('update:downloaded', info);
  });
  autoUpdater.on('error', (err) => {
    console.error('[updater] error:', err);
  });
}

ipcMain.handle('updater:check', async (event) => {
  requireSettings(event);
  if (isDev) return { skipped: 'dev-mode' };
  try {
    const result = await autoUpdater.checkForUpdates();
    return { ok: true, version: result?.updateInfo?.version };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
});

ipcMain.handle('updater:install', (event) => {
  requireSettings(event);
  if (isDev) return { skipped: 'dev-mode' };
  autoUpdater.quitAndInstall();
});

// -- App lifecycle --------------------------------------------------------

app.whenReady().then(async () => {
  setupAutoUpdater();
  setupMenu();
  let credentials;
  try { credentials = desktop.readCredentials(await getStore(), safeStorage); }
  catch { /* Settings lets the user replace unavailable saved credentials. */ }
  if (credentials) await createWindow();
  else await openSettings();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) openSettings();
  });
});

app.on('window-all-closed', () => {
  stopWebui();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => { stopWebui(); });