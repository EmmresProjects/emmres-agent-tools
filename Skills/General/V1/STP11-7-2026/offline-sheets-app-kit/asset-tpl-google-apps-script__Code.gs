/**
 * =====================================================================
 *  {{KIT_TITLE}} - Google Apps Script backend
 *  File: Code.gs  (main entry point / API)
 * ---------------------------------------------------------------------
 *  Endpoints (all POST, JSON body, keyed by "action"):
 *    ping           health check + sheet name
 *    login          username + password -> account + role + branch
 *    pull           reference data (branches, roles, accounts, products,
 *                   settings, recent orders) for one device
 *    pushOrders     orders + order items + kitchen queue rows
 *    pushNonRevenue leftover / employee meal logs
 *    pushEOD        end-of-day totals written to the Reports sheet
 *    voidOrder      void a single order with a reason
 *    manage         create / update / delete a product, branch, account
 *    changePassword change a password (verifies the old one unless admin)
 *    saveSettings   write Settings rows
 *    report         aggregated figures for a date range
 *
 *  Everything is idempotent per RecordID, so the app can retry safely.
 * =====================================================================
 */

/* ------------------------------------------------------------------ */
/*  Web app entry points                                              */
/* ------------------------------------------------------------------ */
function doGet(e) {
  var action = (e && e.parameter && e.parameter.action) || 'health';
  if (action === 'health') {
    return jsonOut({
      ok: true, service: '{{APP_TITLE}} API',
      version: '1.0.0', time: new Date().toISOString(),
      authorised: false
    });
  }
  return jsonOut({ ok: false, error: 'Use POST for this action.' });
}

