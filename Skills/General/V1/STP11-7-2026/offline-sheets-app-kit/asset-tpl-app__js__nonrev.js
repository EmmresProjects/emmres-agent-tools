/* =====================================================================
   {{KIT_TITLE}} - Non-Revenue Logs
   Non-revenue entries: counted per item, never added
   to cash sales. Both go to the Non-Revenue Logs sheet.
   ===================================================================== */
(function (global) {
  'use strict';

  const NonRev = {};
  const TYPES = function () { return App.nonRevenueTypes(); };

  function entryRow(l) {
    const items = (l.items || []).map(function (i) { return i.qty + '\u00D7 ' + i.name; }).join(', ');
    const type = TYPES().find(function (t) { return t.id === l.logType; }) || TYPES()[0] || { cls: 'warn', label: l.logType };
    return '<tr>' +
      '<td class="small nowrap">' + U.fmtDate(l.businessDate) + '<br><span class="muted">' + U.time12(l.createdAt) + '</span></td>' +
      '<td><span class="badge ' + type.cls + '">' + U.esc(type.label) + '</span></td>' +
      '<td class="small">' + U.esc(App.branchCode(l.branchId)) + '</td>' +
      '<td class="small">' + U.esc(items) + (l.note ? '<div class="muted">' + U.esc(l.note) + '</div>' : '') + '</td>' +
      '<td class="small">' + U.esc(l.loggedByName || '') + '</td>' +
      '<td>' + (l.syncStatus === 'Sent' ? '<span class="badge ok">Sent</span>' : '<span class="badge warn">Pending</span>') + '</td>' +
      '<td class="right nowrap">' +
        (l.syncStatus !== 'Sent' ? '<button class="btn btn-ghost btn-sm" data-del="' + U.esc(l.recordId) + '">Delete</button>' : '') +
      '</td>' +
    '</tr>';
  }

  NonRev.render = function () {
    const host = document.getElementById('nonrevBody');
    if (!host) return;

    DB.getAll('nonrev').then(function (logs) {
      const branch = App.state.branchId;
      const mine = logs.filter(function (l) { return App.isAdmin() ? true : l.branchId === App.myBranch(); })
        .sort(function (a, b) { return new Date(b.createdAt) - new Date(a.createdAt); });

      const today = mine.filter(function (l) { return l.businessDate === U.todayKey(); });
      const sum = function (list, type) {
        return U.sum(list.filter(function (l) { return l.logType === type; }),
          function (l) { return U.sum(l.items || [], function (i) { return i.qty; }); });
      };
      const TYPES_L = TYPES();

      host.innerHTML =
        '<div class="grid g-' + Math.min(4, TYPES_L.length + 1) + ' mb-2">' +
          TYPES_L.map(function (t) {
            return '<div class="metric m-' + (t.cls === 'info' ? 'info' : t.cls === 'danger' ? 'warn' : 'warn') + '">' +
              '<div class="label">' + U.esc(t.label) + ' today</div><div class="value">' + sum(today, t.id) + '</div>' +
              '<div class="foot">item count, not sales</div></div>';
          }).join('') +
          '<div class="metric"><div class="label">Entries today</div><div class="value">' + today.length + '</div>' +
            '<div class="foot">' + mine.filter(function (l) { return l.syncStatus !== 'Sent'; }).length + ' waiting to sync</div></div>' +
        '</div>' +

        '<div class="card card-pad-0">' +
          '<div class="card-head" style="padding:14px 16px 0"><h3>Non-revenue entries</h3>' +
            '<span class="sub">' + (App.isAdmin() ? 'All branches' : App.branchName(App.myBranch())) + '</span>' +
            '<div class="grow"></div>' +
            '<button class="btn btn-primary btn-sm" id="nrAdd">+ New entry</button></div>' +
          '<div class="table-wrap mt-1" id="nrTable">' +
            (mine.length
              ? '<table class="tbl"><thead><tr><th>When</th><th>Type</th><th>Branch</th><th>Items</th><th>Logged by</th><th>Sync</th><th></th></tr></thead>' +
                '<tbody>' + mine.slice(0, 200).map(entryRow).join('') + '</tbody></table>'
              : '<div class="tbl-empty">No non-revenue entries yet.<br><span class="small">' +
                U.esc(App.setting('NonRevenuePageHint', 'Counted here, never added to sales')) +
                ' are counted separately and never added to cash sales.</span></div>') +
          '</div>' +
        '</div>';

      const add = document.getElementById('nrAdd');
      if (add) add.onclick = NonRev.openForm;

      host.querySelectorAll('[data-del]').forEach(function (b) {
        b.onclick = function () {
          App.confirm('Delete this entry?', 'Only entries that have not been sent to Google Sheets can be deleted.',
            { danger: true, okText: 'Delete' }).then(function (yes) {
            if (!yes) return;
            DB.delete('nonrev', b.getAttribute('data-del')).then(function () {
              App.toast('Entry deleted', 'ok');
              NonRev.render();
            });
          });
        };
      });
    });
  };

  NonRev.openForm = function () {
    const products = App.state.products.filter(function (p) { return String(p.status || 'Active') === 'Active'; });
    let type = (TYPES()[0] || { id: 'Stock Loss' }).id;
    const qty = {};

    App.modal({
      title: 'New non-revenue entry',
      size: 'wide',
      body:
        '<div class="field"><label>Type</label><div class="row" id="nrTypes">' +
          TYPES().map(function (t) {
            return '<button class="btn ' + (t.id === type ? 'btn-primary' : 'btn-ghost') + '" data-t="' + U.esc(t.id) + '" style="flex:1">' + U.esc(t.label) + '</button>';
          }).join('') + '</div>' +
          '<p class="small muted mt-1" id="nrHint">' + U.esc((TYPES()[0] || {}).hint || '') + '</p></div>' +
        '<div class="field"><label>Date</label><input class="input" type="date" id="nrDate" value="' + U.todayKey() + '"></div>' +
        '<div class="field" style="margin-bottom:6px"><label>Items counted</label>' +
          '<input class="input" id="nrSearch" placeholder="Search the menu…"></div>' +
        '<div id="nrList" style="max-height:44vh;overflow-y:auto;border:1px solid var(--line);border-radius:12px"></div>' +
        '<div class="field mt-2"><label>Note (optional)</label><input class="input" id="nrNote" placeholder="e.g. end of day count"></div>',
      footer: '<button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary" data-save>Save entry</button>',
      onMount: function (m, close) {
        const listHost = m.querySelector('#nrList');

        function drawList(filter) {
          const term = (filter || '').toLowerCase();
          const list = products.filter(function (p) { return !term || p.name.toLowerCase().indexOf(term) >= 0; });
          listHost.innerHTML = list.map(function (p) {
            const q = qty[p.id] || 0;
            return '<div class="cart-line" style="border-radius:0">' +
              '<div class="cl-name">' + U.esc(p.name) + '<div class="cl-sub">' + U.esc(p.category) + '</div></div>' +
              '<div class="qty-box"><button data-dec="' + U.esc(p.id) + '">\u2212</button>' +
                '<input data-q="' + U.esc(p.id) + '" value="' + q + '" inputmode="numeric">' +
                '<button data-inc="' + U.esc(p.id) + '">+</button></div></div>';
          }).join('') || '<div class="tbl-empty">No matching item.</div>';

          listHost.querySelectorAll('[data-inc]').forEach(function (b) {
            b.onclick = function () { const id = b.getAttribute('data-inc'); qty[id] = (qty[id] || 0) + 1; b.parentNode.querySelector('input').value = qty[id]; };
          });
          listHost.querySelectorAll('[data-dec]').forEach(function (b) {
            b.onclick = function () { const id = b.getAttribute('data-dec'); qty[id] = Math.max(0, (qty[id] || 0) - 1); b.parentNode.querySelector('input').value = qty[id]; };
          });
          listHost.querySelectorAll('[data-q]').forEach(function (inp) {
            inp.onchange = function () { qty[inp.getAttribute('data-q')] = Math.max(0, U.int(inp.value)); inp.value = qty[inp.getAttribute('data-q')]; };
          });
        }
        drawList('');

        m.querySelector('#nrSearch').addEventListener('input', U.debounce(function (e) { drawList(e.target.value); }, 120));

        m.querySelectorAll('[data-t]').forEach(function (b) {
          b.onclick = function () {
            type = b.getAttribute('data-t');
            m.querySelectorAll('[data-t]').forEach(function (x) { x.classList.toggle('btn-primary', x === b); x.classList.toggle('btn-ghost', x !== b); });
            const t = TYPES().find(function (x) { return x.id === type; });
            m.querySelector('#nrHint').textContent = t.hint;
          };
        });

        m.querySelector('[data-save]').onclick = function () {
          const items = Object.keys(qty).filter(function (k) { return qty[k] > 0; }).map(function (id) {
            const p = App.state.products.find(function (x) { return x.id === id; });
            return {
              productId: id, name: p ? p.name : id, qty: qty[id],
              volume: p && p.volume ? p.volume : []
            };
          });
          if (!items.length) { App.toast('Add at least one item', 'warn'); return; }

          const dateKey = m.querySelector('#nrDate').value || U.todayKey();
          const s = App.state.session;
          const volumeTotals = {};
          items.forEach(function (i) {
            (i.volume || []).forEach(function (v) { volumeTotals[v.unit] = U.round2((volumeTotals[v.unit] || 0) + v.qty * i.qty); });
          });

          const rec = {
            recordId: U.recordId(),
            logType: type,
            businessDate: dateKey,
            createdAt: U.nowISO(),
            branchId: App.state.branchId,
            branchName: App.branchName(App.state.branchId),
            loggedBy: s.accountId,
            loggedByName: s.displayName,
            items: items,
            volumeTotals: volumeTotals,
            note: m.querySelector('#nrNote').value.trim(),
            syncStatus: 'Pending',
            sentAt: ''
          };

          DB.put('nonrev', rec).then(function () {
            close();
            App.toast(type + ' entry saved', 'ok', items.length + ' item(s) counted, not added to sales.');
            NonRev.render();
            App.refreshPendingCounts();
            if (navigator.onLine && API.isConfigured()) App.Sync.run({ silent: true });
          });
        };
      }
    });
  };

  global.NonRev = NonRev;
})(window);
