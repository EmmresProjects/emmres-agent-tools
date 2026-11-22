/* =====================================================================
   {{KIT_TITLE}} - Point of Sale screen
   Large product buttons, direct quantity entry, exact change, queue cards.
   ===================================================================== */
(function (global) {
  'use strict';

  const POS = {};
  let cart = { lines: [], queueNumber: null, orderType: 'Dine-in', notes: '', discount: null };
  let search = '';
  let category = 'All';

  POS.state = function () { return cart; };

  POS.resetCart = function () {
    cart = { lines: [], queueNumber: null, orderType: 'Dine-in', notes: '', discount: null };
  };

  POS.init = function () { POS.render(); };

  POS.focusSearch = function () {
    const s = document.getElementById('posSearch');
    if (s) s.focus();
  };

  /* ---------------------------------------------------------------- */
  /*  Cart maths                                                      */
  /* ---------------------------------------------------------------- */
  POS.subtotal = function () {
    return U.round2(U.sum(cart.lines, function (l) { return l.qty * l.price; }));
  };
  POS.discountAmount = function () {
    if (!cart.discount) return 0;
    const sub = POS.subtotal();
    if (cart.discount.type === 'percent') return U.round2(sub * (U.num(cart.discount.value) / 100));
    return Math.min(sub, U.round2(U.num(cart.discount.value)));
  };
  POS.total = function () { return U.round2(Math.max(0, POS.subtotal() - POS.discountAmount())); };
  POS.itemCount = function () { return U.sum(cart.lines, function (l) { return l.qty; }); };

  POS.volumeFor = function () {
    const out = {};
    cart.lines.forEach(function (l) {
      (l.volume || []).forEach(function (v) {
        out[v.unit] = U.round2((out[v.unit] || 0) + v.qty * l.qty);
      });
    });
    return out;
  };

  /* ---------------------------------------------------------------- */
  /*  Add / change lines                                              */
  /* ---------------------------------------------------------------- */
  POS.addProduct = function (productId, qty) {
    const p = App.state.products.find(function (x) { return x.id === productId; });
    if (!p) return;
    const n = Math.max(1, U.int(qty || 1));
    const line = cart.lines.find(function (l) { return l.productId === productId; });
    if (line) line.qty = U.round2(line.qty + n);
    else {
      cart.lines.push({
        productId: p.id, name: p.name, price: U.num(p.price), qty: n,
        volume: p.volume || [], contents: p.contents || ''
      });
    }
    POS.render();
    POS.flashCart();
  };

  POS.setQty = function (productId, qty) {
    const line = cart.lines.find(function (l) { return l.productId === productId; });
    if (!line) return;
    const q = U.num(qty);
    if (q <= 0) cart.lines = cart.lines.filter(function (l) { return l.productId !== productId; });
    else line.qty = q;
    POS.render();
  };

  POS.bumpQty = function (productId, delta) {
    const line = cart.lines.find(function (l) { return l.productId === productId; });
    if (!line) return;
    POS.setQty(productId, line.qty + delta);
  };

  POS.clearCart = function () {
    if (!cart.lines.length) return;
    App.confirm('Clear this order?', 'All items in the cart will be removed. Queue number stays as it is.',
      { okText: 'Clear cart', danger: true }).then(function (yes) {
      if (!yes) return;
      cart.lines = []; cart.discount = null;
      POS.render();
    });
  };

  POS.flashCart = function () {
    const el = document.getElementById('cartBody');
    if (!el) return;
    el.style.transition = 'none';
    el.style.background = 'rgba(255,176,46,.10)';
    setTimeout(function () { el.style.transition = 'background .45s'; el.style.background = 'transparent'; }, 90);
  };

  /* ---------------------------------------------------------------- */
  /*  Queue cards                                                     */
  /* ---------------------------------------------------------------- */
  POS.queueRange = function () {
    const min = App.settingNum('QueueMin', 1);
    const max = App.settingNum('QueueMax', 18);
    const out = [];
    for (let i = min; i <= max; i++) out.push(i);
    return out;
  };

  /* Active (unfinished) orders hold a queue card */
  POS.usedQueueNumbers = function () {
    return DB.getAll('orders').then(function (orders) {
      const used = {};
      orders.forEach(function (o) {
        if (!o.queueNumber) return;
        if (o.kitchenStatus === 'Served' || o.kitchenStatus === 'Voided') return;
        if (o.branchId !== App.state.branchId) return;
        used[o.queueNumber] = o;
      });
      return used;
    });
  };

  POS.availableQueue = function () {
    return POS.usedQueueNumbers().then(function (used) {
      return POS.queueRange().filter(function (n) { return !used[n]; });
    });
  };

  /* ---------------------------------------------------------------- */
  /*  Render                                                          */
  /* ---------------------------------------------------------------- */
  POS.render = function () {
    const host = document.getElementById('page-pos');
    if (!host || host.classList.contains('hidden') && App.state.page !== 'pos') { /* still render */ }
    if (!document.getElementById('posProducts')) return;

    POS.renderProducts();
    POS.renderCart();
    if (typeof Kitchen !== 'undefined' && Kitchen.renderStrip) Kitchen.renderStrip();
    POS.renderFab();
  };

  POS.renderProducts = function () {
    const host = document.getElementById('posProducts');
    if (!host) return;
    const catIds = Array.from(new Set(App.state.products.map(function (p) { return p.category; })))
      .filter(Boolean)
      .sort(function (a, b) { return App.categoryLabel(a).localeCompare(App.categoryLabel(b)); });
    const cats = ['All'].concat(catIds);
    const tabsHost = document.getElementById('posCategories');
    if (tabsHost) {
      tabsHost.innerHTML = cats.map(function (c) {
        const label = c === 'All' ? 'All' : App.categoryLabel(c);
        return '<button class="cat-tab' + (c === category ? ' active' : '') + '" data-cat="' + U.esc(c) + '">' + U.esc(label) + '</button>';
      }).join('');
      tabsHost.querySelectorAll('[data-cat]').forEach(function (b) {
        b.onclick = function () { category = b.getAttribute('data-cat'); POS.renderProducts(); };
      });
    }

    const q = search.toLowerCase().trim();
    const list = App.state.products.filter(function (p) {
      if (String(p.status || 'Active') !== 'Active') return false;
      if (category !== 'All' && p.category !== category) return false;
      if (!q) return true;
      return (p.name + ' ' + p.category + ' ' + (p.contents || '')).toLowerCase().indexOf(q) >= 0;
    });

    if (!list.length) {
      host.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="es-ico">\uD83C\uDF57</div>' +
        '<h4>No products match</h4><p>Try another category or clear the search.</p></div>';
      return;
    }

    host.innerHTML = list.map(function (p) {
      return '<button class="prod-btn" data-add="' + U.esc(p.id) + '">' +
        '<div><div class="pname">' + U.esc(p.name) + '</div><div class="pcat">' + U.esc(App.categoryLabel(p.category)) + '</div></div>' +
        '<div class="prow"><span class="pprice">' + U.money(p.price) + '</span>' +
        (p.contents ? '<span class="combo-tag">COMBO</span>' : (p.volumeLabel ? '<span class="pvol">' + U.esc(p.volumeLabel) + '</span>' : '')) +
        '</div></button>';
    }).join('');

    host.querySelectorAll('[data-add]').forEach(function (b) {
      b.onclick = function () { POS.addProduct(b.getAttribute('data-add'), 1); };
      // long press = add 5
      let t;
      b.addEventListener('touchstart', function () {
        t = setTimeout(function () { POS.addProduct(b.getAttribute('data-add'), 5); }, 700);
      }, { passive: true });
      b.addEventListener('touchend', function () { clearTimeout(t); });
      b.addEventListener('contextmenu', function (e) { e.preventDefault(); POS.addProduct(b.getAttribute('data-add'), 5); });
    });
  };

  POS.renderCart = function () {
    const host = document.getElementById('cartBody');
    if (!host) return;

    if (!cart.lines.length) {
      host.innerHTML = '<div class="cart-empty">Tap a product to start the order.<br><span class="small">Hold a product button to add 5 at once.</span></div>';
    } else {
      host.innerHTML = cart.lines.map(function (l) {
        return '<div class="cart-line">' +
          '<div class="cl-name">' + U.esc(l.name) +
            (l.contents ? '<div class="cl-sub">' + U.esc(l.contents) + '</div>' : '') +
            '<div class="cl-sub">' + U.money(l.price) + ' each</div></div>' +
          '<div class="qty-box">' +
            '<button data-dec="' + U.esc(l.productId) + '" aria-label="Less">\u2212</button>' +
            '<input data-qty="' + U.esc(l.productId) + '" inputmode="numeric" value="' + l.qty + '">' +
            '<button data-inc="' + U.esc(l.productId) + '" aria-label="More">+</button>' +
          '</div>' +
          '<div class="cl-total">' + U.money(l.qty * l.price) + '</div>' +
        '</div>';
      }).join('');

      host.querySelectorAll('[data-inc]').forEach(function (b) { b.onclick = function () { POS.bumpQty(b.getAttribute('data-inc'), 1); }; });
      host.querySelectorAll('[data-dec]').forEach(function (b) { b.onclick = function () { POS.bumpQty(b.getAttribute('data-dec'), -1); }; });
      host.querySelectorAll('[data-qty]').forEach(function (inp) {
        inp.onchange = function () { POS.setQty(inp.getAttribute('data-qty'), inp.value); };
        inp.onfocus = function () { inp.select(); };
        inp.onkeydown = function (e) { if (e.key === 'Enter') { inp.blur(); } };
      });
    }

    const sub = POS.subtotal();
    const disc = POS.discountAmount();
    const total = POS.total();
    const el = function (id) { return document.getElementById(id); };
    if (el('cartSubtotal')) el('cartSubtotal').textContent = U.money(sub);
    if (el('cartDiscountRow')) el('cartDiscountRow').classList.toggle('hidden', disc <= 0);
    if (el('cartDiscount')) el('cartDiscount').textContent = '-' + U.money(disc);
    if (el('cartTotal')) el('cartTotal').textContent = U.money(total);
    if (el('cartCount')) el('cartCount').textContent = POS.itemCount() + (POS.itemCount() === 1 ? ' item' : ' items');
    if (el('cartQueue')) el('cartQueue').textContent = cart.queueNumber ? 'Queue #' + cart.queueNumber : 'No queue card';
    const co = document.getElementById('checkoutBtn');
    if (co) co.disabled = !cart.lines.length;
    const hold = document.getElementById('holdBtn');
    if (hold) hold.disabled = !cart.lines.length;
  };

  POS.renderFab = function () {
    const fab = document.getElementById('cartFab');
    if (!fab) return;
    const n = POS.itemCount();
    fab.innerHTML = '<span>\uD83D\uDED2 ' + (n ? n + ' items \u00B7 ' + U.money(POS.total()) : 'Cart') + '</span>';
  };

  POS.toggleCart = function () {
    const c = document.getElementById('cartPanel');
    if (!c) return;
    if (c.classList.contains('open')) {
      c.classList.remove('open');
      const b = document.querySelector('.cart-backdrop');
      if (b) b.remove();
    } else {
      c.classList.add('open');
      const bd = document.createElement('div');
      bd.className = 'cart-backdrop';
      bd.onclick = function () { c.classList.remove('open'); bd.remove(); };
      document.body.appendChild(bd);
    }
  };

  /* ---------------------------------------------------------------- */
  /*  Queue picker                                                    */
  /* ---------------------------------------------------------------- */
  POS.openQueuePicker = function () {
    if (!App.feature('queueCards')) { App.toast('Queue cards are turned off for this shop', 'warn'); return; }
    POS.usedQueueNumbers().then(function (used) {
      const range = POS.queueRange();
      const avail = range.filter(function (n) { return !used[n]; });
      App.modal({
        title: 'Choose queue card',
        size: 'narrow',
        body:
          '<p class="small muted" style="margin:0 0 12px">Queue cards run from ' + range[0] + ' to ' + range[range.length - 1] +
          '. A number is released as soon as the order is served or voided.</p>' +
          '<div class="qgrid" id="qgrid">' +
          range.map(function (n) {
            const isUsed = !!used[n];
            const sel = cart.queueNumber === n;
            return '<button class="qbtn ' + (sel ? 'selected' : isUsed ? 'used' : 'avail') + '" ' +
              (isUsed ? 'disabled' : '') + ' data-q="' + n + '">' + n + '</button>';
          }).join('') +
          '</div>' +
          '<div class="q-legend">' +
            '<span><i style="background:var(--ok)"></i>Available (' + avail.length + ')</span>' +
            '<span><i style="background:var(--surface-3)"></i>In use (' + (range.length - avail.length) + ')</span>' +
          '</div>' +
          (avail.length === 0 ? '<p class="small" style="color:var(--danger);margin:12px 0 0">All queue cards are in use. Serve or void an order first.</p>' : ''),
        footer: '<button class="btn btn-ghost" data-clear>Clear</button><button class="btn btn-primary" data-ok>Use number</button>',
        onMount: function (m, close) {
          let picked = cart.queueNumber;
          m.querySelectorAll('[data-q]').forEach(function (b) {
            b.onclick = function () {
              picked = U.int(b.getAttribute('data-q'));
              m.querySelectorAll('[data-q]').forEach(function (x) {
                x.classList.remove('selected');
                if (!x.disabled) x.classList.add('avail');
              });
              b.classList.add('selected');
            };
          });
          m.querySelector('[data-clear]').onclick = function () { cart.queueNumber = null; close(); POS.render(); };
          m.querySelector('[data-ok]').onclick = function () {
            cart.queueNumber = picked || null;
            close();
            POS.render();
            if (!picked) App.toast('No queue card selected', 'warn', 'You can still save the order.');
          };
        }
      });
    });
  };

  /* ---------------------------------------------------------------- */
  /*  Discount                                                        */
  /* ---------------------------------------------------------------- */
  POS.openDiscount = function () {
    if (!App.can('order.discount')) { App.toast('Your role cannot give discounts', 'err'); return; }
    const cur = cart.discount || { type: 'percent', value: '' };
    App.modal({
      title: 'Discount',
      size: 'narrow',
      body:
        '<div class="field"><label>Type</label><select class="select" id="dType">' +
          '<option value="percent"' + (cur.type === 'percent' ? ' selected' : '') + '>Percent (%)</option>' +
          '<option value="amount"' + (cur.type === 'amount' ? ' selected' : '') + '>Amount (\u20B1)</option>' +
        '</select></div>' +
        '<div class="field"><label>Value</label><input class="input" id="dVal" inputmode="decimal" value="' + U.esc(cur.value) + '"></div>' +
        '<div class="cash-quick">' +
          [5, 10, 15, 20].map(function (p) { return '<button type="button" data-p="' + p + '">' + p + '%</button>'; }).join('') +
        '</div>',
      footer: '<button class="btn btn-ghost" data-clear>Remove</button><button class="btn btn-primary" data-ok>Apply</button>',
      onMount: function (m, close) {
        m.querySelectorAll('[data-p]').forEach(function (b) {
          b.onclick = function () {
            m.querySelector('#dType').value = 'percent';
            m.querySelector('#dVal').value = b.getAttribute('data-p');
          };
        });
        m.querySelector('[data-clear]').onclick = function () { cart.discount = null; close(); POS.render(); };
        m.querySelector('[data-ok]').onclick = function () {
          const type = m.querySelector('#dType').value;
          const val = U.num(m.querySelector('#dVal').value);
          cart.discount = val > 0 ? { type: type, value: val } : null;
          close(); POS.render();
        };
      }
    });
  };

  /* ---------------------------------------------------------------- */
  /*  Checkout                                                        */
  /* ---------------------------------------------------------------- */
  POS.openCheckout = function () {
    if (!cart.lines.length) { App.toast('Cart is empty', 'warn', 'Tap a product first.'); return; }

    const total = POS.total();
    const PAYMENTS = App.paymentMethods();
    let method = PAYMENTS[0] || 'Cash';
    let tendered = '';
    let queueNumber = cart.queueNumber;

    POS.usedQueueNumbers().then(function (used) {
      const avail = POS.queueRange().filter(function (n) { return !used[n]; });
      const useQueue = App.feature('queueCards');
      if (!queueNumber && useQueue && App.settingBool('RequireQueueNumber', true) && avail.length) queueNumber = avail[0];

      const quickCash = (function () {
        const base = [20, 50, 100, 200, 500, 1000];
        const out = [total];
        base.forEach(function (b) { out.push(b); });
        // next round amounts above the total
        [50, 100, 500, 1000].forEach(function (step) {
          const v = Math.ceil(total / step) * step;
          if (v > total) out.push(v);
        });
        return Array.from(new Set(out.map(function (v) { return U.round2(v); }))).filter(function (v) { return v >= total || v === total; }).slice(0, 7);
      })();

      const inst = App.modal({
        title: 'Checkout',
        body:
          '<div class="checkout-total"><div class="lbl">Amount due</div><div class="amt">' + U.money(total) + '</div>' +
            '<div class="small muted">' + POS.itemCount() + ' item(s) \u00B7 Subtotal ' + U.money(POS.subtotal()) +
            (POS.discountAmount() ? ' \u00B7 Discount -' + U.money(POS.discountAmount()) : '') + '</div></div>' +

          '<div class="field"><label>Payment type</label>' +
            '<div class="pay-methods" id="payMethods" style="grid-template-columns:repeat(' + PAYMENTS.length + ',1fr)">' +
              PAYMENTS.map(function (p) {
                return '<button class="pay-method' + (p === method ? ' active' : '') + '" data-pay="' + U.esc(p) + '">' +
                  '<span class="pm-ico">' + App.paymentIcon(p) + '</span><span>' + U.esc(p) + '</span></button>';
              }).join('') +
            '</div></div>' +

          '<div id="cashSection">' +
            '<div class="field"><label>Cash received</label>' +
              '<input class="input cash-input" id="cashInput" inputmode="decimal" placeholder="0.00" value=""></div>' +
            '<div class="cash-quick" id="quickCash">' +
              quickCash.map(function (v) { return '<button type="button" data-cash="' + v + '">' + (v === total ? 'Exact ' + U.money0(v) : U.money0(v)) + '</button>'; }).join('') +
            '</div>' +
            '<div class="keypad" id="keypad">' +
              ['1','2','3','4','5','6','7','8','9','.','0','\u232B'].map(function (k) {
                return '<button type="button" data-k="' + k + '">' + k + '</button>';
              }).join('') +
            '</div>' +
            '<div class="change-box" id="changeBox">' +
              '<span class="cb-lbl">Change / Sukli</span><span class="cb-val" id="changeVal">\u20B10.00</span>' +
            '</div>' +
          '</div>' +

          (useQueue ? '<div class="sep"></div>' +
          '<div class="field"><label>Queue card</label>' +
            '<div class="row row-nowrap"><select class="select grow" id="queueSelect">' +
              '<option value="">No queue card</option>' +
              POS.queueRange().map(function (n) {
                const isUsed = !!used[n] && n !== cart.queueNumber;
                return '<option value="' + n + '"' + (n === queueNumber ? ' selected' : '') + (isUsed ? ' disabled' : '') + '>' +
                  'Queue #' + n + (isUsed ? ' (in use)' : '') + '</option>';
              }).join('') +
            '</select></div></div>'
          : '') +
          '<div class="field" style="margin-bottom:0"><label>Note (optional)</label>' +
            '<input class="input" id="orderNote" placeholder="e.g. no chili, extra sauce" value="' + U.esc(cart.notes) + '"></div>' +
          (App.settingBool('AllowUnpaidOrders', true) ?
            '<p class="small muted" style="margin:12px 0 0">Leaving it unpaid keeps the order in the kitchen queue. You can mark it paid later from Orders.</p>' : ''),

        footer:
          '<button class="btn btn-ghost" data-close>Back</button>' +
          (App.settingBool('AllowUnpaidOrders', true) ? '<button class="btn" data-unpaid>Order Only (Unpaid)</button>' : '') +
          '<button class="btn btn-primary btn-lg" data-pay-now>Pay &amp; Save</button>',

        onMount: function (m, close) {
          const cashInput = m.querySelector('#cashInput');
          const changeBox = m.querySelector('#changeBox');
          const changeVal = m.querySelector('#changeVal');

          function setMethod(p) {
            method = p;
            m.querySelectorAll('[data-pay]').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-pay') === p); });
            m.querySelector('#cashSection').classList.toggle('hidden', String(p).toLowerCase() !== 'cash');
            if (p !== 'Cash') { tendered = String(total); updateChange(); }
          }
          function updateChange() {
            const cash = U.num(tendered);
            const change = U.round2(cash - total);
            changeVal.textContent = U.money(change);
            changeBox.classList.toggle('short', cash > 0 && change < 0);
          }
          function pushKey(k) {
            if (k === '\u232B') tendered = String(tendered).slice(0, -1);
            else if (k === '.') { if (String(tendered).indexOf('.') < 0) tendered = (String(tendered) || '0') + '.'; }
            else tendered = String(tendered) + k;
            if (String(tendered).length > 1 && String(tendered).startsWith('0') && !String(tendered).startsWith('0.')) {
              tendered = String(tendered).replace(/^0+/, '');
            }
            cashInput.value = tendered;
            updateChange();
          }

          m.querySelectorAll('[data-pay]').forEach(function (b) { b.onclick = function () { setMethod(b.getAttribute('data-pay')); }; });
          m.querySelectorAll('[data-cash]').forEach(function (b) {
            b.onclick = function () { tendered = b.getAttribute('data-cash'); cashInput.value = tendered; updateChange(); };
          });
          m.querySelectorAll('[data-k]').forEach(function (b) { b.onclick = function () { pushKey(b.getAttribute('data-k')); }; });
          cashInput.addEventListener('input', function () { tendered = cashInput.value; updateChange(); });
          const qSel = m.querySelector('#queueSelect');
          if (qSel) qSel.onchange = function (e) { queueNumber = e.target.value ? U.int(e.target.value) : null; };
          m.querySelector('#orderNote').addEventListener('input', function (e) { cart.notes = e.target.value; });
          setMethod(App.paymentMethods()[0]);

          m.querySelector('[data-pay-now]').onclick = function () {
            const isCash = String(method).toLowerCase() === 'cash';
            const cash = isCash ? U.num(tendered) : total;
            if (isCash && cash < total) {
              App.toast('Cash received is less than the total', 'err', 'Enter the amount the customer handed over.');
              cashInput.focus();
              return;
            }
            const note = m.querySelector('#orderNote').value.trim();
            cart.notes = note;
            close();
            POS.completeOrder({
              paymentMethod: method,
              amountTendered: cash,
              changeDue: U.round2(cash - total),
              paymentStatus: 'Paid',
              queueNumber: queueNumber,
              notes: note
            });
          };

          const unpaidBtn = m.querySelector('[data-unpaid]');
          if (unpaidBtn) unpaidBtn.onclick = function () {
            const note = m.querySelector('#orderNote').value.trim();
            cart.notes = note;
            close();
            POS.completeOrder({
              paymentMethod: 'Unpaid', amountTendered: 0, changeDue: 0,
              paymentStatus: 'Unpaid', queueNumber: queueNumber, notes: note
            });
          };
        }
      });
      void inst;
    });
  };

  /* ---------------------------------------------------------------- */
  /*  Save the order (local first, then push)                         */
  /* ---------------------------------------------------------------- */
  POS.completeOrder = function (opts) {
    const total = POS.total();
    const subtotal = POS.subtotal();
    const discAmt = POS.discountAmount();
    const now = new Date();
    const dateKey = U.dateKey(now);
    const s = App.state.session;

    const order = {
      recordId: U.recordId(),
      orderNo: 'ORD-' + dateKey.replace(/-/g, '') + '-' + String(Math.floor(Math.random() * 9000) + 1000),
      branchId: App.state.branchId,
      branchName: App.branchName(App.state.branchId),
      accountId: s.accountId,
      cashierName: s.displayName,
      deviceId: API.deviceId(),
      businessDate: dateKey,
      createdAt: U.nowISO(),
      updatedAt: U.nowISO(),
      queueNumber: opts.queueNumber || null,
      orderType: cart.orderType || 'Dine-in',
      lineItems: cart.lines.map(function (l) {
        return {
          productId: l.productId, name: l.name, qty: l.qty, price: l.price,
          lineTotal: U.round2(l.qty * l.price),
          volume: (l.volume || []).map(function (v) { return { unit: v.unit, qty: v.qty }; })
        };
      }),
      volumeTotals: POS.volumeFor(),
      subtotal: subtotal,
      discountType: cart.discount ? cart.discount.type : '',
      discountValue: cart.discount ? U.num(cart.discount.value) : 0,
      discountAmount: discAmt,
      total: total,
      paymentMethod: opts.paymentMethod,
      amountTendered: U.round2(opts.amountTendered),
      changeDue: U.round2(opts.changeDue),
      paymentStatus: opts.paymentStatus,
      paidAt: opts.paymentStatus === 'Paid' ? U.nowISO() : '',
      kitchenStatus: 'New',
      kitchenUpdatedAt: U.nowISO(),
      servedAt: '',
      voidedAt: '', voidReason: '', voidedBy: '',
      notes: opts.notes || '',
      revision: 1,
      syncStatus: 'Pending',
      sentAt: '',
      needsReviewReason: ''
    };

    DB.put('orders', order).then(function () {
      POS.resetCart();
      POS.render();
      App.refreshPendingCounts();
      if (typeof Kitchen !== 'undefined' && Kitchen.renderStrip) Kitchen.renderStrip();
      App.toast('Order ' + order.orderNo + ' saved', 'ok',
        opts.paymentStatus === 'Paid'
          ? (opts.paymentMethod === 'Cash' ? 'Change: ' + U.money(opts.changeDue) : 'Paid via ' + opts.paymentMethod)
          : 'Unpaid \u2014 still in the kitchen queue');

      if (navigator.onLine && API.isConfigured()) {
        App.Sync.run({ silent: true });
      }
      POS.offerReceipt(order);
      if (typeof Orders !== 'undefined' && App.state.page === 'orders') Orders.render();
    }).catch(function (e) {
      App.toast('Could not save the order', 'err', String(e && e.message || e));
    });
  };

  POS.offerReceipt = function (order) {
    const choice = App.el('#autoPrint');
    if (App.settingBool('AutoPrintReceipt', false)) { Receipt.print(order); return; }
    App.modal({
      title: 'Order saved \u2014 #' + (order.queueNumber || '--'),
      size: 'narrow',
      body: '<p style="margin:0 0 12px;color:var(--text-dim);line-height:1.6">' +
        '<strong style="color:var(--text)">' + U.esc(order.orderNo) + '</strong><br>' +
        'Total ' + U.money(order.total) + (order.paymentStatus === 'Paid'
          ? (order.paymentMethod === 'Cash' ? ' \u00B7 Change ' + U.money(order.changeDue) : ' \u00B7 ' + order.paymentMethod)
          : ' \u00B7 <span style="color:var(--warn)">UNPAID</span>') +
        '</p><p class="small muted" style="margin:0">Next order is ready. Print a receipt if the customer needs one.</p>',
      footer: '<button class="btn btn-ghost" data-close>Done</button><button class="btn btn-primary" data-print>Print receipt</button>',
      onMount: function (m, close) {
        m.querySelector('[data-print]').onclick = function () { close(); Receipt.print(order); };
      }
    });
  };

  /* ---------------------------------------------------------------- */
  /*  Receipt printing                                                */
  /* ---------------------------------------------------------------- */
  const Receipt = {};
  Receipt.html = function (o) {
    const biz = App.setting('BusinessName', '{{BUSINESS_NAME}}');
    const footer = App.setting('ReceiptFooter', 'Salamat! Balik-balik po.');
    return '<div class="rcpt">' +
      '<div class="c b t">' + U.esc(biz) + '</div>' +
      '<div class="c s">' + U.esc(App.branchName(o.branchId)) + '</div>' +
      '<div class="c s">' + U.esc(o.orderNo) + '</div>' +
      '<div class="hr"></div>' +
      '<div class="r s"><span>' + U.fmtDateTime(o.createdAt) + '</span><span>#' + (o.queueNumber || '--') + '</span></div>' +
      '<div class="r s"><span>Cashier: ' + U.esc(o.cashierName || '') + '</span><span>' + U.esc(o.orderType || '') + '</span></div>' +
      '<div class="hr"></div>' +
      o.lineItems.map(function (l) {
        return '<div class="r"><span>' + l.qty + ' x ' + U.esc(l.name) + '</span><span>' + U.money(l.lineTotal) + '</span></div>';
      }).join('') +
      '<div class="hr"></div>' +
      '<div class="r"><span>Subtotal</span><span>' + U.money(o.subtotal) + '</span></div>' +
      (o.discountAmount ? '<div class="r"><span>Discount</span><span>-' + U.money(o.discountAmount) + '</span></div>' : '') +
      '<div class="r b t2"><span>TOTAL</span><span>' + U.money(o.total) + '</span></div>' +
      '<div class="r"><span>' + U.esc(o.paymentMethod) + '</span><span>' + U.money(o.amountTendered) + '</span></div>' +
      (o.paymentStatus === 'Paid' ? '<div class="r b"><span>CHANGE</span><span>' + U.money(o.changeDue) + '</span></div>' :
        '<div class="r b c" style="color:#000"><span>*** UNPAID ***</span></div>') +
      (o.notes ? '<div class="s" style="margin-top:4px">Note: ' + U.esc(o.notes) + '</div>' : '') +
      '<div class="hr"></div>' +
      '<div class="c s">' + U.esc(footer) + '</div>' +
      '<div class="c xs">' + U.esc(App.setting('DeviceLabel', API.deviceLabel())) + ' \u00B7 ' + U.esc(o.recordId.slice(0, 8)) + '</div>' +
      '</div>';
  };

  Receipt.print = function (order) {
    const css = '<style>' +
      '*{box-sizing:border-box}body{font-family:"Courier New",monospace;font-size:12px;margin:0;padding:8px;width:80mm;color:#000}' +
      '.c{text-align:center}.r{display:flex;justify-content:space-between;gap:8px}.b{font-weight:bold}.t{font-size:15px;font-weight:bold}' +
      '.t2{font-size:14px}.s{font-size:11px}.xs{font-size:9px;color:#444}' +
      '.hr{border-top:1px dashed #000;margin:5px 0}.rcpt{width:100%}' +
      '@media print{@page{margin:4mm}}' +
      '</style>';
    const html = '<!doctype html><html><head><meta charset="utf-8"><title>Receipt</title>' + css + '</head><body>' +
      Receipt.html(order) + '</body></html>';

    // Electron / Cordova friendly: open a print window; fall back to a hidden iframe
    try {
      const w = window.open('', '_blank', 'width=420,height=640');
      if (w) { w.document.write(html); w.document.close(); w.focus(); setTimeout(function () { try { w.print(); } catch (e) {} }, 350); return; }
    } catch (e) { /* blocked */ }
    const iframe = document.createElement('iframe');
    iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;';
    document.body.appendChild(iframe);
    iframe.contentDocument.open();
    iframe.contentDocument.write(html);
    iframe.contentDocument.close();
    setTimeout(function () {
      try { iframe.contentWindow.focus(); iframe.contentWindow.print(); } catch (e) { App.toast('Printing not available here', 'warn'); }
      setTimeout(function () { iframe.remove(); }, 1500);
    }, 350);
  };

  /* ---------------------------------------------------------------- */
  /*  Wire up the DOM once                                            */
  /* ---------------------------------------------------------------- */
  document.addEventListener('DOMContentLoaded', function () {
    const s = document.getElementById('posSearch');
    if (s) s.addEventListener('input', U.debounce(function () { search = s.value; POS.renderProducts(); }, 120));

    const q = document.getElementById('queueBtn');
    if (q) q.onclick = POS.openQueuePicker;
    const qb = document.getElementById('cartQueue');
    if (qb) qb.onclick = POS.openQueuePicker;

    const co = document.getElementById('checkoutBtn');
    if (co) co.onclick = POS.openCheckout;

    const hold = document.getElementById('holdBtn');
    if (hold) hold.onclick = function () {
      if (!cart.lines.length) return;
      POS.openCheckout();
    };

    const clear = document.getElementById('clearCartBtn');
    if (clear) clear.onclick = POS.clearCart;

    const disc = document.getElementById('discountBtn');
    if (disc) {
      disc.onclick = POS.openDiscount;
      disc.classList.toggle('hidden', !App.feature('discounts'));
    }
    const qBtn = document.getElementById('queueBtn');
    if (qBtn) qBtn.classList.toggle('hidden', !App.feature('queueCards'));

    const fab = document.getElementById('cartFab');
    if (fab) fab.onclick = POS.toggleCart;
  });

  global.Receipt = Receipt;
  global.POS = POS;
})(window);
