'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const desktop = require('../desktop');

function fixtures() {
  const values = new Map();
  const store = { get: (key) => values.get(key), set: (key, value) => values.set(key, value),
                  delete: (key) => values.delete(key) };
  const vault = { isEncryptionAvailable: () => true,
                  encryptString: (value) => Buffer.from('encrypted:' + value),
                  decryptString: (buffer) => buffer.toString().slice('encrypted:'.length) };
  return { store, vault };
}

test('vault round trip, old Buffer JSON compatibility and clearing', () => {
  const { store, vault } = fixtures();
  desktop.saveCredentials(store, vault, 'fixture-sid', 'fixture-password');
  assert.deepEqual(desktop.readCredentials(store, vault),
                   { sid: 'fixture-sid', password: 'fixture-password' });
  store.set('credentials.sid', vault.encryptString('legacy-sid').toJSON());
  assert.equal(desktop.readCredentials(store, vault).sid, 'legacy-sid');
  assert.equal(store.get('credentials.password').includes('fixture-password'), false);
  desktop.clearCredentials(store);
  assert.equal(desktop.readCredentials(store, vault), null);
});

test('invalid credentials do not modify storage', () => {
  const { store, vault } = fixtures();
  assert.throws(() => desktop.saveCredentials(store, vault, 'sid', ''), /Enter both/);
  assert.equal(store.get('credentials.sid'), undefined);
});

test('backend receives skin as an argument and credentials only over stdin', () => {
  const config = desktop.backendLaunch(12345, 'default_zh',
                                        { sid: 'fixture-sid', password: 'fixture-password' });
  assert.deepEqual(config.args, ['-m', 'sustech_survival.webui._desktop', '--port', '12345', '--skin', 'default_zh']);
  assert.equal(config.args.join(' ').includes('fixture-password'), false);
  assert.equal(JSON.parse(config.input).password, 'fixture-password');
  assert.deepEqual(JSON.parse(desktop.backendLaunch(12345, null, null).input), {});
});

function mainHarness(credentials = null) {
  const { store, vault } = fixtures();
  if (credentials) desktop.saveCredentials(store, vault, credentials.sid, credentials.password);
  const windows = [], children = [], handlers = new Map();
  let ready, menu;
  const directory = path.join(__dirname, '..');
  class Window extends EventEmitter {
    constructor(options) {
      super(); this.options = options; this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = () => {};
      this.webContents.send = () => {};
      windows.push(this);
    }
    async loadFile(file) { this.url = pathToFileURL(file).href; }
    async loadURL(url) { this.url = url; }
    focus() { this.focused = true; }
    static getAllWindows() { return windows; }
  }
  const app = new EventEmitter();
  app.isPackaged = false;
  app.whenReady = () => ({ then: (callback) => { ready = callback; } });
  app.quit = () => { throw new Error('unexpected quit'); };
  const electron = { app, BrowserWindow: Window, safeStorage: vault,
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    dialog: { showErrorBox: () => {} }, shell: { openExternal: () => {} },
    Menu: { buildFromTemplate: (template) => template, setApplicationMenu: (template) => { menu = template; } } };
  const net = {
    createServer: () => ({ unref() {}, on() {}, address: () => ({ port: 12345 + children.length }),
                          listen: (_port, _host, callback) => callback(), close: (callback) => callback() }),
    connect: () => {
      const socket = new EventEmitter(); socket.end = () => {}; socket.destroy = () => {};
      queueMicrotask(() => socket.emit('connect')); return socket;
    },
  };
  const spawn = (_python, args, options) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
    child.stdout.resume = child.stderr.resume = () => {};
    child.stdin = new EventEmitter(); child.stdin.end = (input) => { child.input = input; };
    child.kill = () => { child.killed = true; child.emit('exit', 0); };
    child.pid = 1000 + children.length;
    if (args[0] === '-c') queueMicrotask(() => child.emit('exit', 0));
    else { child.args = args; child.options = options; children.push(child); }
    return child;
  };
  const requireFixture = (name) => {
    if (name === 'electron') return electron;
    if (name === 'electron-updater') return { autoUpdater: new EventEmitter() };
    if (name === './desktop') return { ...desktop, loadStore: async () => store };
    if (name === 'node:child_process') return { spawn };
    if (name === 'node:net') return net;
    if (name === 'node:fs') return { existsSync: (file) => file === '/fixture/python' };
    return require(name);
  };
  vm.runInNewContext(fs.readFileSync(path.join(directory, 'main.js'), 'utf8'), {
    require: requireFixture, __dirname: directory,
    process: { platform: 'darwin', arch: 'arm64', env: { SUSTECH_PYTHON: '/fixture/python' } },
    console: { log() {}, error() {} }, Buffer,
    setTimeout: () => 0,
  });
  const event = (window) => ({ sender: window.webContents, senderFrame: { url: window.url } });
  return { ready: () => ready(), windows, children, handlers, event, menu: () => menu };
}

