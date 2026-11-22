/* =====================================================================
   {{KIT_TITLE}} - Settings page
   Server connection, business info, queue range, receipts, this device.
   ===================================================================== */
(function (global) {
  'use strict';

  const SettingsPage = {};

  SettingsPage.render = function () {
    const host = document.getElementById('settingsBody');
    if (!host) return;

    const s = App.state.session;
    const isAdmin = App.can('settings.manage');

    host.innerHTML =
      '<div class="grid g-2">' +

        /* Connection */
        '<div class="card">' +
          '<div class="card-head"><h3>Google Sheets connection</h3></div>' +
          '<p class="small muted" style="margin-top:0">Paste the Apps Script Web App URL and the access token you set in <span class="mono">Code.gs</span>. ' +
            'Every device at every branch uses the same two values.</p>' +
          (API.isConfigured()
            ? '<div class="small mb-2"><span class="badge ok">Configured</span> <span class="mono muted" style="word-break:break-all">' + U.esc(API.getUrl()) + '</span></div>'
            : '<div class="small mb-2"><span class="badge warn">Not configured</span> Running in local mode — records stay on this device.</div>') +
          '<button class="btn btn-primary" id="stConn">' + (API.isConfigured() ? 'Change connection' : 'Set up connection') + '</button>' +
          '<button class="btn btn-ghost" id="stTest" style="margin-left:8px">Test connection</button>' +
          '<button class="btn btn-ghost" id="stPull" style="margin-left:8px">Pull menu &amp; settings</button>' +
        '</div>' +

        /* Business */
        '<div class="card">' +
          '<div class="card-head"><h3>Business</h3></div>' +
          '<div class="field"><label>Business name</label><input class="input" id="stBiz" value="' + U.esc(App.setting('BusinessName', "{{BUSINESS_NAME}}")) + '" ' + (isAdmin ? '' : 'disabled') + '></div>' +
          '<div class="field"><label>Receipt footer</label><input class="input" id="stFooter" value="' + U.esc(App.setting('ReceiptFooter', 'Salamat! Balik-balik po.')) + '" ' + (isAdmin ? '' : 'disabled') + '></div>' +
          '<div class="grid g-2"><div class="field"><label>Queue from</label><input class="input" id="stQMin" inputmode="numeric" value="' + App.settingNum('QueueMin', 1) + '" ' + (isAdmin ? '' : 'disabled') + '></div>' +
          '<div class="field"><label>Queue to</label><input class="input" id="stQMax" inputmode="numeric" value="' + App.settingNum('QueueMax', 18) + '" ' + (isAdmin ? '' : 'disabled') + '></div></div>' +
          (isAdmin ? '<button class="btn btn-primary" id="stSaveBiz">Save business settings</button>'
            : '<p class="small muted" style="margin:0">Only an owner or admin can change these.</p>') +
        '</div>' +

        /* Behaviour */
        '<div class="card">' +
          '<div class="card-head"><h3>App behaviour</h3></div>' +
          SettingsPage.switchRow('AutoSync', 'Send records automatically', 'Push pending records to Google Sheets whenever the connection is back.') +
          SettingsPage.switchRow('FeatureKitchen', 'Kitchen queue & display', 'Turn off for a shop with no back-of-house queue.') +
          SettingsPage.switchRow('FeatureQueueCards', 'Queue cards', 'Numbered order cards that return to the pool when served.') +
          SettingsPage.switchRow('FeatureDiscounts', 'Discounts', 'Allow percent or amount discounts at checkout.') +
          SettingsPage.switchRow('FeatureMultiBranch', 'Multi-branch selector', 'Off for a single-location shop.') +
          SettingsPage.switchRow('AllowUnpaidOrders', 'Allow Unpaid / Order Only', 'The order is prepared first; payment can be taken later.') +
          SettingsPage.switchRow('RequireQueueNumber', 'Suggest a queue card on checkout', 'Picks the lowest free card automatically.') +
          SettingsPage.switchRow('AutoReleaseQueue', 'Release queue number when served or voided', 'The card goes back to the available list.') +
          SettingsPage.switchRow('AutoPrintReceipt', 'Print receipt automatically after saving', 'Uses the device print dialog (or a thermal printer app on Android).') +
          '<div class="set-row"><div class="sr-lbl"><strong>Screen size</strong><span>Compact for many products, large for fat fingers on a tablet.</span></div>' +
            '<select class="select" id="stDensity" style="width:150px">' +
              ['comfortable', 'compact', 'large'].map(function (d) {
                return '<option' + (App.setting('Density', 'comfortable') === d ? ' selected' : '') + '>' + d + '</option>';
              }).join('') + '</select></div>' +
          '<div class="set-row"><div class="sr-lbl"><strong>Void window (minutes)</strong><span>How long staff may void their own order.</span></div>' +
            '<input class="input" id="stVoidWin" inputmode="numeric" style="width:100px;text-align:right" value="' + App.settingNum('VoidWindowMinutes', 15) + '"></div>' +
          '<div class="set-row" style="border-bottom:none"><div class="sr-lbl"><strong>Kitchen warning (minutes)</strong><span>Ticket turns red after this long.</span></div>' +
            '<input class="input" id="stKitchenWarn" inputmode="numeric" style="width:100px;text-align:right" value="' + App.settingNum('KitchenWarnMinutes', 8) + '"></div>' +
        '</div>' +

        /* This device */
        '<div class="card">' +
          '<div class="card-head"><h3>This device</h3></div>' +
          '<div class="set-row"><div class="sr-lbl"><strong>Device label</strong><span>Shown in the Sync Log so the owner knows where a record came from.</span></div>' +
            '<input class="input" id="stDevice" style="width:150px" value="' + U.esc(API.deviceLabel()) + '"></div>' +
          '<div class="set-row"><div class="sr-lbl"><strong>Signed in as</strong><span>' + U.esc(s.displayName) + ' \u00B7 ' + U.esc(s.roleName) + '</span></div>' +
            '<span class="badge brand">' + U.esc(s.branchId) + '</span></div>' +
          '<div class="set-row"><div class="sr-lbl"><strong>Local database</strong><span>' +
            (DB.mode === 'indexeddb' ? 'IndexedDB \u2014 full offline storage'
              : U.ls.persistent ? 'localStorage fallback \u2014 limited space'
                : 'Preview mode \u2014 memory only, nothing is kept after closing') + '</span></div>' +
            '<span class="badge ' + (DB.mode === 'indexeddb' && U.ls.persistent ? 'ok' : 'warn') + '">' +
              (U.ls.persistent ? DB.mode : 'preview') + '</span></div>' +
          '<div class="set-row" style="border-bottom:none"><div class="sr-lbl"><strong>App version</strong><span>{{KIT_TITLE}}</span></div>' +
            '<span class="badge gray">v' + APP_VERSION + '</span></div>' +
          '<div class="row mt-2">' +
            '<button class="btn btn-ghost" id="stChangePw">Change my password</button>' +
            '<button class="btn btn-ghost" id="stBackup">Back up device</button>' +
            '<button class="btn btn-ghost" id="stSignOut">Sign out</button>' +
          '</div>' +
        '</div>' +
      '</div>' +

      '<div class="card mt-2">' +
        '<div class="card-head"><h3>About</h3></div>' +
        '<p class="small muted" style="margin:0;line-height:1.7">{{KIT_TITLE}} \u2014 simple POS, kitchen queue, and multi-branch sales tracker.<br>' +
        'Built by {{AUTHOR}}. Offline-first: every record is stored on the device before it is sent to Google Sheets.<br>' +
        'Google Sheets and Apps Script are used under their free tiers, which have daily limits. Test the volume of orders during the trial.</p>' +
      '</div>';

    SettingsPage.wire(isAdmin);
  };

  SettingsPage.switchRow = function (key, label, hint) {
    const on = App.settingBool(key, true);
    return '<div class="set-row"><div class="sr-lbl"><strong>' + label + '</strong><span>' + hint + '</span></div>' +
      '<button class="switch' + (on ? ' on' : '') + '" data-sw="' + key + '" role="switch" aria-checked="' + on + '"></button></div>';
  };

  SettingsPage.wire = function (isAdmin) {
    App.els('[data-sw]').forEach(function (sw) {
      sw.onclick = function () {
        const key = sw.getAttribute('data-sw');
        const next = !sw.classList.contains('on');
        sw.classList.toggle('on', next);
        DB.setSetting(key, next ? 'true' : 'false').then(function () {
          return App.reload();
        }).then(function () {
          if (key === 'AutoSync') App.Sync.restart();
          App.toast('Saved', 'ok', key + ' is now ' + (next ? 'on' : 'off'));
        });
      };
    });

    const conn = document.getElementById('stConn');
    if (conn) conn.onclick = SettingsPage.connectionModal;

    const test = document.getElementById('stTest');
    if (test) test.onclick = function () {
      test.disabled = true; test.textContent = 'Testing…';
      API.ping().then(function (r) {
        test.disabled = false; test.textContent = 'Test connection';
        if (r.ok) App.toast('Connected to Google Sheets', 'ok', (r.data && r.data.sheet) || 'Server is responding.');
        else App.toast('Connection failed', 'err', r.error || 'Check the URL, token, and deployment access.');
      });
    };

    const pull = document.getElementById('stPull');
    if (pull) pull.onclick = function () { App.pullReference({}); };

    const saveBiz = document.getElementById('stSaveBiz');
    if (saveBiz) saveBiz.onclick = function () {
      const settings = {
        BusinessName: document.getElementById('stBiz').value.trim() || "{{BUSINESS_NAME}}",
        ReceiptFooter: document.getElementById('stFooter').value.trim(),
        QueueMin: String(U.int(document.getElementById('stQMin').value) || 1),
        QueueMax: String(U.int(document.getElementById('stQMax').value) || 18)
      };
      SettingsPage.saveSettings(settings);
    };

    const dens = document.getElementById('stDensity');
    if (dens) dens.onchange = function () { SettingsPage.saveSettings({ Density: dens.value }); };
    const vw = document.getElementById('stVoidWin');
    if (vw) vw.onchange = function () { SettingsPage.saveSettings({ VoidWindowMinutes: String(U.int(vw.value) || 15) }); };
    const kw = document.getElementById('stKitchenWarn');
    if (kw) kw.onchange = function () { SettingsPage.saveSettings({ KitchenWarnMinutes: String(U.int(kw.value) || 8) }); };

    const dev = document.getElementById('stDevice');
    if (dev) dev.onchange = function () {
      API.setDeviceLabel(dev.value.trim() || API.deviceLabel());
      App.toast('Device label saved', 'ok');
    };

    const cpw = document.getElementById('stChangePw');
    if (cpw) cpw.onclick = SettingsPage.changeMyPassword;

    const bk = document.getElementById('stBackup');
    if (bk) bk.onclick = SyncPage.backup;

    const so = document.getElementById('stSignOut');
    if (so) so.onclick = function () { App.logout(); };

    void isAdmin;
  };

  SettingsPage.saveSettings = function (settings) {
    const sets = Object.keys(settings).map(function (k) { return { key: k, value: settings[k] }; });
    DB.bulkPut('settings', sets).then(function () {
      return App.reload();
    }).then(function () {
      App.toast('Settings saved', 'ok');
      SettingsPage.render();
      if (navigator.onLine && API.isConfigured()) {
        API.saveSettings(settings, App.state.session.accountId);
      }
    });
  };

  SettingsPage.connectionModal = function () {
    App.modal({
      title: 'Google Sheets connection',
      body:
        '<p class="small muted" style="margin-top:0;line-height:1.6">Open <span class="mono">docs/02-google-sheets-setup.md</span> in the project folder for the full walkthrough with pictures of each step. ' +
          'Short version: create the Sheet from the included template, open <strong>Extensions \u2192 Apps Script</strong>, paste the provided <span class="mono">Code.gs</span>, ' +
          'set your token, then <strong>Deploy \u2192 New deployment \u2192 Web app</strong> with access <strong>Anyone</strong>.</p>' +
        '<div class="field"><label>Web App URL (ends with /exec)</label>' +
          '<input class="input mono" id="cUrl" style="font-size:.78rem" placeholder="https://script.google.com/macros/s/AKfy…/exec" value="' + U.esc(API.getUrl()) + '"></div>' +
        '<div class="field"><label>Access token</label>' +
          '<input class="input mono" id="cToken" placeholder="The same token you set inside Code.gs" value="' + U.esc(API.getToken()) + '"></div>' +
        '<div class="card" style="background:var(--bg-2);border-color:var(--line)">' +
          '<p class="small muted" style="margin:0;line-height:1.65">The token is a shared password between the app and your spreadsheet. It stops strangers who find the URL from reading or writing your sales. ' +
          'Keep it out of screenshots and group chats.</p>' +
        '</div>',
      footer: '<button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary" data-save>Save &amp; test</button>',
      onMount: function (m, close) {
        m.querySelector('[data-save]').onclick = function () {
          const url = m.querySelector('#cUrl').value.trim();
          const token = m.querySelector('#cToken').value.trim();
          if (url && !/^https:\/\/script\.google\.com\/macros\/s\/.+\/exec/.test(url)) {
            App.toast('That does not look like a Web App URL', 'err', 'It should start with https://script.google.com/macros/s/ and end with /exec');
            return;
          }
          API.setConfig(url, token);
          close();
          if (!url) { App.toast('Connection cleared', 'warn', 'The app is now in local mode.'); SettingsPage.render(); return; }
          App.toast('Saving…', 'ok');
          API.ping().then(function (r) {
            if (r.ok) {
              App.toast('Connected!', 'ok', 'Pulling the menu and settings from your Sheet…');
              App.pullReference({});
            } else {
              App.toast('Saved, but the test failed', 'err', r.error || 'Check that the deployment access is "Anyone".');
            }
            SettingsPage.render();
          });
        };
      }
    });
  };

  SettingsPage.changeMyPassword = function () {
    App.modal({
      title: 'Change my password',
      size: 'narrow',
      body: '<div class="field"><label>Current password</label><input class="input" id="cp0" type="password"></div>' +
        '<div class="field"><label>New password</label><input class="input" id="cp1" type="password" placeholder="At least 6 characters"></div>' +
        '<div class="field" style="margin-bottom:0"><label>Repeat new password</label><input class="input" id="cp2" type="password"></div>',
      footer: '<button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary" data-save>Change password</button>',
      onMount: function (m, close) {
        m.querySelector('[data-save]').onclick = function () {
          const cur = m.querySelector('#cp0').value;
          const p1 = m.querySelector('#cp1').value;
          const p2 = m.querySelector('#cp2').value;
          if (p1.length < 6) { App.toast('Use at least 6 characters', 'err'); return; }
          if (p1 !== p2) { App.toast('New passwords do not match', 'err'); return; }

          API.LocalAuth.verifyUser(App.state.session, cur).then(function (ok) {
            if (!ok) { App.toast('Current password is wrong', 'err'); return null; }
            return DB.get('accounts', App.state.session.accountId).then(function (acc) {
              const salt = U.uuid().replace(/-/g, '').slice(0, 12);
              return API.LocalAuth.sha256Hex(salt + '|' + p1).then(function (hex) {
                acc.passwordSalt = salt;
                acc.passwordHash = 'sha256$' + hex;
                return DB.put('accounts', acc);
              });
            }).then(function () {
              close();
              App.toast('Password changed', 'ok', 'Use it the next time you sign in.');
              if (navigator.onLine && API.isConfigured()) {
                API.changePassword(App.state.session.accountId, cur, p1, App.state.session.accountId);
              }
              return true;
            });
          });
        };
      }
    });
  };

  global.SettingsPage = SettingsPage;
})(window);