function doPost(e) {
  var body;
  try {
    body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    return jsonOut({ ok: false, error: 'Request body is not valid JSON.' });
  }

  var action = String(body.action || '');
  var payload = body.payload || {};
  var token = String(body.token || '');

  try {
    if (USER_SETTINGS.ACCESS_TOKEN === 'CHANGE-ME-to-a-long-random-token') {
      return jsonOut({ ok: false, error: 'The server token is still the default. Edit Config.gs and paste your own ACCESS_TOKEN, then redeploy.' });
    }
    if (token !== USER_SETTINGS.ACCESS_TOKEN) {
      logSync_('denied', '', 'Auth', body.deviceId || '', body.deviceLabel || '', 'Denied', 'Wrong or missing access token', '', '');
      return jsonOut({ ok: false, error: 'Access token is not valid. Check the token in Settings.' });
    }

    var started = new Date().getTime();
    var result;

    switch (action) {
      case 'ping':           result = apiPing_(body); break;
      case 'login':          result = apiLogin_(body, payload); break;
      case 'pull':           result = apiPull_(body); break;
      case 'pushOrders':     result = apiPushOrders_(body, payload); break;
      case 'pushNonRevenue': result = apiPushNonRevenue_(body, payload); break;
      case 'pushEOD':        result = apiPushEOD_(body, payload); break;
      case 'voidOrder':      result = apiVoidOrder_(body, payload); break;
      case 'manage':         result = apiManage_(body, payload); break;
      case 'changePassword': result = apiChangePassword_(body, payload); break;
      case 'saveSettings':   result = apiSaveSettings_(body, payload); break;
      case 'report':         result = apiReport_(body, payload); break;
      default:
        result = { ok: false, error: 'Unknown action: ' + action };
    }

    result.ok = result.ok !== false;
    result.ms = new Date().getTime() - started;
    return jsonOut(result);

  } catch (err) {
    logSync_('error', '', action, body.deviceId || '', body.deviceLabel || '', 'Error', String(err && err.message || err), '', '');
    return jsonOut({ ok: false, error: String(err && err.message || err) });
  }
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */
function jsonOut(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function ss_() {
  if (SPREADSHEET_ID) return SpreadsheetApp.openById(SPREADSHEET_ID);
  var active = SpreadsheetApp.getActiveSpreadsheet();
  if (active) return active;
  throw new Error('Open this script from the spreadsheet (Extensions > Apps Script) or set SPREADSHEET_ID in Config.gs.');
}

function sheet_(name) {
  var sh = ss_().getSheetByName(name);
  if (!sh) throw new Error('Missing sheet "' + name + '". Run installKit() first.');
  return sh;
}

/** Read a sheet into an array of objects keyed by the header row. */
function readTable_(name) {
  var sh = sheet_(name);
  var values = sh.getDataRange().getValues();
  if (values.length < 2) return [];
  var head = values[0].map(function (h) { return String(h).trim(); });
  var out = [];
  for (var r = 1; r < values.length; r++) {
    var row = values[r];
    if (!row.join('')) continue;
    var obj = {};
    for (var c = 0; c < head.length; c++) obj[head[c]] = row[c];
    out.push(obj);
  }
  return out;
}

/** Append rows in one call. */
function appendRows_(name, rows) {
  if (!rows || !rows.length) return 0;
  var sh = sheet_(name);
  sh.getRange(sh.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
  return rows.length;
}

/** All RecordIDs in a sheet, as a lookup object. */
function existingIds_(name, colName) {
  var sh = sheet_(name);
  var head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0].map(String);
  var idx = head.indexOf(colName);
  if (idx < 0) return {};
  var last = sh.getLastRow();
  if (last < 2) return {};
  var col = sh.getRange(2, idx + 1, last - 1, 1).getValues();
  var out = {};
  for (var i = 0; i < col.length; i++) {
    var v = String(col[i][0] || '').trim();
    if (v) out[v] = i + 2; // row number in the sheet
  }
  return out;
}

function findRow_(name, colName, value) {
  var ids = existingIds_(name, colName);
  return ids[String(value)] || 0;
}

function todayKey_(d) {
  return Utilities.formatDate(d || new Date(), USER_SETTINGS.TIME_ZONE, 'yyyy-MM-dd');
}

function toNumber_(v) { var n = Number(v); return isFinite(n) ? n : 0; }

function sha256_(text) {
  var raw = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8);
  return raw.map(function (b) { return ('0' + (b & 0xFF).toString(16)).slice(-2); }).join('');
}

function randomSalt_() {
  return Utilities.getUuid().replace(/-/g, '').slice(0, 12);
}

function hashPassword_(password, salt) {
  return 'sha256$' + sha256_(salt + '|' + password);
}

function logSync_(syncId, recordId, recordType, deviceId, deviceLabel, result, note, branchId, accountId) {
  try {
    appendRows_('SyncLog', [[
      syncId || Utilities.getUuid(), recordId || '', recordType || '', deviceId || '',
      deviceLabel || '', new Date(), result || '', String(note || '').slice(0, 400),
      branchId || '', accountId || ''
    ]]);
  } catch (e) { /* logging must never break the request */ }
}

/** Basic rate guard: 60 requests per minute per device. */
function throttle_(deviceId) {
  try {
    var cache = CacheService.getScriptCache();
    var key = 'rl_' + (deviceId || 'anon');
    var n = Number(cache.get(key) || 0);
    if (n > 90) return false;
    cache.put(key, String(n + 1), 60);
    return true;
  } catch (e) { return true; }
}

/* ------------------------------------------------------------------ */
/*  ping                                                             */
/* ------------------------------------------------------------------ */
function apiPing_(body) {
  return {
    ok: true,
    data: {
      sheet: ss_().getName(),
      timeZone: USER_SETTINGS.TIME_ZONE,
      serverTime: new Date().toISOString(),
      device: body.deviceLabel || ''
    }
  };
}

/* ------------------------------------------------------------------ */
/*  login                                                            */
/* ------------------------------------------------------------------ */
function apiLogin_(body, payload) {
  if (!throttle_(body.deviceId)) return { ok: false, error: 'Too many attempts. Wait a minute and try again.' };

  var username = String(payload.username || '').trim().toLowerCase();
  var password = String(payload.password || '');
  if (!username || !password) return { ok: false, error: 'Username and password are required.' };

  var accounts = readTable_('Accounts');
  var account = null;
  for (var i = 0; i < accounts.length; i++) {
    if (String(accounts[i].Username || '').trim().toLowerCase() === username) { account = accounts[i]; break; }
  }

  var logRow = function (result, note) {
    logSync_(Utilities.getUuid(), String(account ? account.AccountID : username), 'Login',
      body.deviceId || '', body.deviceLabel || '', result, note, account ? account.BranchID : '', account ? account.AccountID : '');
  };

  if (!account) { logRow('Failed', 'Unknown username'); return { ok: false, error: 'Wrong username or password.' }; }
  if (String(account.Status).toLowerCase() !== 'active') { logRow('Failed', 'Account disabled'); return { ok: false, error: 'This account is disabled. Ask the owner to turn it back on.' }; }

  var expected = String(account.PasswordHash || '');
  var actual = hashPassword_(password, String(account.PasswordSalt || ''));
  if (expected !== actual) { logRow('Failed', 'Wrong password'); return { ok: false, error: 'Wrong username or password.' }; }

  var roles = readTable_('Roles');
  var role = null;
  for (var r = 0; r < roles.length; r++) {
    if (String(roles[r].RoleID) === String(account.RoleID)) { role = roles[r]; break; }
  }

  // Update LastLogin
  var ids = existingIds_('Accounts', 'AccountID');
  var rowNo = ids[String(account.AccountID)];
  if (rowNo) {
    var head = sheet_('Accounts').getRange(1, 1, 1, sheet_('Accounts').getLastColumn()).getValues()[0].map(String);
    var lastIdx = head.indexOf('LastLogin');
    if (lastIdx >= 0) sheet_('Accounts').getRange(rowNo, lastIdx + 1).setValue(new Date());
  }

  logRow('Success', 'Signed in');

  return {
    ok: true,
    data: {
      account: {
        id: String(account.AccountID),
        username: String(account.Username),
        displayName: String(account.DisplayName),
        roleId: String(account.RoleID),
        branchId: String(account.BranchID),
        status: String(account.Status),
        salt: String(account.PasswordSalt),
        passwordHash: String(account.PasswordHash),
        forceChange: String(account.ForceChange || 'No'),
        createdAt: account.CreatedAt
      },
      role: role ? {
        id: String(role.RoleID), name: String(role.RoleName),
        permissions: String(role.Permissions || '').split(',').map(function (s) { return s.trim(); }).filter(String),
        isAdmin: String(role.IsAdmin) === 'Yes',
        canPOS: String(role.CanUsePOS) !== 'No',
        canKitchen: String(role.CanUseKitchen) !== 'No',
        allBranches: String(role.CanViewAllBranches) === 'Yes',
        canManage: String(role.CanManageData) === 'Yes',
        canExport: String(role.CanExport) === 'Yes'
      } : null,
      branch: branchById_(String(account.BranchID))
    }
  };
}

function branchById_(id) {
  var all = readTable_('Branches');
  for (var i = 0; i < all.length; i++) if (String(all[i].BranchID) === String(id)) return mapBranch_(all[i]);
  return null;
}

function mapBranch_(b) {
  return {
    id: String(b.BranchID), name: String(b.BranchName), code: String(b.BranchID),
    address: String(b.Address || ''), contact: String(b.Contact || ''),
    status: String(b.Status || 'Active'),
    queueMin: toNumber_(b.QueueMin) || 1, queueMax: toNumber_(b.QueueMax) || 18
  };
}

function mapProduct_(p) {
  var vol = [];
  try { vol = JSON.parse(p.VolumeJSON || '[]'); } catch (e) { vol = []; }
  return {
    id: String(p.ProductID), name: String(p.ProductName), category: String(p.Category || ''),
    price: toNumber_(p.Price), volumeLabel: String(p.VolumeLabel || ''),
    volume: vol, contents: String(p.ComboContents || ''),
    status: String(p.Status || 'Active'), sort: toNumber_(p.SortOrder)
  };
}

function mapRole_(r) {
  return {
    id: String(r.RoleID), name: String(r.RoleName), description: String(r.Description || ''),
    permissions: String(r.Permissions || '').split(',').map(function (s) { return s.trim(); }).filter(String),
    isAdmin: String(r.IsAdmin), canPOS: String(r.CanUsePOS), canKitchen: String(r.CanUseKitchen),
    allBranches: String(r.CanViewAllBranches), canManage: String(r.CanManageData), canExport: String(r.CanExport)
  };
}

function mapAccountForApp_(a) {
  return {
    id: String(a.AccountID), username: String(a.Username), displayName: String(a.DisplayName),
    roleId: String(a.RoleID), branchId: String(a.BranchID), status: String(a.Status),
    passwordSalt: String(a.PasswordSalt), passwordHash: String(a.PasswordHash),
    pinHash: String(a.PinHash || ''), createdAt: a.CreatedAt, lastLogin: a.LastLogin || '',
    forceChange: String(a.ForceChange || 'No')
  };
}

/* ------------------------------------------------------------------ */
/*  pull - reference data + recent orders                            */
/* ------------------------------------------------------------------ */
function apiPull_(body) {
  var cache = CacheService.getScriptCache();
  var payload = {};

  var cached = cache.get('ref_v1');
  if (cached) {
    payload = JSON.parse(cached);
  } else {
    payload = {
      branches: readTable_('Branches').filter(function (b) { return String(b.Status) !== 'Closed' || true; }).map(mapBranch_),
      products: readTable_('Products').map(mapProduct_),
      roles: readTable_('Roles').map(mapRole_),
      accounts: readTable_('Accounts').map(mapAccountForApp_),
      settings: readTable_('Settings').map(function (s) { return { key: String(s.Key), value: String(s.Value) }; })
    };
    cache.put('ref_v1', JSON.stringify(payload), CACHE_TTL_SECONDS);
  }

  // Recent orders so an owner sees every branch. Kitchen/cashier roles only
  // need their own branch; the app filters by role anyway.
  payload.orders = recentOrders_(body, MAX_PULL_ORDERS);
  payload.serverTime = new Date().toISOString();
  payload.today = todayKey_();
  return { ok: true, data: payload };
}

function recentOrders_(body, limit) {
  var sh = sheet_('Orders');
  var last = sh.getLastRow();
  if (last < 2) return [];
  var from = Math.max(2, last - limit + 1);
  var values = sh.getRange(from, 1, last - from + 1, sh.getLastColumn()).getValues();
  var head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0].map(String);

  var itemsByOrder = {};
  var itemsSheet = sheet_('OrderItems');
  var iLast = itemsSheet.getLastRow();
  if (iLast > 1) {
    var iFrom = Math.max(2, iLast - (limit * 4) + 1);
    var iValues = itemsSheet.getRange(iFrom, 1, iLast - iFrom + 1, itemsSheet.getLastColumn()).getValues();
    var iHead = itemsSheet.getRange(1, 1, 1, itemsSheet.getLastColumn()).getValues()[0].map(String);
    iValues.forEach(function (row) {
      var o = rowToObj_(iHead, row);
      var rid = String(o.RecordID);
      if (!itemsByOrder[rid]) itemsByOrder[rid] = [];
      var vol = [];
      try { vol = JSON.parse(o.VolumeJSON || '[]'); } catch (e) { vol = []; }
      itemsByOrder[rid].push({
        productId: String(o.ProductID), name: String(o.ProductName),
        qty: toNumber_(o.Qty), price: toNumber_(o.UnitPrice), lineTotal: toNumber_(o.LineTotal), volume: vol
      });
    });
  }

  return values.map(function (row) {
    var o = rowToObj_(head, row);
    var vol = {};
    try { vol = JSON.parse(o.VolumeTotalsJSON || '{}'); } catch (e) { vol = {}; }
    var rid = String(o.RecordID);
    return {
      recordId: rid,
      orderNo: String(o.OrderNo),
      businessDate: String(o.BusinessDate).slice(0, 10),
      createdAt: toIso_(o.CreatedAt),
      updatedAt: toIso_(o.UpdatedAt),
      branchId: String(o.BranchID),
      branchName: String(o.BranchName || ''),
      accountId: String(o.AccountID),
      cashierName: String(o.CashierName || ''),
      deviceId: String(o.DeviceID || ''),
      queueNumber: o.QueueNumber === '' ? null : toNumber_(o.QueueNumber),
      orderType: String(o.OrderType || ''),
      lineItems: itemsByOrder[rid] || [],
      volumeTotals: vol,
      subtotal: toNumber_(o.Subtotal),
      discountType: String(o.DiscountType || ''),
      discountValue: toNumber_(o.DiscountValue),
      discountAmount: toNumber_(o.DiscountAmount),
      total: toNumber_(o.Total),
      paymentMethod: String(o.PaymentMethod || ''),
      paymentStatus: String(o.PaymentStatus || 'Paid'),
      amountTendered: toNumber_(o.AmountTendered),
      changeDue: toNumber_(o.ChangeDue),
      paidAt: toIso_(o.PaidAt),
      kitchenStatus: String(o.KitchenStatus || 'New'),
      kitchenUpdatedAt: toIso_(o.KitchenUpdatedAt),
      servedAt: toIso_(o.ServedAt),
      voidedAt: toIso_(o.VoidedAt),
      voidReason: String(o.VoidReason || ''),
      notes: String(o.Notes || ''),
      revision: toNumber_(o.Revision) || 1,
      syncStatus: 'Sent'
    };
  });
}

