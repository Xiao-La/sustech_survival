// Shared desktop configuration/launch protocol. No Electron or network side effects.
'use strict';

const SID_KEY = 'credentials.sid';
const PASSWORD_KEY = 'credentials.password';
const MODULE_SOURCE = 'sustech_survival[webui] @ git+https://github.com/dumixthestpd/sustech_survival.git';

async function loadStore() {
  // electron-store 10 is an ES module; import it once from the main process.
  const { default: Store } = await import('electron-store');
  return new Store({ name: 'sustech_survival' });
}

function validateCredentials(sid, password) {
  if (typeof sid !== 'string' || typeof password !== 'string' || !sid.trim() || !password
      || /[:\r\n]/.test(sid) || /[\r\n]/.test(password)) {
    throw new Error('Enter both SID and password without line breaks');
  }
}

function saveCredentials(store, vault, sid, password) {
  validateCredentials(sid, password);
  if (!vault.isEncryptionAvailable()) throw new Error('OS credential vault unavailable');
  store.set(SID_KEY, vault.encryptString(sid.trim()).toString('base64'));
  store.set(PASSWORD_KEY, vault.encryptString(password).toString('base64'));
}

function readCredentials(store, vault) {
  const sid = store.get(SID_KEY);
  const password = store.get(PASSWORD_KEY);
  if (!sid || !password) return null;
  if (!vault.isEncryptionAvailable()) throw new Error('OS credential vault unavailable');
  // Also accept the Buffer JSON objects written by the previous implementation.
  const decode = (value) => vault.decryptString(
    typeof value === 'string' ? Buffer.from(value, 'base64') : Buffer.from(value));
  const credentials = { sid: decode(sid), password: decode(password) };
  validateCredentials(credentials.sid, credentials.password);
  return credentials;
}

function clearCredentials(store) {
  store.delete(SID_KEY);
  store.delete(PASSWORD_KEY);
}

function backendLaunch(port, skin, credentials) {
  const args = ['-m', 'sustech_survival.webui._desktop', '--port', String(port)];
  if (skin) args.push('--skin', String(skin));
  return { args, input: JSON.stringify(credentials || {}) + '\n' };
}

module.exports = { loadStore, saveCredentials, readCredentials, clearCredentials,
                   backendLaunch, MODULE_SOURCE };
