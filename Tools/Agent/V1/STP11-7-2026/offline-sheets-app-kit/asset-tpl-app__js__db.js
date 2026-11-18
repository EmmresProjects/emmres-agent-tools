/* =====================================================================
   {{KIT_TITLE}} - Local Database (IndexedDB + localStorage fallback)
   Everything is written here FIRST, then synced to Google Sheets.
   ===================================================================== */
(function (global) {
  'use strict';

  const DB_NAME = '{{BRAND_SLUG}}_pos_tracker';
  const DB_VERSION = 4;
  const STORES = ['products', 'categories', 'accounts', 'branches', 'roles', 'settings', 'orders', 'nonrev', 'synclog', 'eod'];

  const DB = { ready: false, mode: 'indexeddb', _db: null };

  function openIndexedDB() {
    return new Promise(function (resolve, reject) {
      let req;
      try { req = indexedDB.open(DB_NAME, DB_VERSION); }
      catch (e) { reject(e); return; }

      req.onupgradeneeded = function (ev) {
        const db = ev.target.result;
        if (!db.objectStoreNames.contains('products')) {
          const s = db.createObjectStore('products', { keyPath: 'id' });
          s.createIndex('category', 'category', { unique: false });
        }
        if (!db.objectStoreNames.contains('accounts')) {
          const s = db.createObjectStore('accounts', { keyPath: 'id' });
          s.createIndex('username', 'username', { unique: false });
        }
        if (!db.objectStoreNames.contains('branches')) db.createObjectStore('branches', { keyPath: 'id' });
        if (!db.objectStoreNames.contains('categories')) db.createObjectStore('categories', { keyPath: 'id' });
        if (!db.objectStoreNames.contains('roles')) db.createObjectStore('roles', { keyPath: 'id' });
        if (!db.objectStoreNames.contains('settings')) db.createObjectStore('settings', { keyPath: 'key' });
        if (!db.objectStoreNames.contains('nonrev')) {
          const s = db.createObjectStore('nonrev', { keyPath: 'recordId' });
          s.createIndex('syncStatus', 'syncStatus', { unique: false });
          s.createIndex('businessDate', 'businessDate', { unique: false });
        }
        if (!db.objectStoreNames.contains('eod')) {
          const s = db.createObjectStore('eod', { keyPath: 'recordId' });
          s.createIndex('syncStatus', 'syncStatus', { unique: false });
        }
        if (!db.objectStoreNames.contains('orders')) {
          const s = db.createObjectStore('orders', { keyPath: 'recordId' });
          s.createIndex('syncStatus', 'syncStatus', { unique: false });
          s.createIndex('businessDate', 'businessDate', { unique: false });
          s.createIndex('kitchenStatus', 'kitchenStatus', { unique: false });
          s.createIndex('branchId', 'branchId', { unique: false });
        }
        if (!db.objectStoreNames.contains('synclog')) {
          const s = db.createObjectStore('synclog', { keyPath: 'id', autoIncrement: true });
          s.createIndex('recordId', 'recordId', { unique: false });
        }
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
      req.onblocked = function () { reject(new Error('Database blocked by another tab')); };
    });
  }

  /* ------------------------- localStorage fallback ------------------------- */
  const LS_PREFIX = '{{BRAND_SLUG}}:';
  const LS = {
    all: function (store) { return U.ls.get('db_' + store, []); },
    save: function (store, arr) { U.ls.set('db_' + store, arr); },
    keyOf: function (store) { return store === 'settings' ? 'key' : (store === 'orders' || store === 'nonrev' || store === 'eod') ? 'recordId' : 'id'; }
  };

  DB.init = function () {
    if (DB.ready) return Promise.resolve(DB);
    return openIndexedDB().then(function (db) {
      DB._db = db; DB.ready = true; DB.mode = 'indexeddb';
      return DB;
    }).catch(function (err) {
      console.warn('[DB] IndexedDB unavailable, using the storage fallback:', err && err.message);
      DB.ready = true; DB.mode = 'localstorage';
      return DB;
    });
  };

  function tx(store, mode) {
    return DB._db.transaction(store, mode || 'readonly').objectStore(store);
  }

  DB.put = function (store, value) {
    if (DB.mode === 'localstorage') {
      const arr = LS.all(store), k = LS.keyOf(store);
      const i = arr.findIndex(function (r) { return r[k] === value[k]; });
      if (i >= 0) arr[i] = value; else arr.push(value);
      LS.save(store, arr);
      return Promise.resolve(value);
    }
    return new Promise(function (resolve, reject) {
      const r = tx(store, 'readwrite').put(value);
      r.onsuccess = function () { resolve(value); };
      r.onerror = function () { reject(r.error); };
    });
  };

  DB.bulkPut = function (store, values) {
    if (!values || !values.length) return Promise.resolve([]);
    if (DB.mode === 'localstorage') {
      const arr = LS.all(store), k = LS.keyOf(store);
      values.forEach(function (v) {
        const i = arr.findIndex(function (r) { return r[k] === v[k]; });
        if (i >= 0) arr[i] = v; else arr.push(v);
      });
      LS.save(store, arr);
      return Promise.resolve(values);
    }
    return new Promise(function (resolve, reject) {
      const t = DB._db.transaction(store, 'readwrite');
      const os = t.objectStore(store);
      values.forEach(function (v) { os.put(v); });
      t.oncomplete = function () { resolve(values); };
      t.onerror = function () { reject(t.error); };
    });
  };

  DB.get = function (store, key) {
    if (DB.mode === 'localstorage') {
      const k = LS.keyOf(store);
      return Promise.resolve(LS.all(store).find(function (r) { return r[k] === key; }) || null);
    }
    return new Promise(function (resolve, reject) {
      const r = tx(store).get(key);
      r.onsuccess = function () { resolve(r.result || null); };
      r.onerror = function () { reject(r.error); };
    });
  };

  DB.getAll = function (store) {
    if (DB.mode === 'localstorage') return Promise.resolve(LS.all(store));
    return new Promise(function (resolve, reject) {
      const r = tx(store).getAll();
      r.onsuccess = function () { resolve(r.result || []); };
      r.onerror = function () { reject(r.error); };
    });
  };

  DB.getAllByIndex = function (store, index, value) {
    if (DB.mode === 'localstorage') {
      return DB.getAll(store).then(function (all) {
        return all.filter(function (r) { return r[index] === value; });
      });
    }
    return new Promise(function (resolve, reject) {
      const r = tx(store).index(index).getAll(IDBKeyRange.only(value));
      r.onsuccess = function () { resolve(r.result || []); };
      r.onerror = function () { reject(r.error); };
    });
  };

  DB.delete = function (store, key) {
    if (DB.mode === 'localstorage') {
      const k = LS.keyOf(store);
      LS.save(store, LS.all(store).filter(function (r) { return r[k] !== key; }));
      return Promise.resolve();
    }
    return new Promise(function (resolve, reject) {
      const r = tx(store, 'readwrite').delete(key);
      r.onsuccess = function () { resolve(); };
      r.onerror = function () { reject(r.error); };
    });
  };

  DB.clear = function (store) {
    if (DB.mode === 'localstorage') { LS.save(store, []); return Promise.resolve(); }
    return new Promise(function (resolve, reject) {
      const r = tx(store, 'readwrite').clear();
      r.onsuccess = function () { resolve(); };
      r.onerror = function () { reject(r.error); };
    });
  };

  DB.clearAll = function () {
    return Promise.all(STORES.map(function (s) { return DB.clear(s); }));
  };

  DB.exportAll = function () {
    const out = {};
    return STORES.reduce(function (p, s) {
      return p.then(function () { return DB.getAll(s); }).then(function (rows) { out[s] = rows; return out; });
    }, Promise.resolve());
  };

  DB.importAll = function (data) {
    const keys = Object.keys(data || {});
    return keys.reduce(function (p, s) {
      return p.then(function () {
        if (STORES.indexOf(s) < 0) return;
        return DB.clear(s).then(function () { return DB.bulkPut(s, data[s] || []); });
      });
    }, Promise.resolve());
  };

  /* --------------------------- Settings helpers --------------------------- */
  DB.getSetting = function (key, dflt) {
    return DB.get('settings', key).then(function (r) { return (r && r.value !== undefined) ? r.value : dflt; });
  };
  DB.setSetting = function (key, value) { return DB.put('settings', { key: key, value: value }); };

  global.DB = DB;
})(window);