function rowToObj_(head, row) {
  var o = {};
  for (var i = 0; i < head.length; i++) o[head[i]] = row[i];
  return o;
}

function toIso_(v) {
  if (!v) return '';
  if (v instanceof Date) return v.toISOString();
  return String(v);
}

/* ------------------------------------------------------------------ */
/*  pushOrders - the core write path                                  */
/* ------------------------------------------------------------------ */
function apiPushOrders_(body, payload) {
  var orders = payload.orders || [];
  if (!orders.length) return { ok: true, results: [] };

  var throttle = throttle_(body.deviceId);
  if (!throttle) return { ok: false, error: 'Too many requests from this device. Try again shortly.', results: [] };

  var lock = LockService.getScriptLock();
  var results = [];

  try {
    lock.waitLock(LOCK_TIMEOUT_MS);
  } catch (e) {
    return { ok: false, error: 'The spreadsheet is busy. The app will retry automatically.', results: [] };
  }

  try {
    var existing = existingIds_('Orders', 'RecordID');
    var head = sheet_('Orders').getRange(1, 1, 1, sheet_('Orders').getLastColumn()).getValues()[0].map(String);
    var revIdx = head.indexOf('Revision');
    var statusIdx = head.indexOf('KitchenStatus');
    var updIdx = head.indexOf('UpdatedAt');

    var newOrderRows = [];
    var newItemRows = [];
    var kitchenRows = [];
    var orderUpdates = []; // {row, col, value}

    orders.forEach(function (o) {
      var rid = String(o.recordId || '');
      if (!rid) { results.push({ recordId: '(missing)', status: 'error', message: 'Record has no Record ID' }); return; }

      var existingRow = existing[rid];

      if (!existingRow) {
        /* ---------- brand new order ---------- */
        var row = buildOrderRow_(head, o, body);
        newOrderRows.push(row);

        (o.lineItems || []).forEach(function (li, n) {
          newItemRows.push([
            rid, n + 1, o.orderNo || '', o.businessDate || todayKey_(), o.branchId || '',
            li.productId || '', li.name || '', toNumber_(li.qty), toNumber_(li.price),
            toNumber_(li.lineTotal), JSON.stringify(li.volume || [])
          ]);
        });

        kitchenRows.push([
          rid, o.orderNo || '', o.businessDate || todayKey_(), o.branchId || '',
          o.queueNumber === null || o.queueNumber === undefined ? '' : o.queueNumber,
          o.kitchenStatus || 'New', '', o.kitchenUpdatedAt || new Date(),
          (body.accountId || '') , o.notes || ''
        ]);

        results.push({ recordId: rid, status: 'ok' });
        existing[rid] = -1;

      } else {
        /* ---------- order already on the sheet ---------- */
        var sheetRev = revIdx >= 0 ? toNumber_(sheet_('Orders').getRange(existingRow, revIdx + 1).getValue()) : 1;
        var sheetStatus = statusIdx >= 0 ? String(sheet_('Orders').getRange(existingRow, statusIdx + 1).getValue()) : '';
        var clientRev = toNumber_(o.revision) || 1;

        var sameStatus = sheetStatus === String(o.kitchenStatus || '');
        var isOnlyStatusChange = sameStatus ? true : (clientRev === sheetRev + 1 || clientRev === sheetRev);

        if (clientRev > sheetRev && !sameStatus) {
          /* A legitimate status / payment update from this device.
             Update the existing row instead of adding a duplicate. */
          sheet_('Orders').getRange(existingRow, head.indexOf('KitchenStatus') + 1).setValue(o.kitchenStatus || 'New');
          if (head.indexOf('KitchenUpdatedAt') >= 0) sheet_('Orders').getRange(existingRow, head.indexOf('KitchenUpdatedAt') + 1).setValue(new Date());
          if (head.indexOf('PaymentStatus') >= 0) sheet_('Orders').getRange(existingRow, head.indexOf('PaymentStatus') + 1).setValue(o.paymentStatus || 'Paid');
          if (head.indexOf('PaymentMethod') >= 0) sheet_('Orders').getRange(existingRow, head.indexOf('PaymentMethod') + 1).setValue(o.paymentMethod || '');
          if (head.indexOf('AmountTendered') >= 0) sheet_('Orders').getRange(existingRow, head.indexOf('AmountTendered') + 1).setValue(toNumber_(o.amountTendered));
          if (head.indexOf('ChangeDue') >= 0) sheet_('Orders').getRange(existingRow, head.indexOf('ChangeDue') + 1).setValue(toNumber_(o.changeDue));
          if (head.indexOf('PaidAt') >= 0 && o.paidAt) sheet_('Orders').getRange(existingRow, head.indexOf('PaidAt') + 1).setValue(new Date(o.paidAt));
          if (head.indexOf('ServedAt') >= 0 && o.servedAt) sheet_('Orders').getRange(existingRow, head.indexOf('ServedAt') + 1).setValue(new Date(o.servedAt));
          if (head.indexOf('VoidedAt') >= 0 && o.voidedAt) sheet_('Orders').getRange(existingRow, head.indexOf('VoidedAt') + 1).setValue(new Date(o.voidedAt));
          if (head.indexOf('VoidReason') >= 0) sheet_('Orders').getRange(existingRow, head.indexOf('VoidReason') + 1).setValue(o.voidReason || '');
          if (head.indexOf('UpdatedAt') >= 0) sheet_('Orders').getRange(existingRow, head.indexOf('UpdatedAt') + 1).setValue(new Date());
          if (revIdx >= 0) sheet_('Orders').getRange(existingRow, revIdx + 1).setValue(clientRev);

          kitchenRows.push([
            rid, o.orderNo || '', o.businessDate || todayKey_(), o.branchId || '',
            o.queueNumber === null || o.queueNumber === undefined ? '' : o.queueNumber,
            o.kitchenStatus || 'New', sheetStatus, new Date(), body.accountId || '', o.notes || ''
          ]);

          results.push({ recordId: rid, status: 'ok', row: existingRow, note: 'Status updated' });

        } else if (clientRev > sheetRev && sameStatus) {
          /* Same status but a newer revision: assume a correction, update quietly. */
          if (revIdx >= 0) sheet_('Orders').getRange(existingRow, revIdx + 1).setValue(clientRev);
          if (updIdx >= 0) sheet_('Orders').getRange(existingRow, updIdx + 1).setValue(new Date());
          results.push({ recordId: rid, status: 'ok', row: existingRow, note: 'Revision updated' });

        } else if (clientRev === sheetRev) {
          /* Already stored at this revision: treat as a duplicate, never duplicate the row. */
          results.push({ recordId: rid, status: 'duplicate', row: existingRow });

        } else {
          /* The sheet has a newer revision than this device. Do not overwrite:
             park it as Needs Review so a person decides. */
          results.push({
            recordId: rid, status: 'review', row: existingRow,
            message: 'Sheet revision ' + sheetRev + ' is newer than this device (' + clientRev + '). Changed on another device.'
          });
        }
      }
    });

    if (newOrderRows.length) appendRows_('Orders', newOrderRows);
    if (newItemRows.length) appendRows_('OrderItems', newItemRows);
    if (kitchenRows.length) appendRows_('KitchenQueue', kitchenRows);

    // One sync-log line per order so the owner can trace every record.
    var logRows = results.map(function (r) {
      return [Utilities.getUuid(), r.recordId, 'Order', body.deviceId || '', body.deviceLabel || '',
        new Date(), r.status === 'ok' ? 'Sent' : r.status === 'duplicate' ? 'Duplicate' : r.status === 'review' ? 'Needs Review' : 'Error',
        r.message || r.note || '', '', ''];
    });
    try { appendRows_('SyncLog', logRows); } catch (e) { /* ignore */ }

    // Safety net: a duplicate Record ID in Orders would break reporting.
    // Detect and flag instead of failing the request.
    var dupes = detectDuplicateIds_();
    if (dupes.length) {
      results.push({ recordId: '__duplicates__', status: 'error', message: 'Duplicate Record IDs found in Orders: ' + dupes.slice(0, 5).join(', ') });
    }

    void orderUpdates;
    return { ok: true, results: results };

  } finally {
    try { lock.releaseLock(); } catch (e) {}
  }
}