test('first-run settings save launches Python; skin changes and clear restart it', async () => {
  const harness = mainHarness();
  await harness.ready();
  const settings = harness.windows[0];
  assert.match(settings.url, /renderer\/index.html$/);
  assert.equal(harness.children.length, 0);
  assert.ok(harness.menu().some((item) => item.label === 'File'));
  await harness.handlers.get('vault:set')(harness.event(settings),
                                         { sid: 'fixture-sid', password: 'fixture-password' });
  const first = harness.children[0];
  assert.equal(JSON.parse(first.input).sid, 'fixture-sid');
  assert.equal(first.options.stdio[0], 'pipe');
  assert.equal(first.options.env.password, undefined);
  assert.match(harness.windows[1].url, /^http:\/\/127\.0\.0\.1:/);
  assert.equal(harness.windows[1].options.webPreferences.preload, undefined);
  await harness.handlers.get('settings:set')(harness.event(settings),
                                             { key: 'active_skin', value: 'default_zh' });
  assert.equal(first.killed, true);
  assert.deepEqual(harness.children[1].args.slice(-2), ['--skin', 'default_zh']);
  await harness.handlers.get('vault:clear')(harness.event(settings));
  assert.deepEqual(JSON.parse(harness.children[2].input), {});
  await assert.rejects(() => harness.handlers.get('vault:get')(harness.event(harness.windows[1])),
                       /local Settings/);
});

test('Open Web UI permits existing CLI credentials without creating a vault entry', async () => {
  const harness = mainHarness();
  await harness.ready();
  await harness.handlers.get('app:openWebui')(harness.event(harness.windows[0]));
  assert.deepEqual(JSON.parse(harness.children[0].input), {});
});


test('saved vault credentials are read on restart and Settings is available from the menu', async () => {
  const harness = mainHarness({ sid: 'saved-fixture', password: 'saved-password' });
  await harness.ready();
  assert.equal(harness.children.length, 1);
  assert.equal(JSON.parse(harness.children[0].input).sid, 'saved-fixture');
  assert.match(harness.windows[0].url, /^http:\/\/127\.0\.0\.1:/);
  const fileMenu = harness.menu().find((item) => item.label === 'File');
  await fileMenu.submenu[0].click();
  assert.match(harness.windows[1].url, /renderer\/index.html$/);
});


test('closing the Web UI stops its backend even while Settings remains open', async () => {
  const harness = mainHarness({ sid: 'saved-fixture', password: 'saved-password' });
  await harness.ready();
  const fileMenu = harness.menu().find((item) => item.label === 'File');
  await fileMenu.submenu[0].click();
  harness.windows[0].emit('closed');
  assert.equal(harness.children[0].killed, true);
  await harness.handlers.get('app:openWebui')(harness.event(harness.windows[1]));
  assert.equal(harness.children.length, 2);
  assert.match(harness.windows[2].url, /^http:\/\/127\.0\.0\.1:/);
});
