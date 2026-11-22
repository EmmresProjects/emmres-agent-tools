/* =====================================================================
   {{KIT_TITLE}} - Sync & Backup page
   Pending / Needs Review queue, sync log, device backup and restore.
   ===================================================================== */
(function (global) {
  'use strict';

  const SyncPage = {};

  SyncPage.render = function () {
    const host = document.getElementById('syncBody');
    if (!host) return;

    Promise.all([
      DB.getAll('orders'), DB.getAll('nonrev'), DB.getAll('synclog'), DB.getAll('eod')
    ]).then(function (r) {
      const orders = r[0], nonrev = r[1], log = r[2], eod = r[3];
      const pendingOrders = orders.filter(function (o) { return o.syncStatus !== 'Sent'; });
      const reviewOrders = orders.filter(function (o) { return o.syncStatus === 'Needs Review'; });
      const pendingNr = nonrev.filter(function (n) { return n.syncStatus !== 'Sent'; });
      const lastPull = U.ls.get('last_pull_label', '') || '—';
      const lastSync = App.state.sync.lastRun;

      host.innerHTML =
        '<div class="grid g-4 mb-2">' +
          '<div class="metric m-warn"><div class="label">Waiting to send</div><div class="value">' + (pendingOrders.length + pendingNr.length) + '</div>' +
            '<div class="foot">' + pendingOrders.length + ' orders \u00B7 ' + pendingNr.length + ' non-revenue</div></div>' +
          '<div class="metric m-purple"><div class="label">Needs review</div><div class="value">' + reviewOrders.length + '</div>' +
            '<div class="foot">changed on two devices</div></div>' +
          '<div class="metric m-ok"><div class="label">Sent records</div><div class="value">' + orders.filter(function (o) { return o.syncStatus === 'Sent'; }).length + '</div>' +
            '<div class="foot">from this device</div></div>' +
          '<div class="metric m-info"><div class="label">Last sync</div><div class="value" style="font-size:1.1rem">' +
            (lastSync ? U.time12(lastSync) : '—') + '</div><div class="foot">' + API.deviceLabel() + '</div></div>' +
        '</div>' +

        '<div class="card mb-2">' +
          '<div class="card-head"><h3>Connection</h3><div class="grow"></div>' +
            '<button class="btn btn-ghost btn-sm" id="syCfg">Server settings</button>' +
            '<button class="btn btn-primary btn-sm" id="syRun">Sync now</button></div>' +
          '<div class="grid g-3">' +
            '<div><div class="label small muted">Apps Script URL</div><div class="mono small" style="word-break:break-all">' +
              (API.getUrl() ? U.esc(API.getUrl()) : '<span style="color:var(--danger)">Not set</span>') + '</div></div>' +
            '<div><div class="label small muted">Access token</div><div class="mono small">' +
              (API.getToken() ? '\u2022'.repeat(8) + ' set' : '<span style="color:var(--warn)">Not set</span>') + '</div></div>' +
            '<div><div class="label small muted">This device</div><div class="small">' + U.esc(API.deviceLabel()) +
              ' <span class="mono muted">' + U.esc(API.deviceId()) + '</span><br>' +
              (navigator.onLine ? '<span style="color:var(--ok)">Online</span>' : '<span style="color:var(--danger)">Offline</span>') +
              ' \u00B7 auto-sync ' + (App.settingBool('AutoSync', true) ? 'every ' + App.settingNum('SyncIntervalSec', 45) + 's' : 'off') +
            '</div></div>' +
          '</div>' +
        '</div>' +

        (reviewOrders.length ?
        '<div class="card mb-2" style="border-color:rgba(176,123,255,.4)">' +
          '<div class="card-head"><h3 style="color:var(--purple)">Needs review (' + reviewOrders.length + ')</h3>' +
            '<span class="sub">Two devices changed the same record. Nothing was overwritten.</span></div>' +
          '<div class="table-wrap"><table class="tbl"><thead><tr><th>Order</th><th>Date</th><th class="num">Total</th><th>Reason</th><th></th></tr></thead><tbody>' +
            reviewOrders.map(function (o) {
              return '<tr><td class="mono small">' + U.esc(o.orderNo) + '</td>' +
                '<td class="small">' + U.fmtDate(o.businessDate) + '</td>' +
                '<td class="num">' + U.money(o.total) + '</td>' +
                '<td class="small" style="color:var(--purple)">' + U.esc(o.needsReviewReason || 'Conflict') + '</td>' +
                '<td class="right nowrap">' +
                  '<button class="btn btn-ghost btn-sm" data-rev-view="' + U.esc(o.recordId) + '">Open</button>' +
                  '<button class="btn btn-ghost btn-sm" data-rev-ok="' + U.esc(o.recordId) + '">Resend</button>' +
                '</td></tr>';
            }).join('') + '</tbody></table></div></div>' : '') +

        '<div class="card mb-2">' +
          '<div class="card-head"><h3>Waiting to send (' + pendingOrders.length + ')</h3>' +
            '<span class="sub">Saved on this device. They go to Google Sheets automatically when the connection is back.</span></div>' +
          (pendingOrders.length ?
            '<div class="table-wrap"><table class="tbl"><thead><tr><th>Order</th><th>Created</th><th>Branch</th><th class="num">Total</th><th>Status</th><th>Why waiting</th></tr></thead><tbody>' +
            U.sortBy(pendingOrders, function (o) { return o.createdAt; }, 'desc').map(function (o) {
              return '<tr><td class="mono small">' + U.esc(o.orderNo) + '</td>' +
                '<td class="small">' + U.fmtDateTime(o.createdAt) + '</td>' +
                '<td class="small">' + U.esc(App.branchCode(o.branchId)) + '</td>' +
                '<td class="num">' + U.money(o.total) + '</td>' +
                '<td><span class="badge ' + (o.syncStatus === 'Needs Review' ? 'purple' : 'warn') + '">' + U.esc(o.syncStatus) + '</span></td>' +
                '<td class="small dim">' + U.esc(o.needsReviewReason || (navigator.onLine ? 'Waiting for sync' : 'Device is offline')) + '</td></tr>';
            }).join('') + '</tbody></table></div>'
            : '<div class="empty-state" style="padding:26px"><div class="es-ico">\u2705</div><p>Nothing waiting. Everything this device recorded is on Google Sheets.</p></div>') +
        '</div>' +

        '<div class="grid g-2">' +
          '<div class="card">' +
            '<div class="card-head"><h3>Backup &amp; restore</h3></div>' +
            '<p class="small muted" style="margin-top:0">A backup file holds everything on this device: orders, accounts, products and settings. Keep one before changing tablets.</p>' +
            '<div class="row">' +
              '<button class="btn btn-primary" id="bkExport">\u2B07 Back up device data</button>' +
              '<button class="btn btn-ghost" id="bkImport">\u2B06 Restore from backup</button>' +
              '<input type="file" id="bkFile" accept=".json" class="hidden">' +
            '</div>' +
            '<div class="sep"></div>' +
            '<h4 class="small" style="margin:0 0 8px">Maintenance</h4>' +
            '<div class="row">' +
              '<button class="btn btn-ghost btn-sm" id="mtPull">Pull from Sheets</button>' +
              '<button class="btn btn-ghost btn-sm" id="mtLog">Sync log</button>' +
              '<button class="btn btn-ghost btn-sm" id="mtReset" style="color:var(--danger)">Reset this device</button>' +
            '</div>' +
          '</div>' +
          '<div class="card">' +
            '<div class="card-head"><h3>How syncing works</h3></div>' +
            '<ol class="small muted" style="margin:0;padding-left:18px;line-height:1.8">' +
              '<li>Every order is saved on this device <strong>first</strong>.</li>' +
              '<li>If the internet is up, it is sent to Google Sheets right away.</li>' +
              '<li>If not, it stays <strong>Pending</strong> and keeps retrying every ' + App.settingNum('SyncIntervalSec', 45) + ' seconds.</li>' +
              '<li>Each record carries a unique Record ID, so a retry never creates a duplicate.</li>' +
              '<li>If two devices edit the same record, it is parked as <strong>Needs Review</strong> instead of silently overwriting.</li>' +
            '</ol>' +
          '</div>' +
        '</div>';

      const run = document.getElementById('syRun');
      if (run) run.onclick = function () {
        run.disabled = true; run.textContent = 'Syncing…';
        App.Sync.run({}).then(function (s) {
          run.disabled = false; run.textContent = 'Sync now';
          SyncPage.render();
          void s;
        });
      };
      const cfg = document.getElementById('syCfg');
      if (cfg) cfg.onclick = function () { SettingsPage.connectionModal(); };

      host.querySelectorAll('[data-rev-ok]').forEach(function (b) {
        b.onclick = function () {
          DB.get('orders', b.getAttribute('data-rev-ok')).then(function (o) {
            if (!o) return;
            o.syncStatus = 'Pending'; o.needsReviewReason = '';
            return DB.put('orders', o);
          }).then(function () {
            App.toast('Marked for resending', 'ok');
            App.refreshPendingCounts();
            SyncPage.render();
            if (navigator.onLine) App.Sync.run({ silent: true });
          });
        };
      });
      host.querySelectorAll('[data-rev-view]').forEach(function (b) {
        b.onclick = function () { Orders.openDetail(b.getAttribute('data-rev-view')); };
      });

      document.getElementById('bkExport').onclick = SyncPage.backup;
      document.getElementById('bkImport').onclick = function () { document.getElementById('bkFile').click(); };
      document.getElementById('bkFile').onchange = SyncPage.restore;
      document.getElementById('mtPull').onclick = function () {
        App.pullReference({}).then(function (ok) {
          if (!ok) App.toast('Nothing pulled', 'warn', 'Check the server settings and your connection.');
          SyncPage.render();
        });
      };
      document.getElementById('mtLog').onclick = SyncPage.logModal;
      document.getElementById('mtReset').onclick = SyncPage.resetDevice;
    });
  };

  SyncPage.backup = function () {
    DB.exportAll().then(function (data) {
      const payload = {
        app: '{{KIT_TITLE}}', version: APP_VERSION,
        exportedAt: U.nowISO(), deviceId: API.deviceId(), deviceLabel: API.deviceLabel(),
        data: data
      };
      U.download('{{BRAND_SLUG}}-backup-' + U.todayKey() + '-' + API.deviceId() + '.json',
        JSON.stringify(payload, null, 1), 'application/json');
      App.toast('Backup created', 'ok', 'Keep this file somewhere safe (email, drive, USB).');
    });
  };

  SyncPage.restore = function (e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function () {
      let payload;
      try { payload = JSON.parse(reader.result); }
      catch (err) { App.toast('That file is not a valid backup', 'err'); return; }
      if (!payload || !payload.data) { App.toast('This backup is missing its data section', 'err'); return; }

      App.confirm('Restore this backup?',
        'Backup from <strong>' + U.esc(payload.exportedAt ? U.fmtDateTime(payload.exportedAt) : 'unknown date') + '</strong> on device ' +
        U.esc(payload.deviceLabel || payload.deviceId || '?') + '.<br><br>' +
        'The current data on this device will be replaced. Anything already sent to Google Sheets stays there.',
        { danger: true, okText: 'Restore' }).then(function (yes) {
        if (!yes) return;
        DB.importAll(payload.data).then(function () {
          return App.reload();
        }).then(function () {
          App.toast('Backup restored', 'ok', 'Reloading…');
          setTimeout(function () { location.reload(); }, 900);
        });
      });
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  SyncPage.logModal = function () {
    DB.getAll('synclog').then(function (log) {
      const rows = U.sortBy(log, function (l) { return l.id || 0; }, 'desc').slice(0, 200);
      App.modal({
        title: 'Sync log',
        size: 'wide',
        body: rows.length
          ? '<div class="table-wrap"><table class="tbl"><thead><tr><th>When</th><th>Record</th><th>Device</th><th>Result</th><th>Note</th></tr></thead><tbody>' +
            rows.map(function (l) {
              return '<tr><td class="small nowrap">' + U.time12(l.sentAt) + '</td>' +
                '<td class="mono small">' + U.esc(String(l.recordId || '').slice(0, 8)) + '</td>' +
                '<td class="small">' + U.esc(l.deviceLabel || '') + '</td>' +
                '<td><span class="badge ' + (l.result === 'Sent' ? 'ok' : l.result === 'Needs Review' ? 'purple' : 'warn') + '">' +
                  U.esc(l.result || '') + '</span></td>' +
                '<td class="small dim">' + U.esc(l.note || '') + '</td></tr>';
            }).join('') + '</tbody></table></div>'
          : '<p class="muted">No sync activity recorded yet.</p>',
        footer: '<button class="btn btn-ghost" data-close>Close</button>'
      });
    });
  };

  SyncPage.resetDevice = function () {
    App.modal({
      title: 'Reset this device',
      size: 'narrow',
      body:
        '<p class="small" style="color:var(--danger);font-weight:700;margin-top:0">This erases all data stored on this device.</p>' +
        '<p class="small muted">Orders already sent to Google Sheets are not affected. Records still waiting to send <strong>will be lost</strong>. ' +
        'Back up first if you are not sure.</p>' +
        '<div class="field"><label>Type RESET to confirm</label><input class="input" id="rz" placeholder="RESET"></div>',
      footer: '<button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-danger" data-go>Erase device data</button>',
      onMount: function (m, close) {
        m.querySelector('[data-go]').onclick = function () {
          if (m.querySelector('#rz').value.trim().toUpperCase() !== 'RESET') { App.toast('Type RESET to confirm', 'err'); return; }
          DB.clearAll().then(function () {
            U.ls.del('session');
            U.ls.del('auth_mirror');
            App.toast('Device data erased', 'ok', 'Reloading…');
            setTimeout(function () { location.reload(); }, 900);
          });
        };
      }
    });
  };

  global.SyncPage = SyncPage;
})(window);