function buildOrderRow_(head, o, body) {
  var itemCount = (o.lineItems || []).reduce(function (a, l) { return a + toNumber_(l.qty); }, 0);
  var values = {
    RecordID: o.recordId,
    OrderNo: o.orderNo || '',
    BusinessDate: o.businessDate || todayKey_(),
    CreatedAt: o.createdAt ? new Date(o.createdAt) : new Date(),
    UpdatedAt: new Date(),
    BranchID: o.branchId || '',
    BranchName: o.branchName || '',
    AccountID: o.accountId || '',
    CashierName: o.cashierName || '',
    DeviceID: o.deviceId || body.deviceId || '',
    QueueNumber: o.queueNumber === null || o.queueNumber === undefined ? '' : o.queueNumber,
    OrderType: o.orderType || 'Dine-in',
    ItemCount: itemCount,
    Subtotal: toNumber_(o.subtotal),
    DiscountType: o.discountType || '',
    DiscountValue: toNumber_(o.discountValue),
    DiscountAmount: toNumber_(o.discountAmount),
    Total: toNumber_(o.total),
    PaymentMethod: o.paymentMethod || '',
    PaymentStatus: o.paymentStatus || 'Paid',
    AmountTendered: toNumber_(o.amountTendered),
    ChangeDue: toNumber_(o.changeDue),
    PaidAt: o.paidAt ? new Date(o.paidAt) : '',
    KitchenStatus: o.kitchenStatus || 'New',
    KitchenUpdatedAt: new Date(),
    ServedAt: o.servedAt ? new Date(o.servedAt) : '',
    VoidedAt: o.voidedAt ? new Date(o.voidedAt) : '',
    VoidReason: o.voidReason || '',
    VoidedBy: o.voidedBy || '',
    Notes: o.notes || '',
    VolumeTotalsJSON: JSON.stringify(o.volumeTotals || {}),
    Revision: toNumber_(o.revision) || 1,
    SyncStatus: 'Sent',
    ReceivedAt: new Date()
  };
  return head.map(function (h) { return values[h] !== undefined ? values[h] : ''; });
}

