// app.js — minimal settings UI for the Electron shell.
//
// NOTE: The Flask webui itself is the primary surface — this panel is just
// for credential vault + skin picker + update control. The real login flow
// happens inside the BrowserWindow pointing at the bundled webui.

'use strict';

const statusEl = document.getElementById('status');
const sidEl = document.getElementById('sid');
const pwEl = document.getElementById('password');
const skinEl = document.getElementById('skin');
const updateStatusEl = document.getElementById('update-status');

function action(id, work) {
  document.getElementById(id).addEventListener('click', async () => {
    try { await work(); }
    catch (error) { statusEl.textContent = error.message || 'Operation failed'; }
  });
}

action('save', async () => {
  await window.sustech.vault.set(sidEl.value.trim(), pwEl.value);
  pwEl.value = '';
  statusEl.textContent = 'credentials saved; Web UI ready';
});

action('load', async () => {
  const { sid, password } = await window.sustech.vault.get();
  sidEl.value = sid || '';
  pwEl.value = password || '';
  statusEl.textContent = sid ? 'loaded saved credentials' : 'nothing saved yet';
});

action('clear', async () => {
  await window.sustech.vault.clear();
  sidEl.value = '';
  pwEl.value = '';
  statusEl.textContent = 'desktop credentials cleared';
});

action('open-webui', async () => {
  await window.sustech.app.openWebui();
  statusEl.textContent = 'Web UI opened';
});

skinEl.addEventListener('change', async () => {
  try {
    await window.sustech.settings.set('active_skin', skinEl.value);
    statusEl.textContent = `skin set to ${skinEl.value}`;
  } catch (error) { statusEl.textContent = error.message || 'Skin change failed'; }
});

(async () => {
  const saved = await window.sustech.settings.get('active_skin');
  if (saved) skinEl.value = saved;
  statusEl.textContent = 'ready';
})().catch((error) => { statusEl.textContent = error.message; });

document.getElementById('check-update').addEventListener('click', async () => {
  updateStatusEl.textContent = 'checking…';
  const r = await window.sustech.updater.check();
  updateStatusEl.textContent = r.skipped ? 'updates disabled in development' : r.ok ? `latest: v${r.version}` : `error: ${r.error}`;
});

document.getElementById('upgrade-python').addEventListener('click', async () => {
  updateStatusEl.textContent = 'upgrading python module…';
  const r = await window.sustech.python.upgrade();
  updateStatusEl.textContent = r.code === 0 ? 'python module upgraded' : `pip exit ${r.code}`;
});

document.getElementById('open-logs').addEventListener('click', () => {
  window.sustech.app.openLogs();
});

window.sustech.updater.onAvailable((info) => {
  updateStatusEl.textContent = `update v${info.version} downloading…`;
});

window.sustech.updater.onDownloaded((info) => {
  updateStatusEl.textContent = `v${info.version} ready — restart to install`;
});
