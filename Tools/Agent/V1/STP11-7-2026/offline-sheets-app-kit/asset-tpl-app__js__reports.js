/* =====================================================================
   {{KIT_TITLE}} - Reports
   Daily revenue, payment breakdown, product volume, multi-branch view,
   export to CSV (opens in Excel).
   ===================================================================== */
(function (global) {
  'use strict';

  const Reports = {};
  let range = 'today';
  let customDate = U.todayKey();
  let branch = '';

  Reports.rangeBounds = function () {
    const today = new Date();
    switch (range) {
      case 'today': return { from: U.dateKey(today), to: U.dateKey(today), label: 'Today \u00B7 ' + U.fmtDate(U.dateKey(today)) };
      case 'yesterday': {
        const y = U.addDays(today, -1);
        return { from: U.dateKey(y), to: U.dateKey(y), label: 'Yesterday \u00B7 ' + U.fmtDate(U.dateKey(y)) };
      }
      case 'week': return { from: U.dateKey(U.addDays(today, -6)), to: U.dateKey(today), label: 'Last 7 days' };
      case 'month': return { from: U.dateKey(today).slice(0, 8) + '01', to: U.dateKey(today), label: 'This month' };
      case 'custom': return { from: customDate, to: customDate, label: U.fmtDate(customDate) };
      default: return { from: '2000-01-01', to: '2999-12-31', label: 'All time' };
    }
  };

  Reports.render = function () {
    const host = document.getElementById('reportsBody');
    if (!host) return;

    branch = branch || (App.isAdmin() ? 'all' : App.myBranch());
    const bar = document.getElementById('reportsFilters');
    if (bar) {
      const visible = App.isAdmin() || App.state.session.allBranches ? App.state.branches : App.branchesVisible();
      bar.innerHTML =
        '<div class="field" style="margin:0"><label>Period</label><select class="select" id="rRange">' +
          [['today', 'Today'], ['yesterday', 'Yesterday'], ['week', 'Last 7 days'], ['month', 'This month'], ['all', 'All time'], ['custom', 'Pick a date']]
            .map(function (r) { return '<option value="' + r[0] + '"' + (range === r[0] ? ' selected' : '') + '>' + r[1] + '</option>'; }).join('') +
        '</select></div>' +
        (range === 'custom' ? '<div class="field" style="margin:0"><label>Date</label><input class="input" type="date" id="rDate" value="' + U.esc(customDate) + '"></div>' : '') +
        '<div class="field" style="margin:0"><label>Branch</label><select class="select" id="rBranch">' +
          '<option value="all">' + (visible.length > 1 ? 'All branches (combined)' : 'All') + '</option>' +
          visible.map(function (b) { return '<option value="' + U.esc(b.id) + '"' + (branch === b.id ? ' selected' : '') + '>' + U.esc(b.name) + '</option>'; }).join('') +
        '</select></div>' +
        '<button class="btn btn-ghost" id="rExport">\u2B07 Export CSV</button>' +
        '<button class="btn btn-ghost" id="rPrint">\uD83D\uDDA8 Print</button>';

      const rR = bar.querySelector('#rRange');
      if (rR) rR.onchange = function (e) { range = e.target.value; Reports.render(); };
      const d = bar.querySelector('#rDate');
      if (d) d.onchange = function (e) { customDate = e.target.value; Reports.render(); };
      const rB = bar.querySelector('#rBranch');
      if (rB) rB.onchange = function (e) { branch = e.target.value; Reports.render(); };
      const rE = bar.querySelector('#rExport');
      if (rE) rE.onclick = Reports.exportCSV;
      const rP = bar.querySelector('#rPrint');
      if (rP) rP.onclick = function () { window.print(); };
    }

    DB.getAll('orders').then(function (all) {
      const b = Reports.rangeBounds();
      const inRange = all.filter(function (o) {
        if (o.businessDate < b.from || o.businessDate > b.to) return false;
        if (branch !== 'all' && o.branchId !== branch) return false;
        return true;
      });

      const live = inRange.filter(function (o) { return o.kitchenStatus !== 'Voided'; });
      const voided = inRange.filter(function (o) { return o.kitchenStatus === 'Voided'; });
      const paid = live.filter(function (o) { return o.paymentStatus === 'Paid'; });
      const unpaid = live.filter(function (o) { return o.paymentStatus === 'Unpaid'; });

      const gross = U.round2(U.sum(paid, function (o) { return o.total; }));
      const unpaidTotal = U.round2(U.sum(unpaid, function (o) { return o.total; }));
      const voidTotal = U.round2(U.sum(voided, function (o) { return o.total; }));
      const orders = live.length;
      const avg = orders ? U.round2(U.sum(live, function (o) { return o.total; }) / orders) : 0;
      const METHODS = App.paymentMethods();
      const byMethod = {};
      METHODS.forEach(function (m) { byMethod[m] = 0; });
      paid.forEach(function (o) {
        const m = o.paymentMethod;
        if (byMethod[m] === undefined) byMethod[m] = 0;
        byMethod[m] = U.round2(byMethod[m] + o.total);
      });
      const methodColor = function (i) {
        return ['var(--ok)', 'var(--info)', 'var(--purple)', 'var(--warn)', 'var(--brand)', 'var(--danger)'][i % 6];
      };

      // Product volume
      const vol = {};
      live.forEach(function (o) {
        Object.keys(o.volumeTotals || {}).forEach(function (k) {
          vol[k] = U.round2((vol[k] || 0) + o.volumeTotals[k]);
        });
      });

      // Per-item quantity ranking
      const itemQty = {};
      live.forEach(function (o) {
        (o.lineItems || []).forEach(function (l) {
          const key = l.name;
          if (!itemQty[key]) itemQty[key] = { name: key, qty: 0, sales: 0 };
          itemQty[key].qty += l.qty;
          itemQty[key].sales = U.round2(itemQty[key].sales + l.lineTotal);
        });
      });
      const topItems = Object.keys(itemQty).map(function (k) { return itemQty[k]; })
        .sort(function (a, b2) { return b2.qty - a.qty; }).slice(0, 15);

      // Per-branch (multi-branch view)
      const perBranch = {};
      inRange.forEach(function (o) {
        if (o.kitchenStatus === 'Voided') return;
        if (!perBranch[o.branchId]) perBranch[o.branchId] = { id: o.branchId, orders: 0, paid: 0, unpaid: 0, voided: 0 };
        const p = perBranch[o.branchId];
        p.orders++;
        if (o.paymentStatus === 'Paid') {
          p.paid = U.round2(p.paid + o.total);
          const key = 'm_' + o.paymentMethod;
          p[key] = U.round2((p[key] || 0) + o.total);
        }
        else p.unpaid = U.round2(p.unpaid + o.total);
      });
      voided.forEach(function (o) {
        if (!perBranch[o.branchId]) perBranch[o.branchId] = { id: o.branchId, orders: 0, paid: 0, unpaid: 0, voided: 0 };
        perBranch[o.branchId].voided = U.round2(perBranch[o.branchId].voided + o.total);
      });

      // Daily series (last 14 days within range)
      const series = {};
      inRange.forEach(function (o) {
        if (o.kitchenStatus === 'Voided' || o.paymentStatus !== 'Paid') return;
        series[o.businessDate] = U.round2((series[o.businessDate] || 0) + o.total);
      });
      const days = Object.keys(series).sort().slice(-14);
      const maxDay = days.length ? Math.max.apply(null, days.map(function (d) { return series[d]; })) : 1;

      const payTotal = U.sum(METHODS.map(function (m) { return byMethod[m] || 0; })) || 1;

      host.innerHTML =
        /* Metrics */
        '<div class="grid g-4 mb-2">' +
          '<div class="metric"><div class="label">Paid revenue</div><div class="value">' + U.money(gross) + '</div>' +
            '<div class="foot">' + paid.length + ' paid order(s)</div></div>' +
          '<div class="metric m-warn"><div class="label">Unpaid (order only)</div><div class="value">' + U.money(unpaidTotal) + '</div>' +
            '<div class="foot">' + unpaid.length + ' order(s) still to collect</div></div>' +
          '<div class="metric m-info"><div class="label">Average order</div><div class="value">' + U.money(avg) + '</div>' +
            '<div class="foot">' + orders + ' total order(s)</div></div>' +
          '<div class="metric m-purple"><div class="label">Voided</div><div class="value">' + U.money(voidTotal) + '</div>' +
            '<div class="foot">' + voided.length + ' voided order(s)</div></div>' +
        '</div>' +

        '<div class="grid g-2 mb-2">' +
          /* Payment split */
          '<div class="card"><div class="card-head"><h3>Payment breakdown</h3><span class="sub">' + U.esc(b.label) + '</span></div>' +
            '<div class="pay-split">' +
              METHODS.map(function (m, i) {
                const amt = byMethod[m] || 0;
                const pct = Math.round((amt / payTotal) * 100);
                return '<div class="ps-row"><span class="ps-name">' + U.esc(m) + '</span>' +
                  '<div class="ps-track"><div class="ps-fill" style="width:' + pct + '%;background:' + methodColor(i) + '"></div></div>' +
                  '<span class="ps-val">' + U.money(amt) + '</span></div>';
              }).join('') +
            '</div>' +
            '<div class="sep"></div>' +
            '<div class="row" style="justify-content:space-between"><span class="dim small">Total collected</span>' +
              '<strong style="color:var(--brand);font-size:1.1rem">' + U.money(gross) + '</strong></div>' +
            '<div class="row mt-1" style="justify-content:space-between"><span class="dim small">Orders with no payment yet</span>' +
              '<span>' + U.money(unpaidTotal) + '</span></div>' +
          '</div>' +

          /* Daily chart */
          '<div class="card"><div class="card-head"><h3>Daily paid sales</h3><span class="sub">Last ' + days.length + ' day(s) with sales</span></div>' +
            (days.length
              ? '<div class="bar-chart">' + days.map(function (d) {
                  const h = Math.max(3, Math.round((series[d] / maxDay) * 100));
                  return '<div class="bar-col" title="' + U.fmtDate(d) + ': ' + U.money(series[d]) + '">' +
                    '<span class="small dim" style="font-size:.6rem">' + U.money0(series[d]) + '</span>' +
                    '<div class="bar-fill" style="height:' + h + '%"></div>' +
                    '<span class="bar-lbl">' + d.slice(5) + '</span></div>';
                }).join('') + '</div>'
              : '<div class="empty-state" style="padding:24px"><p>No paid sales in this period yet.</p></div>') +
          '</div>' +
        '</div>' +

        '<div class="grid g-2 mb-2">' +
          /* Product volume */
          '<div class="card"><div class="card-head"><h3>Product volume</h3>' +
            '<span class="sub">Real item count \u2014 combos and buckets included</span></div>' +
            (Object.keys(vol).length
              ? '<div class="table-wrap"><table class="tbl"><thead><tr><th>Volume unit</th><th class="num">Count</th></tr></thead><tbody>' +
                Object.keys(vol).sort(function (a, c) { return vol[c] - vol[a]; }).map(function (k) {
                  return '<tr><td>' + U.esc(k) + '</td><td class="num"><strong style="color:var(--brand);font-size:1rem">' + vol[k] + '</strong></td></tr>';
                }).join('') + '</tbody></table></div>'
              : '<div class="empty-state" style="padding:24px"><p>No product volume recorded for this period.</p></div>') +
          '</div>' +

          /* Top items */
          '<div class="card"><div class="card-head"><h3>Best sellers</h3><span class="sub">By quantity</span></div>' +
            (topItems.length
              ? '<div class="table-wrap"><table class="tbl"><thead><tr><th>Item</th><th class="num">Qty</th><th class="num">Sales</th></tr></thead><tbody>' +
                topItems.map(function (t) {
                  return '<tr><td class="small">' + U.esc(t.name) + '</td><td class="num">' + t.qty + '</td>' +
                    '<td class="num">' + U.money(t.sales) + '</td></tr>';
                }).join('') + '</tbody></table></div>'
              : '<div class="empty-state" style="padding:24px"><p>No items sold in this period.</p></div>') +
          '</div>' +
        '</div>' +

        /* Multi-branch */
        '<div class="card mb-2"><div class="card-head"><h3>Branch comparison</h3>' +
          '<span class="sub">' + (branch === 'all' ? 'All visible branches' : App.branchName(branch)) + '</span></div>' +
          '<div class="table-wrap"><table class="tbl"><thead><tr>' +
            '<th>Branch</th><th class="num">Orders</th>' +
            METHODS.map(function (m) { return '<th class="num">' + U.esc(m) + '</th>'; }).join('') +
            '<th class="num">Unpaid</th><th class="num">Voided</th><th class="num">Paid total</th>' +
          '</tr></thead><tbody>' +
          (Object.keys(perBranch).length
            ? U.sortBy(Object.keys(perBranch).map(function (k) { return perBranch[k]; }), function (x) { return x.id; })
              .map(function (p) {
                return '<tr><td><strong>' + U.esc(App.branchName(p.id)) + '</strong><div class="small muted">' + U.esc(p.id) + '</div></td>' +
                  '<td class="num">' + p.orders + '</td>' +
                  METHODS.map(function (m) { return '<td class="num">' + U.money(p['m_' + m] || 0) + '</td>'; }).join('') +
                  '<td class="num" style="color:var(--warn)">' + U.money(p.unpaid) + '</td>' +
                  '<td class="num" style="color:var(--danger)">' + U.money(p.voided) + '</td>' +
                  '<td class="num"><strong>' + U.money(p.paid) + '</strong></td></tr>';
              }).join('')
            : '<tr><td colspan="' + (METHODS.length + 5) + '" class="tbl-empty">No data for this period.</td></tr>') +
          '</tbody></table></div>' +
        '</div>' +

        /* Non-revenue summary */
        Reports.nonRevBlock(b) +

        '<p class="small muted center mt-2">Figures come from orders saved on this device. ' +
          (API.isConfigured() ? 'Google Sheets holds the combined copy for all branches.' : 'Connect a Google Sheet in Settings to share with the owner.') + '</p>';
    });
  };

  Reports.nonRevBlock = function (bounds) {
    return '<div class="card" id="nonrevSummary"><div class="card-head"><h3>Non-revenue counts</h3>' +
      '<span class="sub">' + U.esc(App.setting('NonRevenuePageHint', 'Counted separately, never added to sales')) + ' \u2014 not added to sales</span></div>' +
      '<div id="nonrevInner" class="small muted">Loading…</div></div>';
  };

  Reports.fillNonRev = function () {
    const inner = document.getElementById('nonrevInner');
    if (!inner) return;
    const b = Reports.rangeBounds();
    DB.getAll('nonrev').then(function (logs) {
      const list = logs.filter(function (l) {
        if (l.businessDate < b.from || l.businessDate > b.to) return false;
        if (branch !== 'all' && l.branchId !== branch) return false;
        return true;
      });
      if (!list.length) { inner.innerHTML = '<p style="margin:0">No leftover or employee meal entries in this period.</p>'; return; }
      const byType = U.groupBy(list, function (l) { return l.logType; });
      inner.innerHTML = Object.keys(byType).map(function (t) {
        const items = {};
        byType[t].forEach(function (l) { (l.items || []).forEach(function (i) { items[i.name] = (items[i.name] || 0) + i.qty; }); });
        const meta = (App.nonRevenueTypes().find(function (x) { return x.id === t; }) || { cls: 'warn' });
        return '<div class="mb-2"><span class="badge ' + meta.cls + '">' + U.esc(t) + '</span>' +
          '<div class="row mt-1" style="gap:7px">' + Object.keys(items).map(function (k) {
            return '<span class="badge gray">' + U.esc(k) + ' \u00D7' + items[k] + '</span>';
          }).join('') + '</div></div>';
      }).join('');
    });
  };

  Reports.exportCSV = function () {
    const b = Reports.rangeBounds();
    DB.getAll('orders').then(function (all) {
      const list = all.filter(function (o) {
        if (o.businessDate < b.from || o.businessDate > b.to) return false;
        if (branch !== 'all' && o.branchId !== branch) return false;
        return true;
      });
      if (!list.length) { App.toast('Nothing to export for this period', 'warn'); return; }

      const paid = list.filter(function (o) { return o.paymentStatus === 'Paid' && o.kitchenStatus !== 'Voided'; });
      const vol = {};
      list.forEach(function (o) {
        if (o.kitchenStatus === 'Voided') return;
        Object.keys(o.volumeTotals || {}).forEach(function (k) { vol[k] = U.round2((vol[k] || 0) + o.volumeTotals[k]); });
      });

      const rows = [];
      rows.push(['{{KIT_TITLE}} - Sales Report']);
      rows.push(['Business', App.setting('BusinessName', '{{BUSINESS_NAME}}')]);
      rows.push(['Period', b.label]);
      rows.push(['Branch', branch === 'all' ? 'All branches' : App.branchName(branch)]);
      rows.push(['Generated', U.fmtDateTime(U.nowISO())]);
      rows.push([]);
      rows.push(['Summary']);
      rows.push(['Paid revenue', U.round2(U.sum(paid, function (o) { return o.total; }))]);
      App.paymentMethods().forEach(function (m) {
        rows.push([m, U.round2(U.sum(paid.filter(function (o) { return o.paymentMethod === m; }), function (o) { return o.total; }))]);
      });
      rows.push(['Orders', list.length]);
      rows.push([]);
      rows.push(['Product volume']);
      Object.keys(vol).forEach(function (k) { rows.push([k, vol[k]]); });
      rows.push([]);
      rows.push(['Order details']);
      rows.push(['OrderNo', 'Date', 'Time', 'Branch', 'Cashier', 'Queue', 'Status', 'Payment', 'Method', 'Items', 'Total', 'Tendered', 'Change', 'VoidReason']);

      list.forEach(function (o) {
        rows.push([
          o.orderNo, o.businessDate, U.time12(o.createdAt), App.branchName(o.branchId), o.cashierName,
          o.queueNumber || '', o.kitchenStatus, o.paymentStatus, o.paymentMethod,
          (o.lineItems || []).map(function (l) { return l.qty + 'x ' + l.name; }).join('; '),
          o.total, o.amountTendered, o.changeDue, o.voidReason || ''
        ]);
      });

      U.download('{{BRAND_SLUG}}-report-' + b.from + '_to_' + b.to + '.csv',
        '\uFEFF' + U.toCSV(rows), 'text/csv;charset=utf-8');
      App.toast('Report exported', 'ok', list.length + ' order(s). Opens in Excel or Google Sheets.');
    });
  };

  const origRender = Reports.render;
  Reports.render = function () {
    const ret = origRender.apply(this, arguments);
    setTimeout(Reports.fillNonRev, 60);
    return ret;
  };

  global.Reports = Reports;
})(window);