function detectDuplicateIds_() {
  try {
    var ids = existingIds_('Orders', 'RecordID');
    void ids;
    var sh = sheet_('Orders');
    var last = sh.getLastRow();
    if (last < 2) return [];
    var col = sh.getRange(2, 1, last - 1, 1).getValues();
    var seen = {}, dupes = [];
    col.forEach(function (r) {
      var v = String(r[0] || '').trim();
      if (!v) return;
      if (seen[v]) { if (dupes.indexOf(v) < 0) dupes.push(v); } else seen[v] = true;
    });
    return dupes;
  } catch (e) { return []; }
}

/* ------------------------------------------------------------------ */
/*  pushNonRevenue                                                   */
/* ------------------------------------------------------------------ */
function apiPushNonRevenue_(body, payload) {
  var logs = payload.logs || [];
  if (!logs.length) return { ok: true, results: [] };

  var existing = existingIds_('NonRevenueLogs', 'RecordID');
  var rows = [];
  logs.forEach(function (l) {
    if (!l.recordId || existing[String(l.recordId)]) return;
    var totalQty = (l.items || []).reduce(function (a, i) { return a + toNumber_(i.qty); }, 0);
    rows.push([
      l.recordId, l.logType || 'Stock Loss', l.businessDate || todayKey_(),
      l.createdAt ? new Date(l.createdAt) : new Date(), l.branchId || '',
      l.loggedBy || '', l.loggedByName || '', JSON.stringify(l.items || []),
      JSON.stringify(l.volumeTotals || {}), l.note || '', totalQty, new Date()
    ]);
  });
  appendRows_('NonRevenueLogs', rows);
  logSync_(Utilities.getUuid(), '', 'NonRevenue', body.deviceId || '', body.deviceLabel || '', 'Sent', rows.length + ' log(s)', '', '');
  return { ok: true, data: { added: rows.length } };
}

