/* =====================================================================
   {{KIT_TITLE}} - Kitchen Display System
   Live queue strip (top of POS) + full kitchen board.
   Statuses: New -> Preparing -> Ready -> Served, or Voided.
   Queue numbers are released automatically on Served / Voided.
   ===================================================================== */
(function (global) {
  'use strict';

  const Kitchen = {};
  let filter = 'Active';
  let ticker = null;
  let lastHash = '';

  const ACTIVE = ['New', 'Preparing', 'Ready'];

  Kitchen.activeOrders = function (branchId) {
    return DB.getAll('orders').then(function (all) {
      return all.filter(function (o) {
        if (o.kitchenStatus === 'Voided') return false;
        if (ACTIVE.indexOf(o.kitchenStatus) < 0) return false;
        if (branchId && o.branchId !== branchId) return false;
        return true;
      }).sort(function (a, b) {
        // Unpaid first? No: oldest first, but Ready last so cooks clear the line
        const rank = { New: 0, Preparing: 1, Ready: 2 };
        const ra = rank[a.kitchenStatus] || 0, rb = rank[b.kitchenStatus] || 0;
        if (ra !== rb) return ra - rb;
        return new Date(a.createdAt) - new Date(b.createdAt);
      });
    });
  };

  Kitchen.shortItems = function (o) {
    return (o.lineItems || []).map(function (l) { return l.qty + '\u00D7 ' + l.name; }).join(', ');
  };

  Kitchen.statusBadge = function (st) {
    const map = {
      'New': ['warn', 'NEW'], 'Preparing': ['info', 'PREPARING'], 'Ready': ['ok', 'READY'],
      'Served': ['gray', 'SERVED'], 'Voided': ['danger', 'VOIDED']
    };
    const m = map[st] || ['gray', st];
    return '<span class="badge ' + m[0] + '">' + m[1] + '</span>';
  };

  /* ------------------------------------------------------------------ */
  /*  Top strip on the POS screen                                       */
  /* ------------------------------------------------------------------ */
  Kitchen.renderStrip = function () {
    const track = document.getElementById('kdsTrack');
    if (!track) return;
    if (!App.feature('kitchen')) { track.innerHTML = ''; return; }
    Kitchen.activeOrders(App.state.branchId).then(function (orders) {
      const head = document.getElementById('kdsCount');
      if (head) head.textContent = orders.length ? orders.length + ' active' : 'no active orders';
      if (!orders.length) {
        track.innerHTML = '<div class="kds-empty">\uD83D\uDC68\u200D\uD83C\uDF73 <span>The kitchen queue is clear. New orders appear here automatically.</span></div>';
        return;
      }
      const warn = App.settingNum('KitchenWarnMinutes', 8);
      track.innerHTML = orders.map(function (o) {
        const mins = U.minutesSince(o.createdAt);
        const late = mins >= warn;
        const cls = o.kitchenStatus === 'New' ? 'st-new' : o.kitchenStatus === 'Preparing' ? 'st-preparing' : 'st-ready';
        return '<div class="kds-card ' + cls + (o.justUpdated ? ' st-pulse' : '') + '" data-open="' + U.esc(o.recordId) + '">' +
          '<div class="qc"><span>' + U.esc(o.orderNo.slice(-4)) + '</span>' +
            '<span class="' + (late ? 'kt-late' : '') + '">' + mins + 'm</span></div>' +
          '<div class="qnum">#' + (o.queueNumber || '--') + '</div>' +
          '<div class="items">' + U.esc(Kitchen.shortItems(o)) + '</div>' +
          '<div class="meta">' + Kitchen.statusBadge(o.kitchenStatus) +
            (o.paymentStatus === 'Unpaid' ? '<span class="badge danger">UNPAID</span>' : '') +
          '</div>' +
        '</div>';
      }).join('');
      track.querySelectorAll('[data-open]').forEach(function (c) {
        c.onclick = function () { Kitchen.openTicket(c.getAttribute('data-open')); };
      });
    });
  };

  /* ------------------------------------------------------------------ */
  /*  Full kitchen page                                                 */
  /* ------------------------------------------------------------------ */
  Kitchen.render = function () {
    const host = document.getElementById('kitchenBoard');
    if (!host) return;

    const tabsHost = document.getElementById('kdsTabs');
    if (tabsHost) {
      const opts = [['Active', 'Active'], ['New', 'New'], ['Preparing', 'Preparing'], ['Ready', 'Ready'], ['Today', 'Served today']];
      tabsHost.innerHTML = opts.map(function (t) {
        return '<button class="tab' + (filter === t[0] ? ' active' : '') + '" data-kf="' + t[0] + '">' + t[1] + '</button>';
      }).join('');
      tabsHost.querySelectorAll('[data-kf]').forEach(function (b) {
        b.onclick = function () { filter = b.getAttribute('data-kf'); Kitchen.render(); };
      });
    }

    const branch = App.state.branchId;

    DB.getAll('orders').then(function (all) {
      let orders = all.filter(function (o) { return o.branchId === branch; });
      if (filter === 'Active') orders = orders.filter(function (o) { return ACTIVE.indexOf(o.kitchenStatus) >= 0; });
      else if (filter === 'Today') orders = orders.filter(function (o) {
        return o.businessDate === U.todayKey() && o.kitchenStatus === 'Served';
      });
      else orders = orders.filter(function (o) { return o.kitchenStatus === filter; });

      orders.sort(function (a, b) { return new Date(a.createdAt) - new Date(b.createdAt); });

      // Counters
      const counts = { New: 0, Preparing: 0, Ready: 0, Unpaid: 0 };
      all.filter(function (o) { return o.branchId === branch; }).forEach(function (o) {
        if (ACTIVE.indexOf(o.kitchenStatus) >= 0) {
          counts[o.kitchenStatus]++;
          if (o.paymentStatus === 'Unpaid') counts.Unpaid++;
        }
      });
      const set = function (id, v) { const e = document.getElementById(id); if (e) e.textContent = v; };
      set('cntNew', counts.New);
      set('cntPrep', counts.Preparing);
      set('cntReady', counts.Ready);
      set('cntUnpaid', counts.Unpaid);

      if (!orders.length) {
        host.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="es-ico">\u2705</div>' +
          '<h4>Nothing here</h4><p>' + (filter === 'Active' ? 'All active orders are finished. New tickets appear on their own.' : 'No orders match this filter.') + '</p></div>';
        return;
      }

      const warn = App.settingNum('KitchenWarnMinutes', 8);
      host.innerHTML = orders.map(function (o) {
        const mins = U.minutesSince(o.createdAt);
        const late = mins >= warn && ACTIVE.indexOf(o.kitchenStatus) >= 0;
        const headCls = o.kitchenStatus === 'New' ? 'head-new' : o.kitchenStatus === 'Preparing' ? 'head-preparing' : 'head-ready';
        const nextBtn = o.kitchenStatus === 'New' ? { to: 'Preparing', label: 'Start preparing', cls: 'btn' }
          : o.kitchenStatus === 'Preparing' ? { to: 'Ready', label: 'Mark ready', cls: 'btn btn-ok' }
            : o.kitchenStatus === 'Ready' ? { to: 'Served', label: 'Mark served', cls: 'btn btn-primary' } : null;

        return '<div class="kds-ticket ' + headCls + '">' +
          '<div class="kt-top">' +
            '<span class="kt-num">#' + (o.queueNumber || '--') + '</span>' +
            '<div class="grow"><div style="font-size:.78rem;font-weight:700">' + U.esc(o.orderNo) + '</div>' +
              '<div class="kt-time' + (late ? ' kt-late' : '') + '">' + mins + ' min ago \u00B7 ' + U.time12(o.createdAt) + '</div></div>' +
            Kitchen.statusBadge(o.kitchenStatus) +
          '</div>' +
          '<div class="kt-items">' +
            (o.lineItems || []).map(function (l) {
              return '<div class="kt-item"><span>' + U.esc(l.name) + '</span><b>\u00D7' + l.qty + '</b></div>';
            }).join('') +
            (o.notes ? '<div class="small" style="margin-top:7px;color:var(--warn)">\u26A0 ' + U.esc(o.notes) + '</div>' : '') +
          '</div>' +
          '<div class="kt-foot">' +
            (nextBtn ? '<button class="btn ' + (o.kitchenStatus === 'Preparing' ? 'btn-ok' : o.kitchenStatus === 'Ready' ? 'btn-primary' : '') + '" data-next="' + U.esc(o.recordId) + '">' + nextBtn.label + '</button>' : '') +
            (o.paymentStatus === 'Unpaid' && App.can('payment.collect') ?
              '<button class="btn btn-ok" data-pay="' + U.esc(o.recordId) + '">Take payment</button>' :
              '<span class="badge ok paid-badge">PAID \u00B7 ' + U.esc(o.paymentMethod) + '</span>') +
            '<div class="span2 row" style="gap:7px">' +
              '<button class="btn btn-ghost btn-sm grow" data-view="' + U.esc(o.recordId) + '">Details</button>' +
              (App.can('order.void.any') || App.can('order.void.own') ?
                '<button class="btn btn-ghost btn-sm" data-void="' + U.esc(o.recordId) + '">Void</button>' : '') +
            '</div>' +
          '</div>' +
        '</div>';
      }).join('');

      host.querySelectorAll('[data-next]').forEach(function (b) {
        b.onclick = function () { Kitchen.advance(b.getAttribute('data-next')); };
      });
      host.querySelectorAll('[data-pay]').forEach(function (b) {
        b.onclick = function () { Kitchen.takePayment(b.getAttribute('data-pay')); };
      });
      host.querySelectorAll('[data-view]').forEach(function (b) {
        b.onclick = function () { Kitchen.openTicket(b.getAttribute('data-view')); };
      });
      host.querySelectorAll('[data-void]').forEach(function (b) {
        b.onclick = function () { Kitchen.voidOrder(b.getAttribute('data-void')); };
      });
    });
  };

  /* ------------------------------------------------------------------ */
  /*  Status changes                                                    */
  /* ------------------------------------------------------------------ */
  Kitchen.setStatus = function (recordId, status, opts) {
    opts = opts || {};
    return DB.get('orders', recordId).then(function (o) {
      if (!o) return null;
      const prev = o.kitchenStatus;
      o.kitchenStatus = status;
      o.kitchenUpdatedAt = U.nowISO();
      o.updatedAt = U.nowISO();
      o.revision = (o.revision || 1) + 1;
      o.statusUpdatedBy = App.state.session ? App.state.session.accountId : '';
      if (status === 'Served') {
        o.servedAt = U.nowISO();
        if (App.settingBool('AutoReleaseQueue', true)) o.queueReleased = true;
      }
      if (status === 'Voided') {
        o.voidedAt = U.nowISO();
        if (App.settingBool('AutoReleaseQueue', true)) o.queueReleased = true;
      }
      // A changed-but-already-sent record must go back through the sync queue
      if (o.syncStatus === 'Sent') o.syncStatus = 'Pending';
      if (o.syncStatus === 'Needs Review' && !o.needsReviewReason) o.syncStatus = 'Pending';
      o.justUpdated = true;

      return DB.put('orders', o).then(function () {
        setTimeout(function () {
          DB.get('orders', recordId).then(function (x) {
            if (x) { x.justUpdated = false; DB.put('orders', x); }
          });
        }, 4000);
        if (navigator.onLine && API.isConfigured()) App.Sync.run({ silent: true });
        App.refreshPendingCounts();
        if (typeof Kitchen !== 'undefined') { Kitchen.renderStrip(); if (App.state.page === 'kitchen') Kitchen.render(); }
        if (typeof Orders !== 'undefined' && App.state.page === 'orders') Orders.render();
        return o;
      });
    });
  };

  Kitchen.advance = function (recordId) {
    DB.get('orders', recordId).then(function (o) {
      if (!o) return;
      const next = { New: 'Preparing', Preparing: 'Ready', Ready: 'Served' }[o.kitchenStatus];
      if (!next) return;
      Kitchen.setStatus(recordId, next).then(function (r) {
        if (r && next === 'Served') App.toast('Queue #' + (r.queueNumber || '--') + ' released', 'ok', 'The card can be used for a new order.');
        else if (r) App.toast('#' + (r.queueNumber || '--') + ' \u2192 ' + next, 'ok');
      });
    });
  };

  Kitchen.voidOrder = function (recordId) {
    DB.get('orders', recordId).then(function (o) {
      if (!o) return;
      const mine = App.state.session && o.accountId === App.state.session.accountId;
      if (!App.can('order.void.any') && !(App.can('order.void.own') && mine)) {
        App.toast('Your role cannot void this order', 'err');
        return;
      }
      if (App.can('order.void.own') && !App.can('order.void.any')) {
        const win = App.settingNum('VoidWindowMinutes', 15);
        if (U.minutesSince(o.createdAt) > win) {
          App.toast('Void window passed', 'err', 'Orders older than ' + win + ' minutes need an owner or supervisor.');
          return;
        }
      }
      App.modal({
        title: 'Void order ' + o.orderNo,
        size: 'narrow',
        body: '<p class="small muted" style="margin:0 0 12px">Voiding removes the sale from revenue, releases queue #' +
          (o.queueNumber || '--') + ', and keeps the record in history marked VOIDED.</p>' +
          '<div class="field" style="margin-bottom:0"><label>Reason</label>' +
          '<select class="select" id="voidReason">' +
            ['Customer cancelled', 'Wrong order entered', 'Duplicate order', 'Out of stock', 'Payment failed', 'Other'].map(function (r) {
              return '<option>' + r + '</option>';
            }).join('') +
          '</select></div>' +
          '<div class="field mt-2"><label>Note (optional)</label><input class="input" id="voidNote" placeholder="Short explanation"></div>',
        footer: '<button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-danger" data-confirm>Void order</button>',
        onMount: function (m, close) {
          m.querySelector('[data-confirm]').onclick = function () {
            const reason = m.querySelector('#voidReason').value;
            const note = m.querySelector('#voidNote').value.trim();
            close();
            o.voidReason = reason + (note ? ' \u2014 ' + note : '');
            o.voidedBy = App.state.session.accountId;
            o.kitchenStatus = 'Voided';
            o.voidedAt = U.nowISO();
            o.queueReleased = true;
            o.revision = (o.revision || 1) + 1;
            o.syncStatus = o.syncStatus === 'Sent' ? 'Pending' : o.syncStatus;
            DB.put('orders', o).then(function () {
              App.toast('Order voided', 'ok', 'Queue #' + (o.queueNumber || '--') + ' is available again.');
              Kitchen.renderStrip();
              if (App.state.page === 'kitchen') Kitchen.render();
              if (App.state.page === 'orders') Orders.render();
              if (App.state.page === 'pos') POS.render();
              App.refreshPendingCounts();
              if (navigator.onLine && API.isConfigured()) App.Sync.run({ silent: true });
            });
          };
        }
      });
    });
  };

  /* ------------------------------------------------------------------ */
  /*  Payment for unpaid orders                                        */
  /* ------------------------------------------------------------------ */
  Kitchen.takePayment = function (recordId) {
    if (!App.can('payment.collect')) { App.toast('Your role cannot take payment', 'err'); return; }
    DB.get('orders', recordId).then(function (o) {
      if (!o) return;
      if (o.paymentStatus === 'Paid') { App.toast('Already paid', 'warn'); return; }
      let method = App.paymentMethods()[0] || 'Cash', tendered = '';

      App.modal({
        title: 'Take payment \u00B7 #' + (o.queueNumber || '--'),
        body: '<div class="checkout-total"><div class="lbl">Amount due</div><div class="amt">' + U.money(o.total) + '</div></div>' +
          '<div class="field"><label>Payment type</label><div class="pay-methods" id="payMethods" ' +
            'style="grid-template-columns:repeat(' + App.paymentMethods().length + ',1fr)">' +
            App.paymentMethods().map(function (p) {
              return '<button class="pay-method' + (p === App.paymentMethods()[0] ? ' active' : '') + '" data-pay="' + U.esc(p) + '">' +
                '<span class="pm-ico">' + App.paymentIcon(p) + '</span><span>' + U.esc(p) + '</span></button>';
            }).join('') + '</div></div>' +
          '<div id="cashSection"><div class="field"><label>Cash received</label>' +
            '<input class="input cash-input" id="cashInput" inputmode="decimal" placeholder="0.00"></div>' +
            '<div class="cash-quick">' + [o.total, Math.ceil(o.total / 50) * 50, Math.ceil(o.total / 100) * 100].filter(function (v, i, a) { return a.indexOf(v) === i; })
              .map(function (v) { return '<button type="button" data-cash="' + v + '">' + U.money0(v) + '</button>'; }).join('') + '</div>' +
            '<div class="change-box" id="changeBox"><span class="cb-lbl">Change / Sukli</span><span class="cb-val" id="changeVal">\u20B10.00</span></div>' +
          '</div>',
        footer: '<button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary" data-ok>Confirm payment</button>',
        onMount: function (m, close) {
          const cashInput = m.querySelector('#cashInput');
          const changeBox = m.querySelector('#changeBox');
          const changeVal = m.querySelector('#changeVal');
          function upd() {
            const cash = U.num(tendered);
            const ch = U.round2(cash - o.total);
            changeVal.textContent = U.money(ch);
            changeBox.classList.toggle('short', cash > 0 && ch < 0);
          }
          m.querySelectorAll('[data-pay]').forEach(function (b) {
            b.onclick = function () {
              method = b.getAttribute('data-pay');
              m.querySelectorAll('[data-pay]').forEach(function (x) { x.classList.toggle('active', x === b); });
              m.querySelector('#cashSection').classList.toggle('hidden', method !== 'Cash');
              if (method !== 'Cash') { tendered = String(o.total); upd(); }
            };
          });
          m.querySelectorAll('[data-cash]').forEach(function (b) {
            b.onclick = function () { tendered = b.getAttribute('data-cash'); cashInput.value = tendered; upd(); };
          });
          cashInput.addEventListener('input', function () { tendered = cashInput.value; upd(); });
          m.querySelector('[data-ok]').onclick = function () {
            const cash = method === 'Cash' ? U.num(tendered) : o.total;
            if (method === 'Cash' && cash < o.total) { App.toast('Cash received is too low', 'err'); return; }
            close();
            o.paymentStatus = 'Paid';
            o.paymentMethod = method;
            o.amountTendered = U.round2(cash);
            o.changeDue = U.round2(cash - o.total);
            o.paidAt = U.nowISO();
            o.paidBy = App.state.session.accountId;
            o.updatedAt = U.nowISO();
            o.revision = (o.revision || 1) + 1;
            o.syncStatus = o.syncStatus === 'Sent' ? 'Pending' : o.syncStatus;
            DB.put('orders', o).then(function () {
              App.toast('Payment recorded', 'ok', method + ' \u00B7 ' + U.money(o.total) +
                (method === 'Cash' ? ' \u00B7 Change ' + U.money(o.changeDue) : ''));
              Kitchen.renderStrip();
              if (App.state.page === 'kitchen') Kitchen.render();
              if (App.state.page === 'orders') Orders.render();
              if (App.state.page === 'reports') Reports.render();
              App.refreshPendingCounts();
              if (navigator.onLine && API.isConfigured()) App.Sync.run({ silent: true });
            });
          };
        }
      });
    });
  };

  /* ------------------------------------------------------------------ */
  /*  Ticket detail                                                    */
  /* ------------------------------------------------------------------ */
  Kitchen.openTicket = function (recordId) {
    DB.get('orders', recordId).then(function (o) {
      if (!o) return;
      const rows = (o.lineItems || []).map(function (l) {
        return '<tr><td>' + U.esc(l.name) + '</td><td class="num">' + l.qty + '</td>' +
          '<td class="num">' + U.money(l.price) + '</td><td class="num">' + U.money(l.lineTotal) + '</td></tr>';
      }).join('');
      const vol = Object.keys(o.volumeTotals || {}).map(function (k) {
        return '<span class="badge gray">' + U.esc(k) + ' \u00D7' + o.volumeTotals[k] + '</span>';
      }).join(' ');

      App.modal({
        title: 'Order ' + o.orderNo,
        size: 'wide',
        body:
          '<div class="row" style="gap:8px;margin-bottom:12px">' +
            Kitchen.statusBadge(o.kitchenStatus) +
            '<span class="badge ' + (o.paymentStatus === 'Paid' ? 'ok' : 'danger') + '">' + U.esc(o.paymentStatus) + '</span>' +
            '<span class="badge gray">Queue #' + (o.queueNumber || '--') + '</span>' +
            '<span class="badge gray">' + U.esc(o.orderType || '') + '</span>' +
            '<span class="badge ' + (o.syncStatus === 'Sent' ? 'ok' : o.syncStatus === 'Needs Review' ? 'danger' : 'warn') + '">' +
              U.esc(o.syncStatus) + '</span>' +
          '</div>' +
          '<div class="table-wrap"><table class="tbl"><thead><tr><th>Item</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Total</th></tr></thead>' +
          '<tbody>' + rows + '</tbody></table></div>' +
          '<div class="row mt-2" style="justify-content:space-between">' +
            '<div class="small muted">' + U.esc(o.cashierName) + ' \u00B7 ' + U.fmtDateTime(o.createdAt) + '</div>' +
            '<div><strong style="font-size:1.15rem;color:var(--brand)">' + U.money(o.total) + '</strong></div>' +
          '</div>' +
          (vol ? '<div class="mt-2">' + vol + '</div>' : '') +
          (o.notes ? '<div class="card mt-2" style="background:var(--bg-2)"><strong class="small">Note:</strong> <span class="small">' + U.esc(o.notes) + '</span></div>' : '') +
          (o.voidReason ? '<div class="card mt-2" style="background:var(--danger-soft);border-color:rgba(255,95,109,.3)"><strong class="small">Voided:</strong> <span class="small">' + U.esc(o.voidReason) + '</span></div>' : '') +
          '<div class="small muted mt-2 mono">Record ID: ' + U.esc(o.recordId) + '</div>',
        footer: '<button class="btn btn-ghost" data-close>Close</button>' +
          '<button class="btn" data-print>Print receipt</button>' +
          (ACTIVE.indexOf(o.kitchenStatus) >= 0 ? '<button class="btn btn-primary" data-next2>Next status</button>' : ''),
        onMount: function (m, close) {
          m.querySelector('[data-print]').onclick = function () { Receipt.print(o); };
          const n = m.querySelector('[data-next2]');
          if (n) n.onclick = function () { close(); Kitchen.advance(o.recordId); };
        }
      });
    });
  };

  /* ------------------------------------------------------------------ */
  /*  Live refresh (no websocket: reads the local DB on a timer)         */
  /* ------------------------------------------------------------------ */
  Kitchen.startTicker = function () {
    if (ticker) clearInterval(ticker);
    ticker = setInterval(function () {
      if (!App.state.session || !App.feature('kitchen')) return;
      DB.getAll('orders').then(function (all) {
        const active = all.filter(function (o) { return ACTIVE.indexOf(o.kitchenStatus) >= 0; });
        const hash = active.map(function (o) { return o.recordId + o.kitchenStatus + o.paymentStatus; }).join('|');
        if (hash !== lastHash) {
          lastHash = hash;
          if (App.state.page === 'kitchen') Kitchen.render();
          if (App.state.page === 'pos') Kitchen.renderStrip();
        } else if (App.state.page === 'kitchen' || App.state.page === 'pos') {
          Kitchen.renderStrip();
          if (App.state.page === 'kitchen') Kitchen.render();
        }
      });
    }, 6000);
  };

  global.Kitchen = Kitchen;
})(window);
