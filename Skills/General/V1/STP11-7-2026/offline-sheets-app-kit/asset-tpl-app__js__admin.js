/* =====================================================================
   {{KIT_TITLE}} - Admin screens
   Products & menu, accounts & roles, branches.
   Changes are saved locally first, then pushed to Google Sheets.
   ===================================================================== */
(function (global) {
  'use strict';

  const Admin = {};

  /* ------------------------------------------------------------------ */
  /*  PRODUCTS                                                          */
  /* ------------------------------------------------------------------ */
  Admin.renderProducts = function () {
    const host = document.getElementById('productsBody');
    if (!host) return;

    const products = App.state.products;
    const cats = Array.from(new Set(products.map(function (p) { return p.category; }))).filter(Boolean).sort();

    host.innerHTML =
      '<div class="card card-pad-0">' +
        '<div class="card-head" style="padding:14px 16px 0">' +
          '<h3>Menu (' + products.length + ' items)</h3>' +
          '<span class="sub">Price, category and product volume per item</span>' +
          '<div class="grow"></div>' +
          '<button class="btn btn-ghost btn-sm" id="pCatBtn">Categories (' + cats.length + ')</button>' +
          '<button class="btn btn-primary btn-sm" id="pAdd">+ New product</button>' +
        '</div>' +
        '<div class="table-wrap mt-1">' +
          '<table class="tbl"><thead><tr>' +
            '<th>Product</th><th>Category</th><th class="num">Price</th><th>Volume per item</th>' +
            '<th>Combo contents</th><th>Status</th><th></th>' +
          '</tr></thead><tbody>' +
          (products.length ? products.map(function (p) {
            const vol = (p.volume || []).map(function (v) { return v.qty + ' \u00D7 ' + v.unit; }).join(', ');
            return '<tr>' +
              '<td><strong>' + U.esc(p.name) + '</strong><div class="small muted mono">' + U.esc(p.id) + '</div></td>' +
              '<td class="small">' + U.esc(p.category || '') + '</td>' +
              '<td class="num"><strong>' + U.money(p.price) + '</strong></td>' +
              '<td class="small dim">' + U.esc(vol || '\u2014') + '</td>' +
              '<td class="small dim" style="max-width:220px">' + U.esc(p.contents || '\u2014') + '</td>' +
              '<td>' + (String(p.status) === 'Active' ? '<span class="badge ok">Active</span>' : '<span class="badge gray">Inactive</span>') + '</td>' +
              '<td class="right nowrap">' +
                '<button class="btn btn-ghost btn-sm" data-edit="' + U.esc(p.id) + '">Edit</button>' +
                '<button class="btn btn-ghost btn-sm" data-del="' + U.esc(p.id) + '">\u2715</button>' +
              '</td></tr>';
          }).join('') : '<tr><td colspan="7" class="tbl-empty">No products yet. Add your menu to get started.</td></tr>') +
          '</tbody></table>' +
        '</div>' +
      '</div>' +
      '<p class="small muted mt-2">Product volume is what the kitchen actually uses up. A bucket of 6 chicken + 4 rice adds ' +
        '<strong>2 Espresso Shots</strong> and <strong>1 Croissant</strong> to the daily volume report, even though it is sold as one combo.</p>';

    const add = document.getElementById('pAdd');
    if (add) add.onclick = function () { Admin.productForm(null); };
    const catBtn = document.getElementById('pCatBtn');
    if (catBtn) catBtn.onclick = Admin.categoriesModal;
    host.querySelectorAll('[data-edit]').forEach(function (b) {
      b.onclick = function () {
        const p = products.find(function (x) { return x.id === b.getAttribute('data-edit'); });
        Admin.productForm(p);
      };
    });
    host.querySelectorAll('[data-del]').forEach(function (b) {
      b.onclick = function () {
        const id = b.getAttribute('data-del');
        const p = products.find(function (x) { return x.id === id; });
        App.confirm('Delete product?', 'Remove <strong>' + U.esc(p.name) + '</strong> from the menu? Past orders keep their record. ' +
          'If you only want to hide it, set the status to Inactive instead.', { danger: true, okText: 'Delete' }).then(function (yes) {
          if (!yes) return;
          DB.delete('products', id).then(function () {
            App.toast('Product deleted', 'ok');
            return App.reload();
          }).then(function () {
            Admin.renderProducts();
            API.manage('products', 'delete', { id: id }, App.state.session.accountId);
            if (navigator.onLine && API.isConfigured()) API.manage('products', 'delete', { id: id }, App.state.session.accountId);
          });
        });
      };
    });
  };

  Admin.productForm = function (existing) {
    const p = existing || { id: '', name: '', category: '', price: '', status: 'Active', volume: [], contents: '', sort: App.state.products.length * 10 + 10, volumeLabel: '' };
    const volume = (p.volume || []).map(function (v) { return { unit: v.unit, qty: v.qty }; });
    const cats = Array.from(new Set(App.state.products.map(function (x) { return x.category; }))).filter(Boolean).sort();

    App.modal({
      title: existing ? 'Edit product' : 'New product',
      size: 'wide',
      body:
        '<div class="grid g-2">' +
          '<div class="field"><label>Product name</label><input class="input" id="fName" value="' + U.esc(p.name) + '" placeholder="e.g. Iced Latte - Large"></div>' +
          '<div class="field"><label>Category</label><input class="input" id="fCat" list="catList" value="' + U.esc(p.category) + '" placeholder="Category name">' +
            '<datalist id="catList">' + cats.map(function (c) { return '<option value="' + U.esc(c) + '">'; }).join('') + '</datalist></div>' +
          '<div class="field"><label>Price (\u20B1)</label><input class="input" id="fPrice" inputmode="decimal" value="' + U.esc(p.price) + '"></div>' +
          '<div class="field"><label>Status</label><select class="select" id="fStatus">' +
            '<option' + (p.status === 'Active' ? ' selected' : '') + '>Active</option>' +
            '<option' + (p.status !== 'Active' ? ' selected' : '') + '>Inactive</option></select></div>' +
        '</div>' +
        '<div class="field"><label>Combo / bucket contents <span class="muted">(shown to staff &amp; kitchen)</span></label>' +
          '<input class="input" id="fContents" value="' + U.esc(p.contents || '') + '" placeholder="e.g. 1 pc chicken + 1 cup rice + 12oz drink"></div>' +
        '<div class="field"><label>Volume label <span class="muted">(short label on the button)</span></label>' +
          '<input class="input" id="fVolLabel" value="' + U.esc(p.volumeLabel || '') + '" placeholder="e.g. 6 chicken, 4 rice"></div>' +
        '<div class="sep"></div>' +
        '<label style="display:block;margin-bottom:8px;font-weight:700">Product volume per item</label>' +
        '<p class="small muted" style="margin:0 0 10px">How many of each unit this product consumes. A combo of 1 chicken + 1 rice adds 1 to each count.</p>' +
        '<div id="volRows"></div>' +
        '<button class="btn btn-ghost btn-sm mt-1" id="volAdd">+ Add volume unit</button>',
      footer: '<button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary" data-save>Save product</button>',
      onMount: function (m, close) {
        function drawVol() {
          const h = m.querySelector('#volRows');
          h.innerHTML = volume.map(function (v, i) {
            return '<div class="row row-nowrap mb-1" data-row="' + i + '">' +
              '<input class="input grow" data-vunit="' + i + '" value="' + U.esc(v.unit) + '" placeholder="Unit e.g. Coffee Bean">' +
              '<input class="input" style="width:100px" data-vqty="' + i + '" inputmode="decimal" value="' + v.qty + '" placeholder="Qty">' +
              '<button class="btn btn-ghost btn-icon" data-vdel="' + i + '">\u2715</button></div>';
          }).join('') || '<p class="small muted">No volume units. Leave empty if this item should not be counted (e.g. condiments).</p>';

          h.querySelectorAll('[data-vunit]').forEach(function (inp) {
            inp.oninput = function () { volume[U.int(inp.getAttribute('data-vunit'))].unit = inp.value; };
          });
          h.querySelectorAll('[data-vqty]').forEach(function (inp) {
            inp.oninput = function () { volume[U.int(inp.getAttribute('data-vqty'))].qty = U.num(inp.value); };
          });
          h.querySelectorAll('[data-vdel]').forEach(function (b) {
            b.onclick = function () { volume.splice(U.int(b.getAttribute('data-vdel')), 1); drawVol(); };
          });
        }
        drawVol();
        m.querySelector('#volAdd').onclick = function () { volume.push({ unit: '', qty: 1 }); drawVol(); };

        m.querySelector('[data-save]').onclick = function () {
          const name = m.querySelector('#fName').value.trim();
          const price = U.num(m.querySelector('#fPrice').value);
          if (!name) { App.toast('Product name is required', 'err'); return; }
          if (price < 0) { App.toast('Price cannot be negative', 'err'); return; }

          const rec = {
            id: existing ? existing.id : 'PRD-' + String(Date.now()).slice(-6),
            name: name,
            category: m.querySelector('#fCat').value.trim() || 'Uncategorized',
            price: U.round2(price),
            status: m.querySelector('#fStatus').value,
            contents: m.querySelector('#fContents').value.trim(),
            volumeLabel: m.querySelector('#fVolLabel').value.trim(),
            volume: volume.filter(function (v) { return v.unit && U.num(v.qty) > 0; }),
            sort: existing ? existing.sort : App.state.products.length * 10 + 10
          };

          m.querySelector('[data-save]').disabled = true;
          DB.put('products', rec).then(function () {
            close();
            App.toast(existing ? 'Product updated' : 'Product added', 'ok', rec.name + ' \u00B7 ' + U.money(rec.price));
            return App.reload();
          }).then(function () {
            Admin.renderProducts();
            if (navigator.onLine && API.isConfigured()) {
              API.manage('products', existing ? 'update' : 'create', rec, App.state.session.accountId).then(function (r) {
                if (r && r.ok) App.toast('Saved to Google Sheets', 'ok');
                else if (r && r.error) App.toast('Saved on this device', 'warn', 'Sheet update failed: ' + r.error);
              });
            } else {
              App.toast('Saved on this device', 'warn', 'It will not appear in Google Sheets until you are online.');
            }
          });
        };
      }
    });
  };

  Admin.categoriesModal = function () {
    const cats = Array.from(new Set(App.state.products.map(function (p) { return p.category; }))).filter(Boolean).sort();
    const counts = {};
    App.state.products.forEach(function (p) { counts[p.category] = (counts[p.category] || 0) + 1; });
    App.modal({
      title: 'Categories',
      size: 'narrow',
      body: '<div class="table-wrap"><table class="tbl"><thead><tr><th>Category</th><th class="num">Items</th></tr></thead><tbody>' +
        (cats.length ? cats.map(function (c) {
          return '<tr><td>' + U.esc(c) + '</td><td class="num">' + counts[c] + '</td></tr>';
        }).join('') : '<tr><td colspan="2" class="tbl-empty">No categories yet</td></tr>') +
        '</tbody></table></div>' +
        '<p class="small muted mt-2" style="margin-bottom:0">Categories come from the products themselves. Type a new category name on any product to create one.</p>'
    });
  };

  /* ------------------------------------------------------------------ */
  /*  ACCOUNTS & ROLES                                                  */
  /* ------------------------------------------------------------------ */
  Admin.renderAccounts = function () {
    const host = document.getElementById('accountsBody');
    if (!host) return;
    if (!App.can('accounts.manage')) {
      host.innerHTML = '<div class="card"><div class="empty-state"><div class="es-ico">\uD83D\uDD12</div>' +
        '<h4>Owner access only</h4><p>Your role cannot open or change accounts.</p></div></div>';
      return;
    }

    const accounts = App.state.accounts;
    const roles = App.state.roles;

    host.innerHTML =
      '<div class="tabs mb-2" id="acctTabs">' +
        '<button class="tab active" data-at="users">Accounts (' + accounts.length + ')</button>' +
        '<button class="tab" data-at="roles">Roles (' + roles.length + ')</button>' +
        '<button class="tab" data-at="branches">Branches (' + App.state.branches.length + ')</button>' +
      '</div>' +
      '<div id="acctPane"></div>';

    host.querySelectorAll('[data-at]').forEach(function (t) {
      t.onclick = function () {
        host.querySelectorAll('[data-at]').forEach(function (x) { x.classList.toggle('active', x === t); });
        Admin.drawAcctPane(t.getAttribute('data-at'));
      };
    });
    Admin.drawAcctPane('users');
  };

  function acctRows() {

    return App.state.accounts.map(function (a) {
          const role = App.state.roles.find(function (r) { return r.id === a.roleId; }) || {};
          return '<tr>' +
            '<td><div class="row row-nowrap" style="gap:9px"><span class="avatar">' + U.initial(a.displayName) + '</span>' +
              '<div><strong>' + U.esc(a.displayName) + '</strong><div class="small muted mono">' + U.esc(a.id) + '</div></div></div></td>' +
            '<td class="mono small">' + U.esc(a.username) + '</td>' +
            '<td class="small"><span class="badge ' + (role.id === 'ROLE-ADMIN' ? 'brand' : 'gray') + '">' + U.esc(role.name || a.roleId) + '</span></td>' +
            '<td class="small">' + U.esc(App.branchCode(a.branchId)) + '</td>' +
            '<td>' + (String(a.status) === 'Active' ? '<span class="badge ok">Active</span>' : '<span class="badge danger">Disabled</span>') + '</td>' +
            '<td class="small dim">' + (a.lastLogin ? U.fmtDateTime(a.lastLogin) : '\u2014') + '</td>' +
            '<td class="right nowrap">' +
              '<button class="btn btn-ghost btn-sm" data-aedit="' + U.esc(a.id) + '">Edit</button>' +
              '<button class="btn btn-ghost btn-sm" data-apw="' + U.esc(a.id) + '">Password</button>' +
            '</td></tr>';
        }).join('');

  }

  Admin.drawAcctPane = function (pane) {
    const host = document.getElementById('acctPane');
    if (!host) return;

    if (pane === 'users') {
      host.innerHTML =
        '<div class="card card-pad-0">' +
          '<div class="card-head" style="padding:14px 16px 0"><h3>User accounts</h3>' +
            '<span class="sub">Passwords are stored as salted SHA-256 hashes \u2014 never as plain text</span>' +
            '<div class="grow"></div><button class="btn btn-primary btn-sm" id="aAdd">+ New account</button></div>' +
          '<div class="table-wrap mt-1"><table class="tbl"><thead><tr>' +
            '<th>Staff</th><th>Username</th><th>Role</th><th>Branch</th><th>Status</th><th>Last login</th><th></th>' +
          '</tr></thead><tbody>' +
          acctRows() +
        '</tbody></table></div>' +
        '<p class="small muted" style="padding:12px 16px;margin:0">Passwords are stored as salted SHA-256 hashes. The owner can reset any password here; staff cannot open this screen.</p>' +
      '</div>';

      const add = document.getElementById('aAdd');
      if (add) add.onclick = function () { Admin.accountForm(null); };
      host.querySelectorAll('[data-aedit]').forEach(function (b) {
        b.onclick = function () {
          const a = App.state.accounts.find(function (x) { return x.id === b.getAttribute('data-aedit'); });
          Admin.accountForm(a);
        };
      });
      host.querySelectorAll('[data-apw]').forEach(function (b) {
        b.onclick = function () { Admin.resetPassword(b.getAttribute('data-apw')); };
      });
      return;
    }

    if (pane === 'roles') {
      const perms = [
        ['pos.use', 'Use POS'], ['kitchen.view', 'See kitchen'], ['kitchen.update', 'Update kitchen status'],
        ['payment.collect', 'Take payment'], ['order.void.own', 'Void own order'], ['order.void.any', 'Void any order'],
        ['order.discount', 'Give discount'], ['reports.view', 'See reports'], ['reports.view.all', 'See all branches'],
        ['products.manage', 'Manage products'], ['accounts.manage', 'Manage accounts'], ['settings.manage', 'Manage settings'],
        ['export.data', 'Export data'], ['nonrev.log', 'Log leftover / meals'], ['sync.manage', 'Manage sync']
      ];
      host.innerHTML =
        '<div class="card card-pad-0">' +
          '<div class="card-head" style="padding:14px 16px 0"><h3>Roles and permissions</h3>' +
            '<span class="sub">Tick what each role may do. Admin always has everything.</span></div>' +
          '<div class="table-wrap mt-1"><table class="tbl"><thead><tr><th>Permission</th>' +
            App.state.roles.map(function (r) { return '<th class="center">' + U.esc(r.name) + '</th>'; }).join('') +
          '</tr></thead><tbody>' +
          perms.map(function (p) {
            return '<tr><td class="small"><strong>' + p[1] + '</strong><div class="muted mono" style="font-size:.68rem">' + p[0] + '</div></td>' +
              App.state.roles.map(function (r) {
                const on = String(r.isAdmin) === 'Yes' || (r.permissions || []).indexOf(p[0]) >= 0;
                return '<td class="center">' + (on ? '<span style="color:var(--ok);font-weight:900">\u2713</span>' : '<span style="color:var(--text-faint)">\u2014</span>') + '</td>';
              }).join('') + '</tr>';
          }).join('') +
          '</tbody></table></div>' +
          '<p class="small muted" style="padding:12px 16px;margin:0">Edit the <span class="mono">Roles</span> sheet in Google Sheets to change a role, then use ' +
            '<strong>Pull from Sheets</strong> in Settings. The app reads permissions from there.</p>' +
        '</div>';
      return;
    }

    /* Branches */
    host.innerHTML =
      '<div class="card card-pad-0">' +
        '<div class="card-head" style="padding:14px 16px 0"><h3>Branches</h3>' +
          '<span class="sub">Each branch keeps its own queue cards and sales</span></div>' +
        '<div class="table-wrap mt-1"><table class="tbl"><thead><tr>' +
          '<th>Branch</th><th>Branch ID</th><th>Address</th><th class="num">Queue range</th><th>Status</th><th></th>' +
        '</tr></thead><tbody>' +
        App.state.branches.map(function (b) {
          return '<tr><td><strong>' + U.esc(b.name) + '</strong></td>' +
            '<td class="mono small">' + U.esc(b.id) + '</td>' +
            '<td class="small dim">' + U.esc(b.address || '') + '</td>' +
            '<td class="num small">' + (b.queueMin || 1) + ' \u2013 ' + (b.queueMax || 18) + '</td>' +
            '<td>' + (String(b.status) === 'Active' ? '<span class="badge ok">Active</span>' : '<span class="badge gray">Closed</span>') + '</td>' +
            '<td class="right"><button class="btn btn-ghost btn-sm" data-bedit="' + U.esc(b.id) + '">Edit</button></td></tr>';
        }).join('') +
        '</tbody></table></div>' +
        '<div style="padding:14px 16px"><button class="btn btn-primary btn-sm" id="bAdd">+ Add branch</button></div>' +
      '</div>';

    const addB = document.getElementById('bAdd');
    if (addB) addB.onclick = function () { Admin.branchForm(null); };
    host.querySelectorAll('[data-bedit]').forEach(function (btn) {
      btn.onclick = function () {
        const b = App.state.branches.find(function (x) { return x.id === btn.getAttribute('data-bedit'); });
        Admin.branchForm(b);
      };
    });
  };

  Admin.accountForm = function (existing) {
    const a = existing || { id: '', username: '', displayName: '', roleId: 'ROLE-CASHIER', branchId: App.state.branches[0] ? App.state.branches[0].id : '', status: 'Active' };
    App.modal({
      title: existing ? 'Edit account' : 'New account',
      body:
        '<div class="field"><label>Full name</label><input class="input" id="uName" value="' + U.esc(a.displayName) + '" placeholder="e.g. Nica - Main Cashier"></div>' +
        '<div class="field"><label>Username</label><input class="input" id="uUser" value="' + U.esc(a.username) + '" placeholder="e.g. cashier1" autocapitalize="none"></div>' +
        '<div class="grid g-2"><div class="field"><label>Role</label><select class="select" id="uRole">' +
          App.state.roles.map(function (r) { return '<option value="' + U.esc(r.id) + '"' + (a.roleId === r.id ? ' selected' : '') + '>' + U.esc(r.name) + '</option>'; }).join('') +
        '</select></div>' +
        '<div class="field"><label>Branch</label><select class="select" id="uBranch">' +
          App.state.branches.map(function (b) { return '<option value="' + U.esc(b.id) + '"' + (a.branchId === b.id ? ' selected' : '') + '>' + U.esc(b.name) + '</option>'; }).join('') +
        '</select></div></div>' +
        '<div class="field"><label>Status</label><select class="select" id="uStatus">' +
          '<option' + (a.status === 'Active' ? ' selected' : '') + '>Active</option>' +
          '<option' + (a.status !== 'Active' ? ' selected' : '') + '>Disabled</option></select></div>' +
        (existing ? '' : '<div class="field"><label>Temporary password</label><input class="input" id="uPw" type="password" placeholder="At least 6 characters"></div>') +
        '<div class="field" style="margin-bottom:0"><label>Quick PIN (optional, 4 digits)</label><input class="input" id="uPin" inputmode="numeric" maxlength="4" placeholder="' + (existing ? 'Leave blank to keep current' : 'e.g. 1234') + '"></div>' +
        (existing ? '' : '<p class="small muted mt-1">Give this password to the staff member. They can change it later from Settings.</p>'),
      footer: '<button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary" data-save>Save account</button>',
      onMount: function (m, close) {
        m.querySelector('[data-save]').onclick = function () {
          const displayName = m.querySelector('#uName').value.trim();
          const username = m.querySelector('#uUser').value.trim().toLowerCase().replace(/\s+/g, '');
          if (!displayName || !username) { App.toast('Name and username are required', 'err'); return; }
          const dup = App.state.accounts.find(function (x) { return x.username === username && x.id !== a.id; });
          if (dup) { App.toast('That username is already used', 'err'); return; }

          const pw = m.querySelector('#uPw');
          const pin = m.querySelector('#uPin').value.trim();

          const rec = {
            id: existing ? existing.id : 'ACC-' + String(Date.now()).slice(-6),
            username: username, displayName: displayName,
            roleId: m.querySelector('#uRole').value,
            branchId: m.querySelector('#uBranch').value,
            status: m.querySelector('#uStatus').value,
            passwordSalt: existing ? existing.passwordSalt : U.uuid().replace(/-/g, '').slice(0, 12),
            passwordHash: existing ? existing.passwordHash : '',
            pinHash: existing ? existing.pinHash : '',
            createdAt: existing ? existing.createdAt : U.nowISO(),
            lastLogin: existing ? existing.lastLogin : '',
            forceChange: 'No'
          };

          if (!existing && (!pw || pw.value.length < 6)) { App.toast('Password must be at least 6 characters', 'err'); return; }

          const afterHash = function () {
            DB.put('accounts', rec).then(function () {
              close();
              App.toast(existing ? 'Account updated' : 'Account created', 'ok', displayName);
              return App.reload();
            }).then(function () {
              Admin.renderAccounts();
              // Push to the sheet. For new accounts the plain password is sent once,
              // the server hashes it and stores only the hash.
              const payload = Object.assign({}, rec);
              if (!existing && pw) payload.plainPassword = pw.value;
              if (pin) payload.plainPin = pin;
              if (navigator.onLine && API.isConfigured()) {
                API.manage('accounts', existing ? 'update' : 'create', payload, App.state.session.accountId).then(function (r) {
                  if (r && r.ok) App.toast('Account saved to Google Sheets', 'ok');
                  else if (r && r.error) App.toast('Saved on this device only', 'warn', 'Sheet update failed: ' + r.error);
                });
              } else {
                App.toast('Saved on this device', 'warn', 'Connect to Google Sheets to share this account.');
              }
            });
          };

          const hashTargets = [];
          if (!existing && pw) {
            hashTargets.push(API.LocalAuth.sha256Hex(rec.passwordSalt + '|' + pw.value).then(function (hex) { rec.passwordHash = 'sha256$' + hex; }));
          }
          if (pin) {
            hashTargets.push(API.LocalAuth.sha256Hex(rec.passwordSalt + '|' + (pin.length === 4 ? pin : pin)).then(function (hex) { rec.pinHash = 'sha256$' + hex; }));
          }
          Promise.all(hashTargets).then(afterHash);
        };
      }
    });
  };

  Admin.resetPassword = function (accountId) {
    const a = App.state.accounts.find(function (x) { return x.id === accountId; });
    if (!a) return;
    App.modal({
      title: 'Reset password \u00B7 ' + a.displayName,
      size: 'narrow',
      body: '<div class="field"><label>New password</label><input class="input" id="rp1" type="password" placeholder="At least 6 characters"></div>' +
        '<div class="field"><label>Repeat password</label><input class="input" id="rp2" type="password"></div>' +
        '<p class="small muted" style="margin:0">Only a salted hash is stored here and in the Google Sheet. Nobody can read the password back.</p>',
      footer: '<button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary" data-save>Set password</button>',
      onMount: function (m, close) {
        m.querySelector('[data-save]').onclick = function () {
          const p1 = m.querySelector('#rp1').value, p2 = m.querySelector('#rp2').value;
          if (p1.length < 6) { App.toast('Use at least 6 characters', 'err'); return; }
          if (p1 !== p2) { App.toast('The two passwords do not match', 'err'); return; }
          const salt = U.uuid().replace(/-/g, '').slice(0, 12);
          API.LocalAuth.sha256Hex(salt + '|' + p1).then(function (hex) {
            a.passwordSalt = salt;
            a.passwordHash = 'sha256$' + hex;
            a.forceChange = 'Yes';
            return DB.put('accounts', a);
          }).then(function () {
            close();
            App.toast('Password changed', 'ok', 'Staff will use the new password on their next sign-in.');
            App.reload().then(Admin.renderAccounts);
            if (navigator.onLine && API.isConfigured()) {
              API.manage('accounts', 'update', Object.assign({}, a, { plainPassword: p1 }), App.state.session.accountId);
            }
          });
        };
      }
    });
  };

  Admin.branchForm = function (existing) {
    const b = existing || { id: '', name: '', address: '', contact: '', status: 'Active', queueMin: 1, queueMax: 18 };
    App.modal({
      title: existing ? 'Edit branch' : 'New branch',
      body:
        '<div class="field"><label>Branch name</label><input class="input" id="bName" value="' + U.esc(b.name) + '" placeholder="Branch name"></div>' +
        '<div class="field"><label>Branch ID</label><input class="input" id="bId" value="' + U.esc(b.id) + '" placeholder="BR-04" ' + (existing ? 'readonly' : '') + '></div>' +
        '<div class="field"><label>Address</label><input class="input" id="bAddr" value="' + U.esc(b.address || '') + '"></div>' +
        '<div class="field"><label>Contact</label><input class="input" id="bContact" value="' + U.esc(b.contact || '') + '"></div>' +
        '<div class="grid g-2"><div class="field"><label>Queue from</label><input class="input" id="bQMin" inputmode="numeric" value="' + (b.queueMin || 1) + '"></div>' +
        '<div class="field"><label>Queue to</label><input class="input" id="bQMax" inputmode="numeric" value="' + (b.queueMax || 18) + '"></div></div>' +
        '<div class="field" style="margin-bottom:0"><label>Status</label><select class="select" id="bStatus">' +
          '<option' + (b.status === 'Active' ? ' selected' : '') + '>Active</option>' +
          '<option' + (b.status !== 'Active' ? ' selected' : '') + '>Closed</option></select></div>',
      footer: '<button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary" data-save>Save branch</button>',
      onMount: function (m, close) {
        m.querySelector('[data-save]').onclick = function () {
          const name = m.querySelector('#bName').value.trim();
          const id = m.querySelector('#bId').value.trim().toUpperCase();
          if (!name || !id) { App.toast('Name and Branch ID are required', 'err'); return; }
          const rec = {
            id: id, name: name, address: m.querySelector('#bAddr').value.trim(),
            contact: m.querySelector('#bContact').value.trim(),
            queueMin: U.int(m.querySelector('#bQMin').value) || 1,
            queueMax: U.int(m.querySelector('#bQMax').value) || 18,
            status: m.querySelector('#bStatus').value
          };
          DB.put('branches', rec).then(function () {
            close();
            App.toast(existing ? 'Branch updated' : 'Branch added', 'ok', name);
            return App.reload();
          }).then(function () {
            Admin.renderAccounts();
            if (navigator.onLine && API.isConfigured()) {
              API.manage('branches', existing ? 'update' : 'create', rec, App.state.session.accountId);
            }
          });
        };
      }
    });
  };

  global.Admin = Admin;
})(window);