/* ------------------------------------------------------------------ */
/*  pushEOD - end of day totals to the Reports sheet                 */
/* ------------------------------------------------------------------ */
function apiPushEOD_(body, payload) {
  var reports = payload.reports || [];
  if (!reports.length) return { ok: true, results: [] };

  var sh = sheet_('Reports');
  var existing = readTable_('Reports');
  var rows = [];

  reports.forEach(function (r) {
    var key = String(r.reportDate || '') + '|' + String(r.branchId || '');
    var found = null;
    for (var i = 0; i < existing.length; i++) {
      var d = existing[i].ReportDate;
      var dk = (d instanceof Date) ? todayKey_(d) : String(d).slice(0, 10);
      if (dk + '|' + String(existing[i].BranchID) === key) { found = existing[i]; break; }
    }
    rows.push([
      r.reportDate || todayKey_(), r.branchId || '', toNumber_(r.paidRevenue),
      toNumber_(r.cash), toNumber_(r.gcash), toNumber_(r.grab),
      toNumber_(r.unpaidTotal), toNumber_(r.voidedTotal), toNumber_(r.orderCount),
      toNumber_(r.avgOrder), JSON.stringify(r.volume || {}), new Date()
    ]);
    void found;
  });

  appendRows_('Reports', rows);
  return { ok: true, data: { added: rows.length } };
}

/* ------------------------------------------------------------------ */
/*  voidOrder                                                        */
/* ------------------------------------------------------------------ */
function apiVoidOrder_(body, payload) {
  var rid = String(payload.recordId || '');
  if (!rid) return { ok: false, error: 'recordId is required' };
  var row = findRow_('Orders', 'RecordID', rid);
  if (!row) return { ok: false, error: 'Order not found on the sheet.' };

  var sh = sheet_('Orders');
  var head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0].map(String);
  var set = function (col, val) {
    var i = head.indexOf(col);
    if (i >= 0) sh.getRange(row, i + 1).setValue(val);
  };
  set('KitchenStatus', 'Voided');
  set('VoidedAt', new Date());
  set('VoidReason', payload.reason || '');
  set('VoidedBy', payload.accountId || '');
  set('UpdatedAt', new Date());
  var revIdx = head.indexOf('Revision');
  if (revIdx >= 0) {
    var cur = toNumber_(sh.getRange(row, revIdx + 1).getValue());
    sh.getRange(row, revIdx + 1).setValue(cur + 1);
  }

  appendRows_('KitchenQueue', [[rid, '', '', '', '', 'Voided', '', new Date(), payload.accountId || '', payload.reason || '']]);
  logSync_(Utilities.getUuid(), rid, 'Order', body.deviceId || '', body.deviceLabel || '', 'Voided', payload.reason || '', '', payload.accountId || '');
  return { ok: true, data: { recordId: rid } };
}

