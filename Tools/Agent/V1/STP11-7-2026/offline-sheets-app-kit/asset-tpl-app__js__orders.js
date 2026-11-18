/* =====================================================================
   {{KIT_TITLE}} - Orders & History
   Search, filter, reprint, take payment, correct, void, export.
   ===================================================================== */
(function (global) {
  'use strict';

  const Orders = {};
  let q = '';
  let fBranch = '';
  let fStatus = 'All';
  let fDate = U.todayKey();
  let fRange = 'today';   // today | week | month | all | custom

  Orders.render = function () {
    const host = document.getElementById('ordersTable');
    if (!host) return;

    fBranch = fBranch || App.state.branchId;

    const bar = document.getElementById('ordersFilters');
    if (bar) {
      const branchOpts = (App.isAdmin() || App.state.session.allBranches ? App.state.branches : App.branchesVisible());
      bar.innerHTML =
        '<div class="field" style="margin:0"><label>Search</label>' +
          '<input class="input" id="oSearch" placeholder="Order no, queue #, cashier, item" value="' + U.esc(q) + '" style="min-width:220px"></div>' +
        '<div class="field" style="margin:0"><label>Branch</label><select class="select" id="oBranch">' +
          '<option value="all"' + (fBranch === 'all' ? ' selected' : '') + '>All visible branches</option>' +
          branchOpts.map(function (b) { return '<option value="' + U.esc(b.id) + '"' + (fBranch === b.id ? ' selected' : '') + '>' + U.esc(b.name) + '</option>'; }).join('') +
        '</select></div>' +
        '<div class="field" style="margin:0"><label>Status</label><select class="select" id="oStatus">' +
          ['All', 'Paid', 'Unpaid', 'New', 'Preparing', 'Ready', 'Served', 'Voided', 'Needs Review'].map(function (s) {
            return '<option' + (fStatus === s ? ' selected' : '') + '>' + s + '</option>';
          }).join('') + '</select></div>' +
        '<div class="field" style="margin:0"><label>Date</label><select class="select" id="oRange">' +
          [['today', 'Today'], ['week', 'Last 7 days'], ['month', 'This month'], ['all', 'All dates'], ['custom', 'Pick a date']].map(function (r) {
            return '<option value="' + r[0] + '"' + (fRange === r[0] ? ' selected' : '') + '>' + r[1] + '</option>';
          }).join('') + '</select></div>' +
        (fRange === 'custom' ? '<div class="field" style="margin:0"><label>Day</label>' +
          '<input class="input" type="date" id="oDate" value="' + U.esc(fDate) + '"></div>' : '') +
        '<button class="btn btn-ghost" id="oExport">\u2B07 Export CSV</button>';

      const search = bar.querySelector('#oSearch');
      search.addEventListener('input', U.debounce(function () { q = search.value; Orders.renderTable(); }, 150));
      const oB = bar.querySelector('#oBranch');
      if (oB) oB.onchange = function (e) { fBranch = e.target.value; Orders.render(); };
      const oS = bar.querySelector('#oStatus');
      if (oS) oS.onchange = function (e) { fStatus = e.target.value; Orders.render(); };
      const oR = bar.querySelector('#oRange');
      if (oR) oR.onchange = function (e) { fRange = e.target.value; Orders.render(); };
      const d = bar.querySelector('#oDate');
      if (d) d.onchange = function (e) { fDate = e.target.value || fDate; Orders.render(); };
      const oE = bar.querySelector('#oExport');
      if (oE) oE.onclick = Orders.exportCSV;
    }

    Orders.renderTable();
  };

  Orders.inRange = function (o) {
    const d = o.businessDate;
    const today = U.todayKey();
    switch (fRange) {
      case 'today': return d === today;
      case 'week': {
        const from = U.dateKey(U.addDays(new Date(), -6));
        return d >= from && d <= today;
      }
      case 'month': return d.slice(0, 7) === today.slice(0, 7);
      case 'custom': return d === fDate;
      default: return true;
    }
  };

  Orders.filtered = function (all) {
    const term = q.toLowerCase().trim();
    return all.filter(function (o) {
      if (fBranch !== 'all' && o.branchId !== fBranch) return false;
      if (!App.isAdmin() && !App.state.session.allBranches && o.branchId !== App.myBranch()) return false;
      if (!Orders.inRange(o)) return false;
      if (fStatus !== 'All') {
        if (fStatus === 'Paid' || fStatus === 'Unpaid') { if (o.paymentStatus !== fStatus) return false; }
        else if (fStatus === 'Needs Review') { if (o.syncStatus !== 'Needs Review') return false; }
        else if (o.kitchenStatus !== fStatus) return false;
      }
      if (!term) return true;
      const hay = [o.orderNo, o.queueNumber ? '#' + o.queueNumber : '', o.cashierName, o.notes,
        (o.lineItems || []).map(function (l) { return l.name; }).join(' ')].join(' ').toLowerCase();
      return hay.indexOf(term) >= 0;
    }).sort(function (a, b) { return new Date(b.createdAt) - new Date(a.createdAt); });
  };

  Orders.renderTable = function () {
    const host = document.getElementById('ordersTable');
    if (!host) return;
    DB.getAll('orders').then(function (all) {
      const list = Orders.filtered(all);
      const sumHost = document.getElementById('ordersSummary');
      const paid = list.filter(function (o) { return o.paymentStatus === 'Paid' && o.kitchenStatus !== 'Voided'; });
      const unpaid = list.filter(function (o) { return o.paymentStatus === 'Unpaid' && o.kitchenStatus !== 'Voided'; });
      if (sumHost) {
        sumHost.innerHTML =
          '<div class="metric"><div class="label">Orders shown</div><div class="value">' + list.length + '</div></div>' +
          '<div class="metric m-ok"><div class="label">Paid sales</div><div class="value">' + U.money(U.sum(paid, function (o) { return o.total; })) + '</div>' +
            '<div class="foot">' + paid.length + ' paid order(s)</div></div>' +
          '<div class="metric m-warn"><div class="label">Unpaid / Order only</div><div class="value">' + U.money(U.sum(unpaid, function (o) { return o.total; })) + '</div>' +
            '<div class="foot">' + unpaid.length + ' order(s) to collect</div></div>' +
          '<div class="metric m-info"><div class="label">Not yet synced</div><div class="value">' +
            list.filter(function (o) { return o.syncStatus !== 'Sent'; }).length + '</div></div>';
      }

      if (!list.length) {
        host.innerHTML = '<div class="tbl-empty">No orders match these filters.</div>';
        return;
      }

      host.innerHTML = '<div class="table-wrap"><table class="tbl"><thead><tr>' +
        '<th>Order</th><th>Date / time</th><th>Branch</th><th>Cashier</th><th class="num">Queue</th>' +
        '<th class="num">Total</th><th>Payment</th><th>Kitchen</th><th>Sync</th><th></th>' +
        '</tr></thead><tbody>' +
        list.slice(0, 400).map(function (o) {
          const payBadge = o.paymentStatus === 'Paid'
            ? '<span class="badge ok">' + U.esc(o.paymentMethod) + '</span>'
            : '<span class="badge danger">Unpaid</span>';
          const syncBadge = o.syncStatus === 'Sent' ? '<span class="badge ok">Sent</span>'
            : o.syncStatus === 'Needs Review' ? '<span class="badge danger">Review</span>'
              : '<span class="badge warn">Pending</span>';
          return '<tr>' +
            '<td><strong class="mono">' + U.esc(o.orderNo) + '</strong></td>' +
            '<td class="nowrap small dim">' + U.fmtDate(o.businessDate) + '<br>' + U.time12(o.createdAt) + '</td>' +
            '<td class="small">' + U.esc(App.branchCode(o.branchId)) + '</td>' +
            '<td class="small">' + U.esc(o.cashierName || '') + '</td>' +
            '<td class="num"><strong>' + (o.queueNumber || '--') + '</strong></td>' +
            '<td class="num"><strong>' + U.money(o.total) + '</strong></td>' +
            '<td>' + payBadge + '</td>' +
            '<td>' + Kitchen.statusBadge(o.kitchenStatus) + '</td>' +
            '<td>' + syncBadge + '</td>' +
            '<td class="right nowrap">' +
              '<button class="btn btn-ghost btn-sm" data-act="view" data-id="' + U.esc(o.recordId) + '">Open</button>' +
            '</td>' +
          '</tr>';
        }).join('') +
        '</tbody></table></div>' +
        (list.length > 400 ? '<p class="small muted center mt-2">Showing the newest 400 of ' + list.length + ' orders. Narrow the date range or export to CSV.</p>' : '');

      host.querySelectorAll('[data-act="view"]').forEach(function (b) {
        b.onclick = function () { Orders.openDetail(b.getAttribute('data-id')); };
      });
    });
  };

  Orders.openDetail = function (recordId) {
    DB.get('orders', recordId).then(function (o) {
      if (!o) return;
      const rows = (o.lineItems || []).map(function (l) {
        return '<tr><td>' + U.esc(l.name) + '</td><td class="num">' + l.qty + '</td>' +
          '<td class="num">' + U.money(l.price) + '</td><td class="num">' + U.money(l.lineTotal) + '</td></tr>';
      }).join('');

      App.modal({
        title: 'Order ' + o.orderNo,
        size: 'wide',
        body:
          '<div class="row" style="gap:7px;margin-bottom:12px">' +
            '<span class="badge ' + (o.paymentStatus === 'Paid' ? 'ok' : 'danger') + '">' + U.esc(o.paymentStatus) + '</span>' +
            Kitchen.statusBadge(o.kitchenStatus) +
            '<span class="badge gray">Queue #' + (o.queueNumber || '--') + '</span>' +
            '<span class="badge gray">' + U.esc(o.branchName || App.branchName(o.branchId)) + '</span>' +
            '<span class="badge ' + (o.syncStatus === 'Sent' ? 'ok' : o.syncStatus === 'Needs Review' ? 'danger' : 'warn') + '">' + U.esc(o.syncStatus) + '</span>' +
          '</div>' +
          '<div class="table-wrap"><table class="tbl"><thead><tr><th>Item</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Total</th></tr></thead><tbody>' +
            rows + '</tbody></table></div>' +
          '<div class="sep"></div>' +
          '<div class="row" style="justify-content:space-between"><span class="dim small">Subtotal</span><span>' + U.money(o.subtotal) + '</span></div>' +
          (o.discountAmount ? '<div class="row" style="justify-content:space-between"><span class="dim small">Discount</span><span>-' + U.money(o.discountAmount) + '</span></div>' : '') +
          '<div class="row mt-1" style="justify-content:space-between"><strong>Total</strong><strong style="font-size:1.2rem;color:var(--brand)">' + U.money(o.total) + '</strong></div>' +
          '<div class="row" style="justify-content:space-between"><span class="dim small">' + U.esc(o.paymentMethod) + ' tendered</span><span>' + U.money(o.amountTendered) + '</span></div>' +
          (o.paymentStatus === 'Paid' ? '<div class="row" style="justify-content:space-between"><span class="dim small">Change</span><span>' + U.money(o.changeDue) + '</span></div>' : '') +
          '<div class="sep"></div>' +
          '<div class="small muted">' + U.esc(o.cashierName) + ' \u00B7 ' + U.fmtDateTime(o.createdAt) + ' \u00B7 ' + U.esc(o.deviceId || '') + '</div>' +
          (o.notes ? '<div class="card mt-2" style="background:var(--bg-2)"><strong class="small">Note:</strong> <span class="small">' + U.esc(o.notes) + '</span></div>' : '') +
          (o.voidReason ? '<div class="card mt-2" style="background:var(--danger-soft);border-color:rgba(255,95,109,.35)"><strong class="small">Voided:</strong> <span class="small">' + U.esc(o.voidReason) + '</span></div>' : '') +
          (o.needsReviewReason ? '<div class="card mt-2" style="background:var(--warn-soft);border-color:rgba(255,197,66,.35)"><strong class="small">Needs review:</strong> <span class="small">' + U.esc(o.needsReviewReason) + '</span></div>' : '') +
          '<div class="small muted mt-2 mono">Record ID: ' + U.esc(o.recordId) + '</div>',
        footer:
          '<button class="btn btn-ghost" data-close>Close</button>' +
          '<button class="btn" data-print>\uD83D\uDDA8 Print</button>' +
          (o.paymentStatus === 'Unpaid' && o.kitchenStatus !== 'Voided' && App.can('payment.collect')
            ? '<button class="btn btn-ok" data-pay>Take payment</button>' : '') +
          (o.kitchenStatus !== 'Voided' && (App.can('order.void.any') || App.can('order.void.own'))
            ? '<button class="btn btn-danger" data-void>Void</button>' : '') +
          (o.syncStatus === 'Needs Review' && App.isAdmin() ? '<button class="btn btn-primary" data-resolve>Mark resolved</button>' : ''),
        onMount: function (m, close) {
          m.querySelector('[data-print]').onclick = function () { Receipt.print(o); };
          const p = m.querySelector('[data-pay]');
          if (p) p.onclick = function () { close(); Kitchen.takePayment(o.recordId); };
          const v = m.querySelector('[data-void]');
          if (v) v.onclick = function () { close(); Kitchen.voidOrder(o.recordId); };
          const r = m.querySelector('[data-resolve]');
          if (r) r.onclick = function () {
            o.syncStatus = 'Pending'; o.needsReviewReason = '';
            DB.put('orders', o).then(function () {
              close();
              App.toast('Record reset to Pending', 'ok', 'It will be sent again on the next sync.');
              Orders.render();
              App.refreshPendingCounts();
            });
          };
        }
      });
    });
  };

  Orders.exportCSV = function () {
    DB.getAll('orders').then(function (all) {
      const list = Orders.filtered(all);
      if (!list.length) { App.toast('Nothing to export', 'warn'); return; }
      const rows = [];
      list.forEach(function (o) {
        (o.lineItems || []).forEach(function (l) {
          rows.push([
            o.orderNo, o.businessDate, U.time12(o.createdAt), App.branchName(o.branchId), o.cashierName,
            o.queueNumber || '', o.kitchenStatus, o.paymentStatus, o.paymentMethod,
            l.name, l.qty, U.round2(l.price), U.round2(l.lineTotal),
            JSON.stringify(o.volumeTotals || {}), o.subtotal, o.discountAmount, o.total,
            U.round2(o.amountTendered), U.round2(o.changeDue), o.syncStatus, o.recordId
          ]);
        });
      });
      const headers = ['OrderNo', 'Date', 'Time', 'Branch', 'Cashier', 'Queue', 'KitchenStatus', 'PaymentStatus',
        'PaymentMethod', 'Item', 'Qty', 'UnitPrice', 'LineTotal', 'VolumeTotals', 'Subtotal', 'Discount', 'Total',
        'Tendered', 'Change', 'SyncStatus', 'RecordID'];
      U.download('{{BRAND_SLUG}}-orders-' + U.todayKey() + '.csv', '\uFEFF' + U.toCSV(rows, headers), 'text/csv;charset=utf-8');
      App.toast('Orders exported', 'ok', rows.length + ' line(s) written.');
    });
  };

  global.Orders = Orders;
})(window);
