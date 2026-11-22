/* =====================================================================
   {{KIT_TITLE}} - Utilities
   ===================================================================== */
(function (global) {
  'use strict';

  const EPS = '{{BRAND_SLUG}}-';
  const U = {};

  /* ---------------------------- IDs ---------------------------- */
  U.uuid = function () {
    try {
      if (global.crypto && typeof global.crypto.randomUUID === 'function') return global.crypto.randomUUID();
    } catch (e) { /* fall through */ }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      const r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  };
  U.recordId = function () { return U.uuid(); };

  /* --------------------------- Numbers ------------------------- */
  U.num = function (v, d) {
    const n = typeof v === 'number' ? v : parseFloat(String(v == null ? '' : v).replace(/[^0-9.\-]/g, ''));
    return isFinite(n) ? n : (d === undefined ? 0 : d);
  };
  U.round2 = function (n) { return Math.round((Number(n) + Number.EPSILON) * 100) / 100; };
  U.money = function (n) {
    const v = U.round2(U.num(n));
    return '\u20B1' + v.toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  U.money0 = function (n) { return '\u20B1' + Math.round(U.num(n)).toLocaleString('en-PH'); };
  U.pct = function (n) { return (U.round2(n * 100)).toFixed(1) + '%'; };
  U.int = function (v) { return Math.round(U.num(v)); };

  /* ---------------------------- Dates -------------------------- */
  U.nowISO = function () { return new Date().toISOString(); };
  U.todayKey = function (d) { return U.dateKey(d || new Date()); };
  U.dateKey = function (d) {
    const dt = (d instanceof Date) ? d : new Date(d);
    const y = dt.getFullYear();
    const m = String(dt.getMonth() + 1).padStart(2, '0');
    const dd = String(dt.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + dd;
  };
  U.addDays = function (dateKeyOrDate, n) {
    const d = (typeof dateKeyOrDate === 'string') ? new Date(dateKeyOrDate + 'T00:00:00') : new Date(dateKeyOrDate);
    d.setDate(d.getDate() + n);
    return d;
  };
  U.time12 = function (iso) {
    const d = new Date(iso);
    if (isNaN(d)) return '--';
    let h = d.getHours(); const m = String(d.getMinutes()).padStart(2, '0');
    const ap = h >= 12 ? 'PM' : 'AM';
    h = h % 12; if (h === 0) h = 12;
    return h + ':' + m + ' ' + ap;
  };
  U.fmtDate = function (key) {
    if (!key) return '--';
    const d = new Date(key + 'T00:00:00');
    if (isNaN(d)) return key;
    return d.toLocaleDateString('en-PH', { month: 'short', day: 'numeric', year: 'numeric' });
  };
  U.fmtDateTime = function (iso) {
    if (!iso) return '--';
    const d = new Date(iso);
    if (isNaN(d)) return '--';
    return U.fmtDate(U.dateKey(d)) + ' ' + U.time12(iso);
  };
  U.ago = function (iso) {
    const t = new Date(iso).getTime();
    if (!t) return '';
    const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm';
    if (s < 86400) return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
    return Math.floor(s / 86400) + 'd';
  };
  U.minutesSince = function (iso) { return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000)); };

  /* --------------------------- Strings ------------------------- */
  U.esc = function (s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };
  U.initial = function (name) {
    const p = String(name || '?').trim().split(/\s+/);
    return ((p[0] || '?')[0] + (p.length > 1 ? p[p.length - 1][0] : '')).toUpperCase();
  };
  U.slug = function (s) { return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, ''); };
  U.titleCase = function (s) { return String(s || '').replace(/\b\w/g, function (c) { return c.toUpperCase(); }); };

  /* ----------------------------- CSV --------------------------- */
  U.toCSV = function (rows, headers) {
    const esc = function (v) {
      const s = (v === null || v === undefined) ? '' : String(v);
      return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    const out = [];
    if (headers) out.push(headers.map(esc).join(','));
    rows.forEach(function (r) { out.push(r.map(esc).join(',')); });
    return out.join('\r\n');
  };
  U.download = function (filename, content, mime) {
    const blob = new Blob([content], { type: mime || 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 400);
  };

  /* --------------------------- Storage ------------------------- */
  /* Storage that never throws:
     1. localStorage when available (normal browsers, APK, EXE)
     2. an in-memory map when storage is blocked (private mode, or a
        sandboxed iframe preview). Data is session-only in that case,
        which is still enough to try the app.                              */
  const MEM = {};
  U.ls = {
    persistent: true,
    get: function (k, d) {
      const key = EPS + k;
      try {
        const v = localStorage.getItem(key);
        if (v !== null) return JSON.parse(v);
      } catch (e) { U.ls.persistent = false; }
      return Object.prototype.hasOwnProperty.call(MEM, key) ? MEM[key] : d;
    },
    set: function (k, v) {
      const key = EPS + k;
      MEM[key] = v;
      try { localStorage.setItem(key, JSON.stringify(v)); return true; }
      catch (e) { U.ls.persistent = false; return false; }
    },
    del: function (k) {
      const key = EPS + k;
      delete MEM[key];
      try { localStorage.removeItem(key); } catch (e) {}
    }
  };

  /* --------------------------- Hashing ------------------------- */
  /* Password hashing for the offline mirror / first-run seed.
     The authoritative hash is produced by Apps Script (SHA-256, salted).
     This local hash keeps plaintext out of localStorage & the sheet.        */
  U.simpleHash = function (password, salt) {
    const str = '{{BRAND_SLUG}}$' + (salt || '') + '$' + String(password);
    let h1 = 0x811c9dc5, h2 = 0x01000193;
    for (let i = 0; i < str.length; i++) {
      const c = str.charCodeAt(i);
      h1 = (h1 ^ c) >>> 0; h1 = Math.imul(h1, 0x01000193) >>> 0;
      h2 = (h2 + c * (i + 7)) >>> 0; h2 = Math.imul(h2, 0x85ebca6b) >>> 0;
    }
    return 'h1$' + h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0');
  };


  /* ------------------- SHA-256 (pure JS) ----------------------- */
  /* crypto.subtle is only available in a secure context. Cordova and
     Electron load the app from file://, so a dependency-free SHA-256
     is required for the password hashes to match the server exactly. */
  const K256 = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
  ];

  function utf8Bytes(str) {
    const out = [];
    for (let i = 0; i < str.length; i++) {
      let c = str.charCodeAt(i);
      if (c < 0x80) out.push(c);
      else if (c < 0x800) { out.push(0xc0 | (c >> 6), 0x80 | (c & 63)); }
      else if (c < 0xd800 || c >= 0xe000) { out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63)); }
      else {
        i++;
        c = 0x10000 + (((c & 0x3ff) << 10) | (str.charCodeAt(i) & 0x3ff));
        out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
      }
    }
    return out;
  }

  U.sha256Hex = function (message) {
    const bytes = utf8Bytes(String(message == null ? '' : message));
    const bitLen = bytes.length * 8;
    bytes.push(0x80);
    while (bytes.length % 64 !== 56) bytes.push(0);
    const hi = Math.floor(bitLen / 0x100000000), lo = bitLen >>> 0;
    bytes.push((hi >>> 24) & 255, (hi >>> 16) & 255, (hi >>> 8) & 255, hi & 255);
    bytes.push((lo >>> 24) & 255, (lo >>> 16) & 255, (lo >>> 8) & 255, lo & 255);

    let H = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    const W = new Array(64);
    const rotr = function (x, n) { return (x >>> n) | (x << (32 - n)); };

    for (let off = 0; off < bytes.length; off += 64) {
      for (let i = 0; i < 16; i++) {
        W[i] = ((bytes[off + i * 4] << 24) | (bytes[off + i * 4 + 1] << 16) |
                (bytes[off + i * 4 + 2] << 8) | bytes[off + i * 4 + 3]) | 0;
      }
      for (let i = 16; i < 64; i++) {
        const s0 = rotr(W[i - 15], 7) ^ rotr(W[i - 15], 18) ^ (W[i - 15] >>> 3);
        const s1 = rotr(W[i - 2], 17) ^ rotr(W[i - 2], 19) ^ (W[i - 2] >>> 10);
        W[i] = (W[i - 16] + s0 + W[i - 7] + s1) | 0;
      }
      let [a, b, c, d, e, f, g, h] = H;
      for (let i = 0; i < 64; i++) {
        const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const t1 = (h + S1 + ch + K256[i] + W[i]) | 0;
        const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + maj) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0;
        d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      H = [(H[0] + a) | 0, (H[1] + b) | 0, (H[2] + c) | 0, (H[3] + d) | 0,
           (H[4] + e) | 0, (H[5] + f) | 0, (H[6] + g) | 0, (H[7] + h) | 0];
    }
    return H.map(function (x) { return ('00000000' + (x >>> 0).toString(16)).slice(-8); }).join('');
  };

  /* ---------------------------- Misc --------------------------- */
  U.debounce = function (fn, ms) {
    let t; return function () { const a = arguments, c = this; clearTimeout(t); t = setTimeout(function () { fn.apply(c, a); }, ms || 200); };
  };
  U.sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  U.clone = function (o) { return JSON.parse(JSON.stringify(o)); };
  U.sum = function (arr, fn) { return arr.reduce(function (a, b) { return a + (fn ? fn(b) : b); }, 0); };
  U.groupBy = function (arr, fn) {
    return arr.reduce(function (acc, item) { const k = fn(item); (acc[k] || (acc[k] = [])).push(item); return acc; }, {});
  };
  U.sortBy = function (arr, fn, dir) {
    const d = dir === 'desc' ? -1 : 1;
    return arr.slice().sort(function (a, b) {
      const x = fn(a), y = fn(b);
      if (x === y) return 0;
      return (x > y ? 1 : -1) * d;
    });
  };

  global.U = U;
})(window);