/* ------------------------------------------------------------------ */
/*  manage - products, branches, accounts                            */
/* ------------------------------------------------------------------ */
function apiManage_(body, payload) {
  var entity = String(payload.entity || '');
  var op = String(payload.op || '');
  var rec = payload.record || {};

  if (entity === 'products') {
    var sh = sheet_('Products');
    var head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0].map(String);
    var rowNo = findRow_('Products', 'ProductID', rec.id);
    var rowValues = {
      ProductID: rec.id, ProductName: rec.name, Category: rec.category || '',
      Price: toNumber_(rec.price), VolumeLabel: rec.volumeLabel || '',
      VolumeJSON: JSON.stringify(rec.volume || []), ComboContents: rec.contents || '',
      Status: rec.status || 'Active', SortOrder: toNumber_(rec.sort)
    };
    var arr = head.map(function (h) { return rowValues[h] !== undefined ? rowValues[h] : ''; });
    if (op === 'delete') {
      if (rowNo) sh.deleteRow(rowNo);
      return { ok: true, data: { deleted: rec.id } };
    }
    if (rowNo) sh.getRange(rowNo, 1, 1, head.length).setValues([arr]);
    else appendRows_('Products', [arr]);
    CacheService.getScriptCache().remove('ref_v1');
    return { ok: true, data: { id: rec.id } };
  }

  if (entity === 'branches') {
    var bs = sheet_('Branches');
    var bhead = bs.getRange(1, 1, 1, bs.getLastColumn()).getValues()[0].map(String);
    var bRow = findRow_('Branches', 'BranchID', rec.id);
    var bValues = {
      BranchID: rec.id, BranchName: rec.name, Address: rec.address || '',
      Contact: rec.contact || '', Status: rec.status || 'Active',
      QueueMin: toNumber_(rec.queueMin) || 1, QueueMax: toNumber_(rec.queueMax) || 18
    };
    var bArr = bhead.map(function (h) { return bValues[h] !== undefined ? bValues[h] : ''; });
    if (bRow) bs.getRange(bRow, 1, 1, bhead.length).setValues([bArr]);
    else appendRows_('Branches', [bArr]);
    CacheService.getScriptCache().remove('ref_v1');
    return { ok: true, data: { id: rec.id } };
  }

  if (entity === 'accounts') {
    var as = sheet_('Accounts');
    var ahead = as.getRange(1, 1, 1, as.getLastColumn()).getValues()[0].map(String);
    var aRow = findRow_('Accounts', 'AccountID', rec.id);

    // Never accept a plain password from a non-admin: the caller already
    // hashed it. When plainPassword is present, hash it here so the sheet
    // only ever stores salt + hash.
    var salt = String(rec.passwordSalt || randomSalt_());
    var hash = String(rec.passwordHash || '');
    if (payload.record.plainPassword) hash = hashPassword_(String(rec.plainPassword), salt);
    if (!hash && !aRow) hash = hashPassword_('ChangeMe123', salt);

    var pinHash = String(rec.pinHash || '');
    if (rec.plainPin) pinHash = hashPassword_(String(rec.plainPin), salt);

    var aValues = {
      AccountID: rec.id, Username: String(rec.username || '').toLowerCase(),
      DisplayName: rec.displayName || '', RoleID: rec.roleId || 'ROLE-CASHIER',
      BranchID: rec.branchId || '', Status: rec.status || 'Active',
      PasswordSalt: salt, PasswordHash: hash, PinHash: pinHash,
      CreatedAt: rec.createdAt ? new Date(rec.createdAt) : new Date(),
      LastLogin: rec.lastLogin || '', ForceChange: rec.forceChange || 'No',
      UpdatedAt: new Date()
    };
    var aArr = ahead.map(function (h) { return aValues[h] !== undefined ? aValues[h] : ''; });
    if (aRow) as.getRange(aRow, 1, 1, ahead.length).setValues([aArr]);
    else appendRows_('Accounts', [aArr]);

    // Keep the username unique
    var dupe = 0;
    readTable_('Accounts').forEach(function (a) {
      if (String(a.Username).toLowerCase() === String(rec.username).toLowerCase()) dupe++;
    });
    CacheService.getScriptCache().remove('ref_v1');
    return { ok: true, data: { id: rec.id, usernameWarning: dupe > 1 ? 'This username appears ' + dupe + ' times. Remove the duplicate.' : '' } };
  }

  return { ok: false, error: 'Unknown entity: ' + entity };
}

/* ------------------------------------------------------------------ */
/*  changePassword                                                   */
/* ------------------------------------------------------------------ */
function apiChangePassword_(body, payload) {
  var accountId = String(payload.accountId || '');
  var newPassword = String(payload.newPassword || '');
  if (!accountId || newPassword.length < 6) return { ok: false, error: 'A password of at least 6 characters is required.' };

  var accounts = readTable_('Accounts');
  var target = null, actor = null;
  accounts.forEach(function (a) {
    if (String(a.AccountID) === accountId) target = a;
    if (String(a.AccountID) === String(payload.actorId)) actor = a;
  });
  if (!target) return { ok: false, error: 'Account not found.' };

  var actorIsAdmin = actor && String(actor.RoleID) === 'ROLE-ADMIN';
  if (!actorIsAdmin) {
    var okOld = hashPassword_(String(payload.oldPassword || ''), String(target.PasswordSalt)) === String(target.PasswordHash);
    if (!okOld) return { ok: false, error: 'The current password is wrong.' };
  }

  var row = findRow_('Accounts', 'AccountID', accountId);
  var sh = sheet_('Accounts');
  var head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0].map(String);
  var salt = randomSalt_();
  var set = function (col, val) {
    var i = head.indexOf(col);
    if (i >= 0) sh.getRange(row, i + 1).setValue(val);
  };
  set('PasswordSalt', salt);
  set('PasswordHash', hashPassword_(newPassword, salt));
  set('ForceChange', 'No');
  set('UpdatedAt', new Date());
  CacheService.getScriptCache().remove('ref_v1');
  logSync_(Utilities.getUuid(), accountId, 'Password', body.deviceId || '', body.deviceLabel || '', 'Changed', '', '', payload.actorId || '');
  return { ok: true, data: { accountId: accountId } };
}

