/* =====================================================================
   {{KIT_TITLE}} - Core application shell
   Session, routing, permissions, modals, toasts, sync engine.
   ===================================================================== */
(function (global) {
  'use strict';

  const App = {};
  if (!global.APP_VERSION) global.APP_VERSION = '{{APP_VERSION}}';

  App.state = {
    brand: '{{BRAND_SLUG}}',
    businessName: '{{BUSINESS_NAME}}',
    session: null,
    page: 'pos',
    branchId: '',
    branches: [],
    roles: [],
    accounts: [],
    products: [],
    settings: {},
    sync: { status: 'idle', lastRun: null, lastError: '', pending: 0, needsReview: 0 },
    demo: false,
    ready: false
  };

  /* ================================================================== */
  /*  Small DOM helpers                                                 */
  /* ================================================================== */
  App.el = function (sel) { return document.querySelector(sel); };
  App.els = function (sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); };
  App.on = function (sel, ev, fn) { App.els(sel).forEach(function (n) { n.addEventListener(ev, fn); }); };

  /* ================================================================== */
  /*  Toasts                                                            */
  /* ================================================================== */
  App.toast = function (msg, type, sub) {
    let host = document.getElementById('toasts');
    if (!host) { host = document.createElement('div'); host.id = 'toasts'; document.body.appendChild(host); }
    const t = document.createElement('div');
    t.className = 'toast ' + (type || '');
    t.innerHTML = '<div>' + U.esc(msg) + '</div>' + (sub ? '<div class="t-sub">' + U.esc(sub) + '</div>' : '');
    host.appendChild(t);
    setTimeout(function () {
      t.style.transition = 'opacity .25s, transform .25s';
      t.style.opacity = '0'; t.style.transform = 'translateX(24px)';
      setTimeout(function () { t.remove(); }, 260);
    }, type === 'err' ? 4200 : 2800);
  };

  /* ================================================================== */
  /*  Modals                                                            */
  /* ================================================================== */
  App.modal = function (opts) {
    const scrim = document.createElement('div');
    scrim.className = 'modal-scrim';
    const size = opts.size === 'wide' ? ' wide' : opts.size === 'narrow' ? ' narrow' : '';
    scrim.innerHTML =
      '<div class="modal' + size + '" role="dialog" aria-modal="true">' +
        '<div class="modal-head"><h3>' + U.esc(opts.title || '') + '</h3>' +
          '<button class="x-btn" data-close aria-label="Close">&#10005;</button></div>' +
        '<div class="modal-body">' + (opts.body || '') + '</div>' +
        (opts.footer === null ? '' : '<div class="modal-foot">' + (opts.footer || '') + '</div>') +
      '</div>';
    document.body.appendChild(scrim);
    const modal = scrim.querySelector('.modal');

    function close(result) {
      scrim.remove();
      document.removeEventListener('keydown', onKey);
      if (opts.onClose) opts.onClose(result);
    }
    function onKey(e) { if (e.key === 'Escape' && !opts.locked) close(null); }

    scrim.addEventListener('click', function (e) {
      if (e.target === scrim && !opts.locked) close(null);
      if (e.target.closest('[data-close]')) close(null);
    });
    document.addEventListener('keydown', onKey);
    if (opts.onMount) opts.onMount(modal, close);

    const focusable = modal.querySelector('[autofocus], input:not([type=hidden]), select, textarea');
    if (focusable) setTimeout(function () { try { focusable.focus(); } catch (e) {} }, 60);
    return { close: close, modal: modal };
  };

  App.confirm = function (title, message, opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      let done = false;
      App.modal({
        title: title, size: 'narrow',
        body: '<p style="margin:0;line-height:1.6;color:var(--text-dim)">' + message + '</p>',
        footer: '<button class="btn btn-ghost" data-no>Cancel</button>' +
                '<button class="btn ' + (opts.danger ? 'btn-danger' : 'btn-primary') + '" data-yes>' + U.esc(opts.okText || 'Continue') + '</button>',
        onMount: function (m, close) {
          m.querySelector('[data-yes]').onclick = function () { done = true; close(); resolve(true); };
          m.querySelector('[data-no]').onclick = function () { done = true; close(); resolve(false); };
        },
        onClose: function () { if (!done) resolve(false); }
      });
    });
  };

  App.prompt = function (title, label, opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      let done = false;
      App.modal({
        title: title, size: 'narrow',
        body: '<div class="field"><label>' + U.esc(label) + '</label>' +
              '<input class="input" id="promptInput" value="' + U.esc(opts.value || '') + '" ' +
              (opts.type === 'password' ? 'type="password"' : '') + ' placeholder="' + U.esc(opts.placeholder || '') + '"></div>' +
              (opts.hint ? '<p class="small muted" style="margin:0">' + opts.hint + '</p>' : ''),
        footer: '<button class="btn btn-ghost" data-no>Cancel</button><button class="btn btn-primary" data-yes>' + U.esc(opts.okText || 'Save') + '</button>',
        onMount: function (m, close) {
          const input = m.querySelector('#promptInput');
          const submit = function () { done = true; const v = input.value.trim(); close(); resolve(v || null); };
          m.querySelector('[data-yes]').onclick = submit;
          m.querySelector('[data-no]').onclick = function () { done = true; close(); resolve(null); };
          input.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
        },
        onClose: function () { if (!done) resolve(null); }
      });
    });
  };

  /* ================================================================== */
  /*  Permissions                                                       */
  /* ================================================================== */
  App.can = function (perm) {
    const s = App.state.session;
    if (!s) return false;
    if (s.isAdmin) return true;
    return (s.permissions || []).indexOf(perm) >= 0;
  };
  App.isAdmin = function () { return !!(App.state.session && App.state.session.isAdmin); };
  App.myBranch = function () { return App.state.session ? App.state.session.branchId : ''; };

  App.branchesVisible = function () {
    const s = App.state.session;
    if (!s) return [];
    if (s.allBranches) return App.state.branches;
    return App.state.branches.filter(function (b) { return b.id === s.branchId; });
  };

  /* Products store a category id; the UI shows the human name. */
  App.categoryLabel = function (id) {
    if (!id) return '';
    const c = (App.state.categories || []).find(function (x) { return x.id === id || x.name === id; });
    return c ? c.name : String(id).replace(/^CAT-/, '').replace(/-/g, ' ').toLowerCase()
      .replace(/\b\w/g, function (m) { return m.toUpperCase(); });
  };

  App.branchName = function (id) {
    const b = App.state.branches.find(function (x) { return x.id === id; });
    return b ? b.name : (id || '—');
  };

  App.branchCode = function (id) {
    const b = App.state.branches.find(function (x) { return x.id === id; });
    return b ? (b.code || b.id) : id;
  };

  /* ================================================================== */
  /*  Session                                                           */
  /* ================================================================== */
  const SESSION_KEY = 'session';

  App.loadSession = function () {
    const s = U.ls.get(SESSION_KEY, null);
    if (s && s.accountId) {
      App.state.session = s;
      App.state.branchId = s.branchId || (App.state.branches[0] && App.state.branches[0].id) || '';
      return true;
    }
    return false;
  };

  App.saveSession = function () {
    if (App.state.session) U.ls.set(SESSION_KEY, App.state.session);
  };

  App.logout = function (silent) {
    U.ls.del(SESSION_KEY);
    App.state.session = null;
    if (typeof POS !== 'undefined' && POS.resetCart) POS.resetCart();
    document.getElementById('app').classList.add('hidden');
    document.getElementById('loginScreen').classList.remove('hidden');
    if (!silent) App.toast('Signed out', 'ok');
  };

  /* ================================================================== */
  /*  First-run seed                                                    */
  /* ================================================================== */
  App.seedIfEmpty = function () {
    return DB.getAll('products').then(function (products) {
      if (products.length) return false;
      const seed = global.KIT_SEED;
      if (!seed) return false;
      return DB.bulkPut('branches', (seed.branches || []).map(App.mapBranch))
        .then(function () { return DB.bulkPut('roles', (seed.roles || []).map(App.mapRole)); })
        .then(function () { return DB.bulkPut('accounts', (seed.accounts || []).map(App.mapAccount)); })
        .then(function () { return DB.bulkPut('products', (seed.products || []).map(App.mapProduct)); })
        .then(function () {
          const cats = (seed.categories || []).map(function (c) {
            return Array.isArray(c) ? { id: c[0], name: c[1], icon: c[2] || '' } : c;
          });
          return cats.length ? DB.bulkPut('categories', cats) : null;
        })
        .then(function () {
          const sets = (seed.settings || []).map(function (row) {
            return { key: row[0], value: row[1] };
          });
          return DB.bulkPut('settings', sets);
        })
        .then(function () { return true; });
    });
  };

  /* Map a sheet-style branch row to the local shape */
  /* Seed rows are arrays (compact, identical in seed.js and Installer.gs).
     These mappers accept either the array shape or an already-mapped object. */

  App.truthy = function (v) {
    return v === true || v === 1 || String(v).toLowerCase() === 'yes' || String(v).toLowerCase() === 'true';
  };

  App.mapRole = function (r) {
    if (r && r.id) return r;
    return {
      id: r[0], name: r[1], description: r[2], permissions: String(r[3] || '').split(',').filter(Boolean),
      isAdmin: r[4] === 'Yes', canPOS: r[5] === 'Yes', canKitchen: r[6] === 'Yes',
      allBranches: r[7] === 'Yes', canManage: r[8] === 'Yes', canExport: r[9] === 'Yes'
    };
  };

  App.mapProduct = function (p) {
    if (p && p.id) return p;
    let volume = p[5];
    if (typeof volume === 'string') { try { volume = JSON.parse(volume || '[]'); } catch (e) { volume = []; } }
    return {
      id: p[0], name: p[1], category: p[2], price: Number(p[3] || 0), volumeLabel: p[4] || '',
      volume: volume || [], contents: p[6] || '', status: p[7] || 'Active', sort: Number(p[8] || 0)
    };
  };

  App.mapBranch = function (b) {
    if (b && b.id) return b;
    if (Array.isArray(b)) {
      /* [BranchID, BranchName, Address, Contact, Status, QueueMin, QueueMax, Note] */
      return {
        id: b[0], name: b[1], address: b[2], contact: b[3], status: b[4] || 'Active',
        queueMin: Number(b[5] || 1), queueMax: Number(b[6] || 18), note: b[7] || ''
      };
    }
    return {
      id: b.BranchID, name: b.BranchName, address: b.Address, contact: b.Contact,
      status: b.Status, queueMin: Number(b.QueueMin || 1), queueMax: Number(b.QueueMax || 18)
    };
  };

  /* Map a sheet-style account row to the local shape */
  App.mapAccount = function (a) {
    if (a.passwordSalt !== undefined) return a; // already local shape
    if (Array.isArray(a)) {
      /* [AccountID, Username, DisplayName, RoleID, BranchID, Status, Salt,
          PasswordHash, PinHash] - the seed ships a salt and both hashes already
          computed, so the browser and the Apps Script installer stay in sync. */
      return {
        id: a[0], username: a[1], displayName: a[2], roleId: a[3], branchId: a[4],
        status: a[5] || 'Active', passwordSalt: a[6], passwordHash: a[7],
        pinHash: a[8], createdAt: '', lastLogin: '', forceChange: 'No'
      };
    }
    return {
      id: a.AccountID, username: a.Username, displayName: a.DisplayName, roleId: a.RoleID,
      branchId: a.BranchID, status: a.Status, passwordSalt: a.PasswordSalt,
      passwordHash: a.PasswordHash, pinHash: a.PinHash,
      createdAt: a.CreatedAt, lastLogin: a.LastLogin, forceChange: a.ForceChange
    };
  };

  /* ================================================================== */
  /*  Load everything into memory                                       */
  /* ================================================================== */
  App.reload = function () {
    return Promise.all([
      DB.getAll('branches'),
      DB.getAll('roles'),
      DB.getAll('accounts'),
      DB.getAll('products'),
      DB.getAll('settings'),
      DB.getAll('categories')
    ]).then(function (r) {
      App.state.branches = U.sortBy(r[0], function (b) { return b.id; });
      App.state.roles = r[1];
      App.state.accounts = r[2];
      App.state.products = U.sortBy(r[3], function (p) { return (p.sort || 0); });
      App.state.settings = r[4].reduce(function (acc, s) {
        const k = s.key !== undefined ? s.key : s.Key;
        if (!k) return acc;
        acc[k] = s.value !== undefined ? s.value : s.Value;
        return acc;
      }, {});
      App.state.categories = r[5] || [];
      App.applySettings();
      App.applyFeatures();
      App.applyLabels();
      App.applyNavPermissions();
      return App.refreshPendingCounts();
    });
  };

  App.setting = function (key, dflt) {
    const v = App.state.settings[key];
    return (v === undefined || v === null || v === '') ? dflt : v;
  };
  App.settingBool = function (key, dflt) {
    const v = App.setting(key, dflt ? 'Yes' : 'No');
    /* The Settings sheet stores Yes/No; an app-written value may be a real boolean. */
    return App.truthy(v);
  };
  App.settingNum = function (key, dflt) { return U.num(App.setting(key, dflt), dflt); };
  App.settingList = function (key, dflt) {
    const v = App.setting(key, '');
    if (Array.isArray(v)) return v.filter(Boolean);
    const parts = String(v || '').split('|').map(function (x) { return x.trim(); }).filter(Boolean);
    return parts.length ? parts : dflt;
  };

  /* Payment methods come from Settings (PaymentMethods, pipe separated)
     so the same build works for cash-only stalls or any mix.            */
  const PAYMENT_ICONS = {
    cash: '\uD83D\uDCB5', gcash: '\uD83D\uDCF1', grab: '\uD83D\uDE97',
    card: '\uD83D\uDCB3', bank: '\uD83C\uDFE6', maya: '\uD83D\uDCF2',
    unpaid: '\u23F3', cheque: '\uD83D\uDCDD', credit: '\uD83D\uDCB3', debit: '\uD83D\uDCB3'
  };
  /* --------------------------- Feature flags ---------------------------
     A generated project can switch whole modules off (a retail shop has no
     kitchen queue, a single-branch shop has no branch selector). Flags come
     from the Settings sheet so the owner can change them without a rebuild. */
  App.FEATURES = {
    kitchen:       { setting: 'FeatureKitchen',       nav: ['kitchen'], els: ['#kdsStrip'] },
    queueCards:    { setting: 'FeatureQueueCards',    els: ['#queueBtn', '#cartQueue', '#queueSelect'] },
    nonRevenue:    { setting: 'FeatureNonRevenue',    nav: ['nonrev'] },
    unpaidOrders:  { setting: 'AllowUnpaidOrders' },
    discounts:     { setting: 'FeatureDiscounts', els: ['#discountBtn'] },
    productVolume: { setting: 'FeatureProductVolume' },
    multiBranch:   { setting: 'FeatureMultiBranch',   els: ['#branchSelect'] }
  };

  App.feature = function (name) {
    if (App.state.features && App.state.features[name] !== undefined) return App.state.features[name];
    const def = App.FEATURES[name] || {};
    return def.setting ? App.settingBool(def.setting, true) : true;
  };

  App.applyLabels = function () {
    /* Nav text that depends on the specification, not on the shop's data. */
    const types = App.nonRevenueTypes();
    const label = App.setting('NonRevenueNavLabel', '');
    const text = label || (types.length === 1 ? types[0].label
      : types.length === 2 ? types[0].label + ' & ' + types[1].label
      : 'Non-Revenue Logs');
    App.els('[data-nonrev-label]').forEach(function (el) { el.textContent = text; });
  };

  App.applyFeatures = function () {
    App.state.features = {};
    Object.keys(App.FEATURES).forEach(function (name) {
      const def = App.FEATURES[name];
      const on = def.setting ? App.settingBool(def.setting, true) : true;
      App.state.features[name] = on;
      (def.els || []).forEach(function (sel) {
        App.els(sel).forEach(function (el) { el.classList.toggle('hidden', !on); });
      });
    });
    return App.state.features;
  };

  App.paymentMethods = function () {
    return App.settingList('PaymentMethods', ['Cash', 'Card', 'Bank Transfer']);
  };
  App.paymentIcon = function (name) {
    return PAYMENT_ICONS[String(name || '').toLowerCase()] || '\uD83D\uDCB0';
  };
  /* Non-revenue types: "Label:Badge colour:Hint" per entry, pipe separated. */
  App.nonRevenueTypes = function () {
    const raw = App.settingList('NonRevenueTypes', ['Stock Loss:warn:Items that left without a sale']);
    return raw.map(function (entry) {
      /* Settings stores each type as "Label:badge:hint". The label is the id,
         so the same string works in the app, the sheet and the reports. */
      const parts = String(entry).split(':');
      return { id: parts[0], label: parts[0], cls: parts[1] || 'warn', hint: parts.slice(2).join(':') || '' };
    }).filter(function (t) { return t.id; });
  };

  App.applySettings = function () {
    const density = String(App.setting('Density', 'comfortable'));
    document.body.classList.remove('density-compact', 'density-large');
    if (density === 'compact') document.body.classList.add('density-compact');
    if (density === 'large') document.body.classList.add('density-large');
    const biz = App.setting('BusinessName', '{{BUSINESS_NAME}}');
    document.title = biz + ' \u2014 POS Tracker';
    App.els('[data-bizname]').forEach(function (n) { n.textContent = biz; });
  };

  App.refreshPendingCounts = function () {
    return DB.getAll('orders').then(function (orders) {
      const pending = orders.filter(function (o) { return o.syncStatus === 'Pending'; }).length;
      const review = orders.filter(function (o) { return o.syncStatus === 'Needs Review'; }).length;
      App.state.sync.pending = pending;
      App.state.sync.needsReview = review;
      App.renderSyncPill();
      App.els('[data-badge="orders"]').forEach(function (n) {
        if (review > 0) { n.textContent = review; n.className = 'badge danger'; n.classList.remove('hidden'); }
        else n.classList.add('hidden');
      });
      return pending;
    });
  };

  /* ================================================================== */
  /*  Prefetch: pull reference data from Google Sheets                  */
  /* ================================================================== */
  App.pullReference = function (opts) {
    opts = opts || {};
    if (!API.isConfigured()) return Promise.resolve(false);
    return API.pull().then(function (data) {
      if (!data) return false;
      const jobs = [];
      /* The sheet speaks PascalCase (BranchID, DisplayName, Key/Value); the device
         speaks camelCase. Normalise here so a pulled row behaves exactly like a
         seeded one, no matter which side wrote it. */
      const normaliseSettings = function (rows) {
        return rows.map(function (r) {
          if (!r) return null;
          if (r.key !== undefined) return r;
          return { key: r.Key !== undefined ? r.Key : r.SettingKey, value: r.Value !== undefined ? r.Value : r.SettingValue };
        }).filter(function (r) { return r && r.key; });
      };

      if (data.branches && data.branches.length) jobs.push(DB.bulkPut('branches', data.branches.map(App.mapBranch)));
      if (data.roles && data.roles.length) jobs.push(DB.bulkPut('roles', data.roles.map(App.mapRole)));
      if (data.accounts && data.accounts.length) jobs.push(DB.bulkPut('accounts', data.accounts.map(App.mapAccount)));
      if (data.products && data.products.length) jobs.push(DB.bulkPut('products', data.products.map(App.mapProduct)));
      if (data.settings && data.settings.length) jobs.push(DB.bulkPut('settings', normaliseSettings(data.settings)));
      // Owners and supervisors also receive recent orders from every branch so
      // the dashboard shows the combined picture, not just this device's sales.
      if (data.orders && data.orders.length) {
        jobs.push(DB.getAll('orders').then(function (local) {
          const have = {};
          local.forEach(function (o) { have[o.recordId] = true; });
          const fresh = data.orders.filter(function (o) { return !have[o.recordId]; })
            .map(function (o) { o.syncStatus = 'Sent'; o.pulled = true; return o; });
          return fresh.length ? DB.bulkPut('orders', fresh) : null;
        }));
      }
      return Promise.all(jobs)
        .then(function () { return DB.setSetting('lastPull', U.nowISO()); })
        .then(function () { return App.reload(); })
        .then(function () {
          if (!opts.silent) App.toast('Menu and settings updated from Google Sheets', 'ok');
          return true;
        });
    }).catch(function (e) {
      if (!opts.silent) App.toast('Could not refresh from Google Sheets', 'err', String(e && e.message || e));
      return false;
    });
  };

  /* ================================================================== */
  /*  SYNC ENGINE                                                       */
  /*  Local-first: every order is written to IndexedDB, then pushed.     */
  /* ================================================================== */
  const Sync = {};
  App.Sync = Sync;

  Sync.busy = false;
  Sync.timer = null;

  Sync.renderPill = function () { App.renderSyncPill(); };

  Sync.statusPill = function () {
    const st = App.state.sync;
    if (!navigator.onLine) return { cls: 'offline', text: 'Offline', sub: st.pending + ' waiting' };
    if (st.status === 'syncing') return { cls: 'syncing', text: 'Syncing…', sub: '' };
    if (st.needsReview > 0) return { cls: 'pending', text: 'Needs review', sub: st.needsReview + ' record(s)' };
    if (st.pending > 0) return { cls: 'pending', text: 'Pending', sub: st.pending + ' waiting' };
    if (!API.isConfigured()) return { cls: 'pending', text: 'Local only', sub: 'no server set up' };
    return { cls: 'online', text: 'Online', sub: 'all sent' };
  };

  Sync.run = function (opts) {
    opts = opts || {};
    if (Sync.busy) return Promise.resolve(false);
    if (!navigator.onLine) {
      App.state.sync.status = 'offline';
      App.renderSyncPill();
      if (!opts.silent) App.toast('Still offline — records are safe on this device', 'warn');
      return Promise.resolve(false);
    }
    Sync.busy = true;
    App.state.sync.status = 'syncing';
    App.renderSyncPill();

    return DB.getAll('orders').then(function (all) {
      const queue = all.filter(function (o) { return o.syncStatus !== 'Sent'; });
      return DB.getAll('nonrev').then(function (nr) {
        const nrQueue = nr.filter(function (n) { return n.syncStatus !== 'Sent'; });
        return DB.getAll('eod').then(function (eod) {
          const eodQueue = eod.filter(function (e) { return e.syncStatus !== 'Sent'; });
          return pushAll(queue, nrQueue, eodQueue);
        });
      });
    }).then(function (summary) {
      Sync.busy = false;
      App.state.sync.status = 'idle';
      App.state.sync.lastRun = U.nowISO();
      return App.refreshPendingCounts().then(function () {
        App.renderSyncPill();
        if (!opts.silent) {
          if (summary.sent > 0) App.toast('Synced ' + summary.sent + ' record(s) to Google Sheets', 'ok',
            summary.failed ? summary.failed + ' still waiting' : '');
          else if (summary.failed > 0) App.toast(summary.failed + ' record(s) could not be sent', 'warn', 'They stay in the sync queue.');
          else App.toast('Everything is already up to date', 'ok');
        }
        if (opts.then) opts.then(summary);
        return summary;
      });
    }).catch(function (err) {
      Sync.busy = false;
      App.state.sync.status = 'idle';
      App.state.sync.lastError = String(err && err.message || err);
      App.renderSyncPill();
      if (!opts.silent) App.toast('Sync error', 'err', App.state.sync.lastError);
      return { sent: 0, failed: 0 };
    });
  };

  function pushAll(orders, nonrev, eod) {
    const summary = { sent: 0, failed: 0, review: 0 };
    const steps = [];

    // 1) Non-revenue logs
    if (nonrev.length && API.isConfigured()) {
      steps.push(function () {
        return API.pushNonRevenue(nonrev).then(function (r) {
          if (r.ok) {
            return Promise.all(nonrev.map(function (n) {
              n.syncStatus = 'Sent'; n.sentAt = U.nowISO();
              return DB.put('nonrev', n);
            })).then(function () { summary.sent += nonrev.length; });
          }
          return markFailed('nonrev', nonrev, r.error);
        });
      });
    }

    // 2) End of day
    if (eod.length && API.isConfigured()) {
      steps.push(function () {
        return API.pushEOD(eod).then(function (r) {
          if (r.ok) {
            return Promise.all(eod.map(function (n) {
              n.syncStatus = 'Sent'; n.sentAt = U.nowISO();
              return DB.put('eod', n);
            })).then(function () { summary.sent += eod.length; });
          }
          return markFailed('eod', eod, r.error);
        });
      });
    }

    // 3) Orders
    steps.push(function () {
      if (!orders.length) return Promise.resolve();
      if (!API.isConfigured()) return markLocal(orders);
      // Send in small batches so a weak connection can still complete
      const batches = [];
      for (let i = 0; i < orders.length; i += 15) batches.push(orders.slice(i, i + 15));
      return batches.reduce(function (p, batch) {
        return p.then(function () {
          return API.pushOrders(batch).then(function (res) {
            if (!res || (!res.ok && !res.results.length)) {
              summary.failed += batch.length;
              return logSync(batch, 'Pending', res && res.error || 'Network error');
            }
            const results = res.results || [];
            return Promise.all(results.map(function (r) {
              const local = batch.find(function (b) { return b.recordId === r.recordId; });
              if (!local) return null;
              if (r.status === 'ok' || r.status === 'duplicate') {
                local.syncStatus = 'Sent';
                local.sentAt = U.nowISO();
                local.serverRow = r.row || '';
                summary.sent++;
                return logSync([local], 'Sent', r.status === 'duplicate' ? 'Duplicate ignored (already on sheet)' : '');
              }
              if (r.status === 'conflict' || r.status === 'review') {
                local.syncStatus = 'Needs Review';
                local.needsReviewReason = r.message || 'Changed on another device';
                summary.review++;
                summary.failed++;
                return logSync([local], 'Needs Review', local.needsReviewReason);
              }
              local.needsReviewReason = r.message || 'Rejected by server';
              if (r.status === 'deleted') { local.syncStatus = 'Sent'; return DB.put('orders', local); }
              summary.failed++;
              return logSync([local], local.syncStatus, local.needsReviewReason);
            })).then(function () {
              return Promise.all(batch.map(function (o) { return DB.put('orders', o); }));
            });
          });
        });
      }, Promise.resolve());
    });

    return steps.reduce(function (p, fn) { return p.then(fn); }, Promise.resolve()).then(function () { return summary; });
  }

  function markLocal(orders) {
    // Demo mode: nothing to send, keep them Pending and say so clearly.
    return Promise.resolve();
  }

  function markFailed(store, rows, err) {
    return logSync(rows, 'Pending', err || 'Network error');
  }

  function logSync(rows, result, note) {
    const entries = rows.map(function (r) {
      return {
        recordId: r.recordId, deviceId: API.deviceId(), deviceLabel: API.deviceLabel(),
        sentAt: U.nowISO(), result: result || r.syncStatus, note: note || '',
        branchId: r.branchId, kind: r.lineItems ? 'Order' : 'Record'
      };
    });
    return DB.bulkPut('synclog', []).then(function () {
      return entries.reduce(function (p, e) { return p.then(function () { return DB.put('synclog', e); }); }, Promise.resolve());
    }).then(function () {
      return DB.getAll('synclog').then(function (all) {
        if (all.length > 400) {
          const sorted = U.sortBy(all, function (a) { return a.id || 0; });
          const drop = sorted.slice(0, all.length - 400);
          return drop.reduce(function (p, d) { return p.then(function () { return DB.delete('synclog', d.id); }); }, Promise.resolve());
        }
      });
    });
  }
  Sync.log = logSync;

  Sync.start = function () {
    if (Sync.timer) clearInterval(Sync.timer);
    if (!App.settingBool('AutoSync', true)) return;
    const sec = Math.max(15, App.settingNum('SyncIntervalSec', 45));
    Sync.timer = setInterval(function () {
      if (navigator.onLine && App.state.session && API.isConfigured()) Sync.run({ silent: true });
    }, sec * 1000);
  };

  Sync.restart = function () { Sync.start(); };

  /* ================================================================== */
  /*  Branding / shell rendering                                        */
  /* ================================================================== */
  App.renderSyncPill = function () {
    const host = document.getElementById('syncPill');
    if (!host) return;
    const s = Sync.statusPill();
    host.className = 'pill ' + s.cls;
    host.innerHTML = '<span class="dot"></span>' + U.esc(s.text) +
      (s.sub ? '<span class="hide-sm" style="opacity:.7;font-weight:600"> \u00B7 ' + U.esc(s.sub) + '</span>' : '');
  };

  App.renderUser = function () {
    const s = App.state.session;
    if (!s) return;
    const av = document.getElementById('userAvatar');
    const nm = document.getElementById('userName');
    const rl = document.getElementById('userRole');
    if (av) av.textContent = U.initial(s.displayName);
    if (nm) nm.textContent = s.displayName;
    if (rl) rl.textContent = s.roleName + ' \u00B7 ' + App.branchCode(s.branchId);
  };

  App.renderBranchSelect = function () {
    const sel = document.getElementById('branchSelect');
    if (!sel) return;
    const list = App.branchesVisible();
    if (list.length <= 1 && !App.isAdmin()) {
      sel.classList.add('hidden');
      return;
    }
    sel.innerHTML = list.map(function (b) {
      return '<option value="' + U.esc(b.id) + '"' + (b.id === App.state.branchId ? ' selected' : '') + '>' +
        U.esc(b.name) + '</option>';
    }).join('');
    /* A single-location shop should not see a branch switcher at all. */
    sel.classList.toggle('hidden', !App.feature('multiBranch'));
  };

  App.applyNavPermissions = function () {
    App.els('[data-nav]').forEach(function (n) {
      const need = n.getAttribute('data-perm');
      const navId = n.getAttribute('data-nav');
      let featureOk = true;
      Object.keys(App.FEATURES).forEach(function (f) {
        const def = App.FEATURES[f];
        if ((def.nav || []).indexOf(navId) >= 0 && !App.feature(f)) featureOk = false;
      });
      const show = (!need || App.can(need)) && featureOk;
      n.classList.toggle('hidden', !show);
    });
    App.els('[data-nav-section]').forEach(function (n) {
      const kids = n.parentNode.querySelectorAll('[data-nav]:not(.hidden)');
      n.classList.toggle('hidden', kids.length === 0);
    });
  };

  /* ================================================================== */
  /*  Routing                                                           */
  /* ================================================================== */
  const PAGES = ['pos', 'kitchen', 'orders', 'reports', 'nonrev', 'products', 'accounts', 'sync', 'settings'];

  App.go = function (page) {
    /* A switched-off module is not reachable, even from a stale link. */
    const gate = Object.keys(App.FEATURES).find(function (f) {
      return (App.FEATURES[f].nav || []).indexOf(page) >= 0 && !App.feature(f);
    });
    if (gate) page = 'pos';
    if (PAGES.indexOf(page) < 0) page = 'pos';
    App.state.page = page;
    App.els('[data-nav]').forEach(function (n) {
      n.classList.toggle('active', n.getAttribute('data-nav') === page);
    });
    App.els('[data-page]').forEach(function (n) {
      n.classList.toggle('hidden', n.getAttribute('data-page') !== page);
    });

    const titles = {
      pos: ['Point of Sale', 'Fast checkout \u00B7 quantity entry \u00B7 exact change'],
      kitchen: ['Kitchen Display', 'Live queue \u00B7 Preparing \u2192 Ready \u2192 Served'],
      orders: ['Orders & History', 'Search, correct, void, and reprint'],
      reports: ['Sales Reports', 'Revenue, payment types, product volume'],
      nonrev: [App.setting('NonRevenuePageTitle', 'Non-Revenue Logs'),
               App.setting('NonRevenuePageHint', 'Stock that left without a sale is counted here, never as revenue.')],
      products: ['Products & Menu', 'Prices, categories, product volume'],
      accounts: ['Accounts & Roles', 'Staff access, passwords, permissions'],
      sync: ['Sync & Backup', 'Pending records, device data, backups'],
      settings: ['Settings', 'Branch, queue, receipts, and connection']
    };
    const t = titles[page] || ['', ''];
    const h = document.getElementById('pageTitle');
    const sub = document.getElementById('pageSub');
    if (h) h.textContent = t[0];
    if (sub) sub.textContent = t[1];

    const kds = document.getElementById('kdsStrip');
    if (kds) kds.classList.toggle('hidden', !(page === 'pos' && App.can('kitchen.view') && App.feature('kitchen')));

    const content = document.getElementById('content');
    if (content) content.scrollTop = 0;

    // Close mobile drawer
    document.getElementById('sidebar').classList.remove('open');
    const scrim = document.querySelector('.sidebar-scrim');
    if (scrim) scrim.remove();

    const renderers = {
      pos: function () { return global.POS && POS.render(); },
      kitchen: function () { return global.Kitchen && Kitchen.render(); },
      orders: function () { return global.Orders && Orders.render(); },
      reports: function () { return global.Reports && Reports.render(); },
      nonrev: function () { return global.NonRev && NonRev.render(); },
      products: function () { return global.Admin && Admin.renderProducts(); },
      accounts: function () { return global.Admin && Admin.renderAccounts(); },
      sync: function () { return global.SyncPage && SyncPage.render(); },
      settings: function () { return global.SettingsPage && SettingsPage.render(); }
    };
    if (renderers[page]) renderers[page]();
  };

  /* ================================================================== */
  /*  Login flow                                                        */
  /* ================================================================== */
  App.doLogin = function (username, password) {
    const btn = document.getElementById('loginBtn');
    btn.disabled = true; btn.textContent = 'Signing in…';
    const done = function (res) {
      btn.disabled = false; btn.textContent = 'Sign in';
      return res;
    };
    const applySession = function (acc, offline) {
      const role = App.state.roles.find(function (r) { return r.id === acc.roleId; }) || {};
      const session = {
        accountId: acc.id, username: acc.username, displayName: acc.displayName,
        roleId: acc.roleId, roleName: role.name || acc.roleId,
        branchId: acc.branchId, isAdmin: App.truthy(role.isAdmin) || acc.roleId === 'ROLE-ADMIN',
        permissions: role.permissions || [], allBranches: App.truthy(role.allBranches),
        canPOS: role.canPOS === undefined || App.truthy(role.canPOS),
        canKitchen: role.canKitchen === undefined || App.truthy(role.canKitchen),
        canManage: App.truthy(role.canManage), canExport: App.truthy(role.canExport),
        offline: !!offline, loginAt: U.nowISO()
      };
      App.state.session = session;
      App.state.branchId = acc.branchId || (App.state.branches[0] && App.state.branches[0].id) || '';
      App.saveSession();
      U.ls.set('last_user', acc.username);
    };

    const finish = function (acc, offline) {
      applySession(acc, offline);
      document.getElementById('loginError').classList.add('hidden');
      document.getElementById('password').value = '';
      App.start();
      App.toast('Welcome, ' + acc.displayName.split(' ')[0] + '!', 'ok', offline ? 'Signed in offline using this device\'s copy.' : '');
    };

    if (navigator.onLine && API.isConfigured()) {
      return API.login(username, password).then(function (r) {
        if (r.ok && r.data && r.data.account) {
          const a = r.data.account;
          // cache the account locally too
          return DB.put('accounts', App.mapAccount({
            id: a.id, username: a.username, displayName: a.displayName, roleId: a.roleId,
            branchId: a.branchId, status: a.status, passwordSalt: a.salt,
            passwordHash: a.passwordHash, createdAt: a.createdAt, forceChange: a.forceChange
          })).then(function () { done(); finish(App.mapAccountCamel(a), false); return true; });
        }
        if (r.offline) {
          return API.LocalAuth.offlineLogin(username, password).then(function (lr) {
            if (lr.ok) {
              const acc = App.state.accounts.find(function (x) { return x.id === lr.data.account.id; });
              done(); finish(acc, true); return true;
            }
            done();
            App.loginError(r.error || 'Cannot reach the server, and this account is not saved on this device.');
            return false;
          });
        }
        done();
        App.loginError(r.error || 'Wrong username or password.');
        return false;
      });
    }

    return API.LocalAuth.offlineLogin(username, password).then(function (lr) {
      done();
      if (!lr.ok) { App.loginError(lr.error); return false; }
      const acc = App.state.accounts.find(function (x) { return x.id === lr.data.account.id; });
      finish(acc, true);
      return true;
    });
  };

  App.mapAccountCamel = function (a) {
    return {
      id: a.id, username: a.username, displayName: a.displayName, roleId: a.roleId,
      branchId: a.branchId, status: a.status, passwordSalt: a.salt, passwordHash: a.passwordHash
    };
  };

  App.loginError = function (msg) {
    const box = document.getElementById('loginError');
    box.textContent = msg;
    box.classList.remove('hidden');
  };

  /* ================================================================== */
  /*  Start the app for a signed-in user                                */
  /* ================================================================== */
  App.start = function () {
    document.getElementById('loginScreen').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
    App.renderUser();
    App.renderBranchSelect();
    App.applyFeatures();
    App.applyLabels();
    App.applyNavPermissions();
    App.renderSyncPill();
    App.applySettings();

    const s = App.state.session;
    let first = 'pos';
    if (!s.canPOS && s.canKitchen) first = 'kitchen';
    if (!s.canPOS && !s.canKitchen && s.canManage) first = 'reports';

    App.go(first);
    if (typeof Kitchen !== 'undefined' && App.feature('kitchen')) Kitchen.startTicker();
    if (typeof POS !== 'undefined') POS.init();
    Sync.start();
    if (navigator.onLine && API.isConfigured()) {
      App.pullReference({ silent: true }).then(function () { App.renderBranchSelect(); });
      Sync.run({ silent: true });
    }
  };

  /* ================================================================== */
  /*  Boot                                                              */
  /* ================================================================== */
  App.boot = function () {
    DB.init().then(function () {
      return App.seedIfEmpty();
    }).then(function () {
      return App.reload();
    }).then(function () {
      App.state.ready = true;
      document.getElementById('bootScreen').classList.add('hidden');
      document.getElementById('loginScreen').classList.remove('hidden');

      // Fill the connection status on the login card
      const st = document.getElementById('connStatus');
      if (st) {
        if (API.isConfigured()) {
          st.innerHTML = '<span class="dot" style="background:var(--ok)"></span> Connected to Google Sheets \u00B7 ' +
            U.esc(App.setting('BusinessName', '{{BUSINESS_NAME}}'));
        } else {
          st.innerHTML = '<span class="dot" style="background:var(--warn)"></span> Local mode \u2014 no Google Sheet connected yet ' +
            '<button class="btn btn-ghost btn-sm" id="openSetup" style="margin-left:8px">Set up</button>';
          const b = document.getElementById('openSetup');
          if (b) b.onclick = function () { SettingsPage.connectionModal(); };
        }
      }

      const lastUser = U.ls.get('last_user', '');
      if (lastUser) {
        const u = document.getElementById('username');
        if (u) u.value = lastUser;
        const p = document.getElementById('password');
        if (p) setTimeout(function () { p.focus(); }, 200);
      }

      if (App.loadSession()) {
        // Session survives a reload: go straight in
        App.start();
      } else if (navigator.onLine === false) {
        App.loginError('This device is offline. You can still sign in with an account saved on this device.');
      }
      if (!U.ls.persistent) {
        setTimeout(function () {
          App.toast('Preview mode \u2014 records are kept in memory only', 'warn',
            'Open app/index.html (or the installed APK/EXE) to keep data after closing.');
        }, 900);
      }
      App.attachShellEvents();
    }).catch(function (err) {
      console.error(err);
      const b = document.getElementById('bootText');
      if (b) b.textContent = 'Could not start the database: ' + (err && err.message || err);
    });
  };

  App.attachShellEvents = function () {
    const loginSubmit = function (e) {
      if (e) e.preventDefault();
      App.doLogin(document.getElementById('username').value.trim(), document.getElementById('password').value);
    };
    const loginForm = document.getElementById('loginForm');
    // A sandboxed preview iframe blocks real form submissions, so the button
    // is type="button" and Enter is handled explicitly. Works everywhere.
    loginForm.addEventListener('submit', loginSubmit);
    document.getElementById('loginBtn').addEventListener('click', loginSubmit);
    ['username', 'password'].forEach(function (id) {
      const el = document.getElementById(id);
      if (el) el.addEventListener('keydown', function (e) { if (e.key === 'Enter') loginSubmit(e); });
    });

    App.els('[data-nav]').forEach(function (n) {
      n.addEventListener('click', function () { App.go(n.getAttribute('data-nav')); });
    });

    const mt = document.getElementById('menuToggle');
    if (mt) mt.addEventListener('click', function () {
      const sb = document.getElementById('sidebar');
      if (sb.classList.contains('open')) { sb.classList.remove('open'); const s = document.querySelector('.sidebar-scrim'); if (s) s.remove(); }
      else {
        sb.classList.add('open');
        const scrim = document.createElement('div');
        scrim.className = 'sidebar-scrim';
        scrim.onclick = function () { sb.classList.remove('open'); scrim.remove(); };
        document.body.appendChild(scrim);
      }
    });

    const so = document.getElementById('signOutBtn');
    if (so) so.addEventListener('click', function () {
      App.confirm('Sign out?', 'Any records still pending will stay saved on this device and sync the next time this account signs in.', { okText: 'Sign out' })
        .then(function (yes) { if (yes) App.logout(); });
    });

    const bs = document.getElementById('branchSelect');
    if (bs) bs.addEventListener('change', function () {
      App.state.branchId = bs.value;
      App.go(App.state.page);
    });

    const sp = document.getElementById('syncPill');
    if (sp) sp.addEventListener('click', function () { Sync.run({}); });

    window.addEventListener('online', function () {
      App.renderSyncPill();
      App.toast('Back online — sending waiting records…', 'ok');
      Sync.run({ silent: true });
    });
    window.addEventListener('offline', function () {
      App.renderSyncPill();
      App.toast('Internet lost — the app keeps working offline', 'warn');
    });

    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && navigator.onLine && API.isConfigured() && App.state.session) Sync.run({ silent: true });
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'F2' && App.state.page === 'pos') { e.preventDefault(); POS.openCheckout(); }
      if (e.key === 'F9' && App.state.page === 'pos') { e.preventDefault(); POS.focusSearch(); }
    });
  };

  App.platform = (function () {
    const ua = navigator.userAgent || '';
    if (window.cordova || /Android/i.test(ua)) return 'android';
    if (/Electron/i.test(ua)) return 'electron';
    return 'web';
  })();

  document.addEventListener('DOMContentLoaded', function () {
    document.body.classList.add('platform-' + App.platform);
    App.boot();
  });

  global.App = App;
})(window);
