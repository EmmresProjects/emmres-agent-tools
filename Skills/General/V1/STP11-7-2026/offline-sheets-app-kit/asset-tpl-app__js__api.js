/* =====================================================================
   {{KIT_TITLE}} - API client for Google Apps Script
   - Never throws on network failure: always resolves with { ok, offline }
   - Local password mirror keeps login working with no internet
   ===================================================================== */
(function (global) {
  'use strict';

  const API = {};
  const KEY_URL = 'cfg_url';
  const KEY_TOKEN = 'cfg_token';

  API.getUrl = function () { return (U.ls.get(KEY_URL, '') || '').trim(); };
  API.getToken = function () { return (U.ls.get(KEY_TOKEN, '') || '').trim(); };
  API.setConfig = function (url, token) { U.ls.set(KEY_URL, (url || '').trim()); U.ls.set(KEY_TOKEN, (token || '').trim()); };
  API.isConfigured = function () { return !!API.getUrl(); };

  API.deviceId = function () {
    let d = U.ls.get('device_id', '');
    if (!d) { d = 'DEV-' + U.uuid().slice(0, 8).toUpperCase(); U.ls.set('device_id', d); }
    return d;
  };
  API.deviceLabel = function () {
    let l = U.ls.get('device_label', '');
    if (!l) { l = 'Device ' + API.deviceId().slice(4); U.ls.set('device_label', l); }
    return l;
  };
  API.setDeviceLabel = function (l) { U.ls.set('device_label', l); };

  /* ------------------------------------------------------------------ */
  /*  Core request                                                      */
  /* ------------------------------------------------------------------ */
  API.request = function (action, payload, opts) {
    opts = opts || {};
    const url = API.getUrl();
    if (!url) return Promise.resolve({ ok: false, offline: true, error: 'No Apps Script URL configured yet.' });
    if (navigator.onLine === false) return Promise.resolve({ ok: false, offline: true, error: 'You are offline.' });

    const body = JSON.stringify({
      token: API.getToken(),
      action: action,
      payload: payload || {},
      deviceId: API.deviceId(),
      deviceLabel: API.deviceLabel(),
      appVersion: (global.APP_VERSION || '{{APP_VERSION}}'),
      clientTime: U.nowISO()
    });

    const ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    const timeout = setTimeout(function () { if (ctrl) ctrl.abort(); }, opts.timeout || 25000);

    return fetch(url, {
      method: 'POST',
      // text/plain avoids the CORS pre-flight, which Apps Script cannot answer
      headers: { 'Content-Type': 'text/plain;charset=utf-8' },
      body: body,
      redirect: 'follow',
      signal: ctrl ? ctrl.signal : undefined
    }).then(function (res) {
      clearTimeout(timeout);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.text();
    }).then(function (text) {
      let data;
      try { data = JSON.parse(text); }
      catch (e) { throw new Error('Bad response from server (is the Web App deployed with access "Anyone"?).'); }
      if (data && data['__{{BRAND_SLUG}}'] === undefined && data.ok === undefined) {
        // still accept, but note the shape
      }
      return data;
    }).catch(function (err) {
      clearTimeout(timeout);
      const msg = String(err && err.message || err);
      const offline = /Failed to fetch|NetworkError|aborted|Load failed|HTTP 0|timed out/i.test(msg);
      return { ok: false, offline: offline, error: offline ? 'Cannot reach the server. Your work is saved on this device.' : msg };
    });
  };

  /* ------------------------------------------------------------------ */
  /*  Actions                                                           */
  /* ------------------------------------------------------------------ */
  API.ping = function () { return API.request('ping', {}, { timeout: 12000 }); };

  API.login = function (username, password, branchId) {
    return API.request('login', { username: username, password: password, branchId: branchId }, { timeout: 20000 })
      .then(function (r) {
        if (r.ok && r.data && r.data.account) {
          // Refresh the local mirror so the next offline login works
          checkPassword(password, r.data.account).then(function (localOk) {
            if (localOk) return;
            return LocalAuth.storeMirror(r.data.account, password);
          });
        }
        return r;
      });
  };

  function checkPassword(password, account) {
    return LocalAuth.verifyAgainst(account, password).then(function (ok) { return ok; });
  }

  API.pull = function () {
    return API.request('pull', {}, { timeout: 30000 }).then(function (r) {
      if (r.ok && r.data) return r.data;
      return null;
    });
  };

  API.pushOrders = function (orders) {
    if (!orders || !orders.length) return Promise.resolve({ ok: true, results: [] });
    return API.request('pushOrders', { orders: orders }, { timeout: 45000 }).then(function (r) {
      // Apps Script returns { ok, results: [...] }; accept a nested data.results too
      const results = (r && r.results) || (r && r.data && r.data.results);
      if (r.ok && results) return { ok: true, results: results };
      return { ok: false, offline: !!r.offline, error: r.error || 'Sync failed', results: [] };
    });
  };

  API.pushNonRevenue = function (rows) {
    if (!rows || !rows.length) return Promise.resolve({ ok: true, results: [] });
    return API.request('pushNonRevenue', { logs: rows }, { timeout: 30000 });
  };

  API.pushEOD = function (rows) {
    if (!rows || !rows.length) return Promise.resolve({ ok: true, results: [] });
    return API.request('pushEOD', { reports: rows }, { timeout: 30000 });
  };

  API.voidOrder = function (recordId, reason, account, isAdmin) {
    return API.request('voidOrder', { recordId: recordId, reason: reason, accountId: account, isAdmin: isAdmin }, { timeout: 20000 });
  };

  API.manage = function (entity, op, record, accountId) {
    return API.request('manage', { entity: entity, op: op, record: record, accountId: accountId }, { timeout: 25000 });
  };

  API.changePassword = function (accountId, oldPassword, newPassword, actorId) {
    return API.request('changePassword', { accountId: accountId, oldPassword: oldPassword, newPassword: newPassword, actorId: actorId }, { timeout: 25000 });
  };

  API.report = function (params) {
    return API.request('report', params || {}, { timeout: 30000 });
  };

  API.saveSettings = function (settings, accountId) {
    return API.request('saveSettings', { settings: settings, accountId: accountId }, { timeout: 20000 });
  };

  /* ================================================================== */
  /*  Local auth mirror (offline sign-in)                               */
  /*  Passwords are never stored. Only salt + SHA-256 are kept, and the  */
  /*  hash is computed locally with SubtleCrypto to match the server.    */
  /* ================================================================== */
  const LocalAuth = {};

  /* Pure-JS SHA-256: works from file:// in Cordova and Electron,
     and always matches the hash Apps Script produces. */
  LocalAuth.sha256Hex = function (str) {
    return Promise.resolve(U.sha256Hex(str));
  };

  LocalAuth.storeMirror = function (account, password) {
    const salt = account && (account.salt || account.passwordSalt);
    if (!account || !salt) return Promise.resolve(false);
    return LocalAuth.sha256Hex(salt + '|' + password).then(function (hex) {
      const mirror = U.ls.get('auth_mirror', {});
      mirror[account.id] = {
        id: account.id, username: account.username, displayName: account.displayName,
        salt: salt, hash: 'sha256$' + hex, savedAt: U.nowISO()
      };
      U.ls.set('auth_mirror', mirror);
      return true;
    });
  };

  LocalAuth.verifyAgainst = function (account, password) {
    const salt = account && (account.salt || account.passwordSalt);
    const hash = account && (account.hash || account.passwordHash);
    if (!salt || !hash) return Promise.resolve(false);
    return LocalAuth.sha256Hex(salt + '|' + String(password)).then(function (hex) {
      return String(hash) === 'sha256$' + hex;
    });
  };

  /* Sign in using the offline copy of the Accounts table */
  LocalAuth.offlineLogin = function (username, password) {
    const uname = String(username || '').trim().toLowerCase();
    return DB.getAll('accounts').then(function (accounts) {
      const acc = accounts.find(function (a) { return String(a.username || '').toLowerCase() === uname; });
      if (!acc) return { ok: false, error: 'Account not found on this device.' };
      if (String(acc.status || 'Active') !== 'Active') return { ok: false, error: 'This account is not active.' };
      return LocalAuth.verifyAgainst(acc, password).then(function (ok) {
        if (!ok) return { ok: false, error: 'Wrong password.' };
        return {
          ok: true, offline: true,
          data: {
            account: {
              id: acc.id, username: acc.username, displayName: acc.displayName,
              roleId: acc.roleId, branchId: acc.branchId, status: acc.status,
              salt: acc.passwordSalt, passwordHash: acc.passwordHash, forceChange: acc.forceChange
            }
          }
        };
      });
    });
  };

  /* Verify the current user's password for sensitive actions */
  LocalAuth.verifyUser = function (session, password) {
    return DB.get('accounts', session.accountId).then(function (acc) {
      if (!acc) return false;
      return LocalAuth.verifyAgainst(acc, password);
    });
  };

  API.LocalAuth = LocalAuth;
  global.API = API;
})(window);