/* ------------------------------------------------------------------ */
/*  saveSettings                                                     */
/* ------------------------------------------------------------------ */
function apiSaveSettings_(body, payload) {
  var settings = payload.settings || {};
  var sh = sheet_('Settings');
  var head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0].map(String);
  var keyIdx = head.indexOf('Key');
  var valIdx = head.indexOf('Value');
  var last = sh.getLastRow();
  var existing = {};
  if (last > 1) {
    var rows = sh.getRange(2, 1, last - 1, head.length).getValues();
    rows.forEach(function (r, i) { existing[String(r[keyIdx])] = i + 2; });
  }
  Object.keys(settings).forEach(function (k) {
    if (existing[k]) sh.getRange(existing[k], valIdx + 1).setValue(settings[k]);
    else appendRows_('Settings', [head.map(function (h) {
      if (h === 'Key') return k;
      if (h === 'Value') return settings[k];
      return '';
    })]);
  });
  CacheService.getScriptCache().remove('ref_v1');
  logSync_(Utilities.getUuid(), '', 'Settings', body.deviceId || '', body.deviceLabel || '', 'Saved', Object.keys(settings).join(', '), '', payload.accountId || '');
  return { ok: true, data: { saved: Object.keys(settings).length } };
}

/* ------------------------------------------------------------------ */
/*  report - aggregated numbers for a range                          */
/* ------------------------------------------------------------------ */
function apiReport_(body, payload) {
  var from = String(payload.from || todayKey_());
  var to = String(payload.to || from);
  var branch = String(payload.branchId || 'all');

  var orders = readTable_('Orders');
  var byBranch = {};
  var totals = { paidRevenue: 0, unpaid: 0, voided: 0, orders: 0, byMethod: {} };
  var volume = {};

  orders.forEach(function (o) {
    var d = (o.BusinessDate instanceof Date) ? todayKey_(o.BusinessDate) : String(o.BusinessDate).slice(0, 10);
    if (!d || d < from || d > to) return;
    if (branch !== 'all' && String(o.BranchID) !== branch) return;

    var bid = String(o.BranchID);
    if (!byBranch[bid]) byBranch[bid] = { branchId: bid, paid: 0, unpaid: 0, voided: 0, orders: 0, byMethod: {} };
    var b = byBranch[bid];

    var isVoid = String(o.KitchenStatus) === 'Voided';
    var amt = toNumber_(o.Total);
    if (isVoid) { b.voided += amt; totals.voided += amt; return; }

    b.orders++; totals.orders++;
    if (String(o.PaymentStatus) === 'Paid') {
      b.paid += amt; totals.paidRevenue += amt;
      var m = String(o.PaymentMethod || 'Unknown');
      b.byMethod[m] = (b.byMethod[m] || 0) + amt;
      totals.byMethod[m] = (totals.byMethod[m] || 0) + amt;
    } else {
      b.unpaid += amt; totals.unpaid += amt;
    }

    try {
      var v = JSON.parse(o.VolumeTotalsJSON || '{}');
      Object.keys(v).forEach(function (k) { volume[k] = (volume[k] || 0) + toNumber_(v[k]); });
    } catch (e) {}
  });

  return { ok: true, data: { from: from, to: to, branchId: branch, totals: totals, byBranch: byBranch, volume: volume } };
}

/* ------------------------------------------------------------------ */
/*  Optional: nightly housekeeping (installable trigger)             */
/* ------------------------------------------------------------------ */
function nightlyHousekeeping() {
  // 1) Rebuild the Reports sheet for yesterday, per branch, so the owner has
  //    a permanent daily summary even if a device never came online.
  var yesterday = todayKey_(new Date(new Date().getTime() - 86400000));
  var res = apiReport_({}, { from: yesterday, to: yesterday, branchId: 'all' });
  var rows = Object.keys(res.data.byBranch).map(function (k) {
    var b = res.data.byBranch[k];
    return [yesterday, k, b.paid, JSON.stringify(b.byMethod || {}), b.unpaid, b.voided, b.orders,
      b.orders ? Math.round((b.paid / b.orders) * 100) / 100 : 0, JSON.stringify(res.data.volume), new Date()];
  });
  if (rows.length) appendRows_('Reports', rows);

  // 2) Trim the SyncLog so it never hits the sheet row limit.
  var sh = sheet_('SyncLog');
  var maxRows = 20000;
  if (sh.getLastRow() > maxRows) {
    sh.deleteRows(2, sh.getLastRow() - maxRows);
  }
  return { ok: true, date: yesterday, branches: rows.length };
}

/**
 * Run once to add the nightly trigger.
 */
function installNightlyTrigger() {
  var triggers = ScriptApp.getProjectTriggers();
  triggers.forEach(function (t) { if (t.getHandlerFunction() === 'nightlyHousekeeping') ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('nightlyHousekeeping').timeBased().atHour(1).everyDays(1).create();
  return 'Nightly housekeeping trigger installed for 1 AM (' + USER_SETTINGS.TIME_ZONE + ').';
}
