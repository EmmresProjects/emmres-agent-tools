/* =====================================================================
   {{APP_TITLE}} - LOCAL MOCK of the Apps Script backend
   ---------------------------------------------------------------------
   This is a development tool only. It speaks the same JSON protocol as
   google-apps-script/Code.gs so the app's sync path can be tested
   without a Google account.

   Run:   node tools/mock-server.js
   Then:  in the app, Settings > Google Sheets connection
          URL   = http://localhost:8765/exec
          token = dev-token

   IMPORTANT: this mock is NOT the real backend. The real one is the
   Apps Script project in /google-apps-script.
   ===================================================================== */
'use strict';

const http = require('http');
const crypto = require('crypto');

const PORT = process.env.PORT || 8765;
const TOKEN = process.env.TOKEN || 'dev-token';

/* --------------------------- tiny table store --------------------------- */
const db = {
  Orders: [], OrderItems: [], KitchenQueue: [], NonRevenueLogs: [], SyncLog: [], Reports: []
};

const sha256 = (s) => crypto.createHash('sha256').update(s, 'utf8').digest('hex');
const salt = () => crypto.randomUUID().replace(/-/g, '').slice(0, 12);
const hash = (pw, s) => 'sha256$' + sha256(s + '|' + pw);
const num = (v) => { const n = Number(v); return isFinite(n) ? n : 0; };
const now = () => new Date().toISOString();
const todayKey = () => new Date().toISOString().slice(0, 10);

/* --------------------------- reference data --------------------------- */
/* The seed file is generated JavaScript (window.KIT_SEED); load it without a DOM. */
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const seedPath = path.join(__dirname, '..', 'app', 'js', 'seed.js');
let seed;
try {
  const sandbox = { window: {} };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(seedPath, 'utf8'), sandbox);
  seed = sandbox.window.KIT_SEED || sandbox.window.EMMRES_SEED;
} catch (e) {
  console.error('Could not read the seed file at ' + seedPath + ': ' + e.message);
  process.exit(1);
}
if (!seed) { console.error('seed.js did not define window.KIT_SEED'); process.exit(1); }
const rowsToObjects = function (rows, headers) {
  return rows.map(function (r) {
    const o = {};
    headers.forEach(function (h, i) { o[h] = r[i]; });
    return o;
  });
};
/* Seed rows are compact arrays; the mock backend maps them exactly the way the
   browser does, so a test can compare what both sides see. */
const mapBranch = b => ({ id: b[0], name: b[1], address: b[2], contact: b[3], status: b[4], queueMin: Number(b[5] || 1), queueMax: Number(b[6] || 18) });
const mapRoles = r => ({ id: r[0], name: r[1], description: r[2], permissions: String(r[3] || '').split(',').filter(Boolean), isAdmin: r[4] === 'Yes', canPOS: r[5] === 'Yes', canKitchen: r[6] === 'Yes', allBranches: r[7] === 'Yes', canManage: r[8] === 'Yes', canExport: r[9] === 'Yes' });
const mapProduct = p => ({ id: p[0], name: p[1], category: p[2], price: Number(p[3] || 0), volumeLabel: p[4] || '', volume: Array.isArray(p[5]) ? p[5] : [], contents: p[6] || '', status: p[7] || 'Active', sort: Number(p[8] || 0) });
const mapAccount = a => ({ id: a[0], username: a[1], displayName: a[2], roleId: a[3], branchId: a[4], status: a[5] || 'Active', passwordSalt: a[6], passwordHash: a[7], pinHash: a[8], createdAt: '', lastLogin: '', forceChange: 'No' });

const branches = seed.branches.map(mapBranch);
const products = seed.products.map(mapProduct);
const roles = seed.roles.map(mapRoles);
const accounts = seed.accounts.map(mapAccount);
const settings = {};
(seed.settings || []).forEach(function (row) { settings[row[0]] = row[1]; });

/* ------------------------------- helpers ------------------------------- */
function logSync(recordId, type, result, note, deviceId, deviceLabel) {
  db.SyncLog.push({
    syncId: crypto.randomUUID(), recordId, recordType: type, deviceId: deviceId || '',
    deviceLabel: deviceLabel || '', sentAt: now(), result, note: note || '',
    branchId: '', accountId: ''
  });
}

function orderRow(o, deviceId) {
  return {
    RecordID: o.recordId, OrderNo: o.orderNo, BusinessDate: o.businessDate,
    CreatedAt: o.createdAt, UpdatedAt: now(), BranchID: o.branchId, BranchName: o.branchName,
    AccountID: o.accountId, CashierName: o.cashierName, DeviceID: o.deviceId || deviceId,
    QueueNumber: o.queueNumber === null || o.queueNumber === undefined ? '' : o.queueNumber,
    OrderType: o.orderType, ItemCount: (o.lineItems || []).reduce((a, l) => a + num(l.qty), 0),
    Subtotal: o.subtotal, DiscountType: o.discountType, DiscountValue: o.discountValue,
    DiscountAmount: o.discountAmount, Total: o.total, PaymentMethod: o.paymentMethod,
    PaymentStatus: o.paymentStatus, AmountTendered: o.amountTendered, ChangeDue: o.changeDue,
    PaidAt: o.paidAt, KitchenStatus: o.kitchenStatus, KitchenUpdatedAt: now(),
    ServedAt: o.servedAt, VoidedAt: o.voidedAt, VoidReason: o.voidReason, VoidedBy: o.voidedBy,
    Notes: o.notes, VolumeTotalsJSON: JSON.stringify(o.volumeTotals || {}),
    Revision: num(o.revision) || 1, SyncStatus: 'Sent', ReceivedAt: now()
  };
}

/* ------------------------------- actions ------------------------------- */
function apiLogin(body, p) {
  const uname = String(p.username || '').trim().toLowerCase();
  const acc = accounts.find(a => String(a.username).toLowerCase() === uname);
  if (!acc) { logSync(uname, 'Login', 'Failed', 'Unknown username', body.deviceId, body.deviceLabel); return { ok: false, error: 'Wrong username or password.' }; }
  if (String(acc.status).toLowerCase() !== 'active') return { ok: false, error: 'This account is disabled.' };
  if (hash(p.password, acc.passwordSalt) !== acc.passwordHash) {
    logSync(acc.id, 'Login', 'Failed', 'Wrong password', body.deviceId, body.deviceLabel);
    return { ok: false, error: 'Wrong username or password.' };
  }
  acc.lastLogin = now();
  const role = roles.find(r => r.id === acc.roleId);
  logSync(acc.id, 'Login', 'Success', 'Signed in', body.deviceId, body.deviceLabel);
  return {
    ok: true, data: {
      account: {
        id: acc.id, username: acc.username, displayName: acc.displayName, roleId: acc.roleId,
        branchId: acc.branchId, status: acc.status, salt: acc.passwordSalt,
        passwordHash: acc.passwordHash, forceChange: acc.forceChange, createdAt: acc.createdAt
      },
      role: role ? {
        id: role.id, name: role.name, permissions: role.permissions,
        isAdmin: role.isAdmin === 'Yes' || role.id === 'ROLE-ADMIN',
        canPOS: role.canPOS !== 'No', canKitchen: role.canKitchen !== 'No',
        allBranches: role.allBranches === 'Yes', canManage: role.canManage === 'Yes', canExport: role.canExport === 'Yes'
      } : null,
      branch: branches.find(b => b.id === acc.branchId) || null
    }
  };
}

function apiPull() {
  return {
    ok: true, data: {
      branches, products, roles,
      accounts: accounts.map(a => ({
        id: a.id, username: a.username, displayName: a.displayName, roleId: a.roleId,
        branchId: a.branchId, status: a.status, passwordSalt: a.passwordSalt,
        passwordHash: a.passwordHash, pinHash: a.pinHash, createdAt: a.createdAt,
        lastLogin: a.lastLogin, forceChange: a.forceChange
      })),
      /* Settings rows use the sheet shape (Key/Value); the app accepts both. */
      settings: Object.keys(settings).map(k => ({ Key: k, Value: String(settings[k]), Note: '' })),
      orders: db.Orders.slice(-400).map(o => {
        const items = db.OrderItems.filter(i => i.RecordID === o.RecordID).map(i => ({
          productId: i.ProductID, name: i.ProductName, qty: num(i.Qty),
          price: num(i.UnitPrice), lineTotal: num(i.LineTotal), volume: JSON.parse(i.VolumeJSON || '[]')
        }));
        return {
          recordId: o.RecordID, orderNo: o.OrderNo, businessDate: String(o.BusinessDate).slice(0, 10),
          createdAt: o.CreatedAt, updatedAt: o.UpdatedAt, branchId: o.BranchID, branchName: o.BranchName,
          accountId: o.AccountID, cashierName: o.CashierName, deviceId: o.DeviceID,
          queueNumber: o.QueueNumber === '' ? null : num(o.QueueNumber), orderType: o.OrderType,
          lineItems: items, volumeTotals: JSON.parse(o.VolumeTotalsJSON || '{}'),
          subtotal: num(o.Subtotal), discountAmount: num(o.DiscountAmount), total: num(o.Total),
          paymentMethod: o.PaymentMethod, paymentStatus: o.PaymentStatus,
          amountTendered: num(o.AmountTendered), changeDue: num(o.ChangeDue),
          kitchenStatus: o.KitchenStatus, notes: o.Notes, revision: num(o.Revision),
          syncStatus: 'Sent', servedAt: o.ServedAt, voidedAt: o.VoidedAt, voidReason: o.VoidReason
        };
      }),
      serverTime: now(), today: todayKey()
    }
  };
}

function apiPushOrders(body, p) {
  const results = [];
  (p.orders || []).forEach(o => {
    const existing = db.Orders.find(x => x.RecordID === o.recordId);
    if (!existing) {
      db.Orders.push(orderRow(o, body.deviceId));
      (o.lineItems || []).forEach((li, n) => db.OrderItems.push({
        RecordID: o.recordId, LineNo: n + 1, OrderNo: o.orderNo, BusinessDate: o.businessDate,
        BranchID: o.branchId, ProductID: li.productId, ProductName: li.name,
        Qty: li.qty, UnitPrice: li.price, LineTotal: li.lineTotal, VolumeJSON: JSON.stringify(li.volume || [])
      }));
      db.KitchenQueue.push({
        RecordID: o.recordId, OrderNo: o.orderNo, BusinessDate: o.businessDate, BranchID: o.branchId,
        QueueNumber: o.queueNumber, KitchenStatus: o.kitchenStatus, PreviousStatus: '', UpdatedAt: now(), UpdatedBy: '', Note: ''
      });
      results.push({ recordId: o.recordId, status: 'ok' });
      return;
    }

    const sheetRev = num(existing.Revision) || 1;
    const clientRev = num(o.revision) || 1;
    const sameStatus = String(existing.KitchenStatus) === String(o.kitchenStatus);

    if (clientRev > sheetRev && !sameStatus) {
      existing.KitchenStatus = o.kitchenStatus;
      existing.KitchenUpdatedAt = now();
      existing.PaymentStatus = o.paymentStatus;
      existing.PaymentMethod = o.paymentMethod;
      existing.AmountTendered = o.amountTendered;
      existing.ChangeDue = o.changeDue;
      existing.UpdatedAt = now();
      existing.Revision = clientRev;
      db.KitchenQueue.push({
        RecordID: o.recordId, OrderNo: o.orderNo, BusinessDate: o.businessDate, BranchID: o.branchId,
        QueueNumber: o.queueNumber, KitchenStatus: o.kitchenStatus, PreviousStatus: existing.KitchenStatus,
        UpdatedAt: now(), UpdatedBy: '', Note: ''
      });
      results.push({ recordId: o.recordId, status: 'ok', note: 'Status updated' });
    } else if (clientRev > sheetRev && sameStatus) {
      existing.Revision = clientRev;
      results.push({ recordId: o.recordId, status: 'ok', note: 'Revision updated' });
    } else if (clientRev === sheetRev) {
      results.push({ recordId: o.recordId, status: 'duplicate' });
    } else {
      results.push({
        recordId: o.recordId, status: 'review',
        message: 'Sheet revision ' + sheetRev + ' is newer than this device (' + clientRev + ').'
      });
    }
  });
  logSync('', 'Order', 'Sent', results.length + ' record(s) processed', body.deviceId, body.deviceLabel);
  return { ok: true, results };
}

function apiPushNonRevenue(body, p) {
  let added = 0;
  (p.logs || []).forEach(l => {
    if (db.NonRevenueLogs.find(x => x.RecordID === l.recordId)) return;
    db.NonRevenueLogs.push({
      RecordID: l.recordId, LogType: l.logType, BusinessDate: l.businessDate, CreatedAt: l.createdAt,
      BranchID: l.branchId, LoggedBy: l.loggedBy, LoggedByName: l.loggedByName,
      ItemsJSON: JSON.stringify(l.items || []), VolumeTotalsJSON: JSON.stringify(l.volumeTotals || {}),
      Note: l.note, TotalQty: (l.items || []).reduce((a, i) => a + num(i.qty), 0), ReceivedAt: now()
    });
    added++;
  });
  return { ok: true, data: { added } };
}

function apiPushEOD(body, p) {
  (p.reports || []).forEach(r => db.Reports.push({ ...r, GeneratedAt: now() }));
  return { ok: true, data: { added: (p.reports || []).length } };
}

function apiVoidOrder(body, p) {
  const o = db.Orders.find(x => x.RecordID === p.recordId);
  if (!o) return { ok: false, error: 'Order not found on the sheet.' };
  o.KitchenStatus = 'Voided';
  o.VoidedAt = now();
  o.VoidReason = p.reason || '';
  o.Revision = num(o.Revision) + 1;
  return { ok: true, data: { recordId: o.RecordID } };
}

function apiManage(body, p) {
  const rec = p.record || {};
  if (p.entity === 'products') {
    const i = products.findIndex(x => x.id === rec.id);
    if (p.op === 'delete') { if (i >= 0) products.splice(i, 1); return { ok: true, data: { deleted: rec.id } }; }
    const clean = {
      id: rec.id, name: rec.name, category: rec.category, price: num(rec.price),
      volumeLabel: rec.volumeLabel, volume: rec.volume || [], contents: rec.contents,
      status: rec.status, sort: num(rec.sort)
    };
    if (i >= 0) products[i] = clean; else products.push(clean);
    return { ok: true, data: { id: rec.id } };
  }
  if (p.entity === 'accounts') {
    const i = accounts.findIndex(x => x.id === rec.id);
    const s = rec.passwordSalt || salt();
    let h = rec.passwordHash || '';
    if (rec.plainPassword) h = hash(rec.plainPassword, s);
    const clean = {
      id: rec.id, username: rec.username, displayName: rec.displayName, roleId: rec.roleId,
      branchId: rec.branchId, status: rec.status, passwordSalt: s, passwordHash: h,
      pinHash: rec.plainPin ? hash(rec.plainPin, s) : (rec.pinHash || ''),
      createdAt: rec.createdAt || now(), lastLogin: rec.lastLogin || '', forceChange: rec.forceChange || 'No'
    };
    if (!h) clean.passwordHash = hash('ChangeMe123', s);
    if (i >= 0) accounts[i] = clean; else accounts.push(clean);
    return { ok: true, data: { id: rec.id } };
  }
  if (p.entity === 'branches') {
    const i = branches.findIndex(x => x.id === rec.id);
    const clean = {
      id: rec.id, name: rec.name, code: rec.id, address: rec.address, contact: rec.contact,
      status: rec.status, queueMin: num(rec.queueMin) || 1, queueMax: num(rec.queueMax) || 18
    };
    if (i >= 0) branches[i] = clean; else branches.push(clean);
    return { ok: true, data: { id: rec.id } };
  }
  return { ok: false, error: 'Unknown entity' };
}

function apiChangePassword(body, p) {
  const acc = accounts.find(a => a.id === p.accountId);
  if (!acc) return { ok: false, error: 'Account not found.' };
  if (!String(p.newPassword || '').length) return { ok: false, error: 'Empty password.' };
  if (p.oldPassword && hash(p.oldPassword, acc.passwordSalt) !== acc.passwordHash) {
    return { ok: false, error: 'The current password is wrong.' };
  }
  acc.passwordSalt = salt();
  acc.passwordHash = hash(p.newPassword, acc.passwordSalt);
  return { ok: true, data: { accountId: acc.id } };
}

function apiSaveSettings(body, p) {
  Object.keys(p.settings || {}).forEach(k => { settings[k] = p.settings[k]; });
  return { ok: true, data: { saved: Object.keys(p.settings || {}).length } };
}

function methods() {
  const raw = settings.PaymentMethods;
  const list = String(raw || 'Cash').split('|').map(s => s.trim()).filter(Boolean);
  return list.length ? list : ['Cash'];
}

function apiReport(body, p) {
  const from = p.from || todayKey(), to = p.to || from;
  const branch = p.branchId || 'all';
  const methodsList = methods();
  const totals = { paidRevenue: 0, unpaid: 0, voided: 0, orders: 0, byMethod: {} };
  methodsList.forEach(m => { totals.byMethod[m] = 0; });
  const byBranch = {}, volume = {};
  db.Orders.forEach(o => {
    const d = String(o.BusinessDate).slice(0, 10);
    if (d < from || d > to) return;
    if (branch !== 'all' && o.BranchID !== branch) return;
    byBranch[o.BranchID] = byBranch[o.BranchID] || { branchId: o.BranchID, paid: 0, unpaid: 0, voided: 0, orders: 0, byMethod: {} };
    const b = byBranch[o.BranchID];
    const amt = num(o.Total);
    if (o.KitchenStatus === 'Voided') { b.voided += amt; totals.voided += amt; return; }
    b.orders++; totals.orders++;
    if (o.PaymentStatus === 'Paid') {
      b.paid += amt; totals.paidRevenue += amt;
      const m = String(o.PaymentMethod || '');
      b.byMethod[m] = (b.byMethod[m] || 0) + amt;
      totals.byMethod[m] = (totals.byMethod[m] || 0) + amt;
    } else { b.unpaid += amt; totals.unpaid += amt; }
    const v = JSON.parse(o.VolumeTotalsJSON || '{}');
    Object.keys(v).forEach(k => { volume[k] = (volume[k] || 0) + num(v[k]); });
  });
  return { ok: true, data: { from, to, branchId: branch, totals, byBranch, volume } };
}

/* ------------------------------- server ------------------------------- */
const server = http.createServer((req, res) => {
  const cors = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'POST, GET, OPTIONS'
  };
  if (req.method === 'OPTIONS') { res.writeHead(204, cors); res.end(); return; }

  if (req.method === 'GET') {
    res.writeHead(200, { ...cors, 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, service: (seed.app && seed.app.title ? seed.app.title : 'Generated app') + ' MOCK API', hint: 'POST JSON with action + token' }));
    return;
  }

  let raw = '';
  req.on('data', c => { raw += c; });
  req.on('end', () => {
    let body = {};
    try { body = JSON.parse(raw || '{}'); } catch (e) { body = {}; }
    const send = (obj) => {
      res.writeHead(200, { ...cors, 'Content-Type': 'application/json' });
      res.end(JSON.stringify(obj));
      console.log(new Date().toISOString().slice(11, 19), body.action, '->', obj.ok ? 'ok' : 'FAIL', obj.error || '');
    };
    if (body.token !== TOKEN) return send({ ok: false, error: 'Access token is not valid.' });
    const p = body.payload || {};
    try {
      switch (body.action) {
        case 'ping': return send({ ok: true, data: { sheet: 'MOCK SHEET', timeZone: 'Asia/Manila', serverTime: now() } });
        case 'login': return send(apiLogin(body, p));
        case 'pull': return send(apiPull());
        case 'pushOrders': return send(apiPushOrders(body, p));
        case 'pushNonRevenue': return send(apiPushNonRevenue(body, p));
        case 'pushEOD': return send(apiPushEOD(body, p));
        case 'voidOrder': return send(apiVoidOrder(body, p));
        case 'manage': return send(apiManage(body, p));
        case 'changePassword': return send(apiChangePassword(body, p));
        case 'saveSettings': return send(apiSaveSettings(body, p));
        case 'report': return send(apiReport(body, p));
        case '__dump': return send({ ok: true, data: db });
        case '__reset':
          Object.keys(db).forEach(function (k) { db[k] = []; });
          return send({ ok: true, data: { reset: true } });
        default: return send({ ok: false, error: 'Unknown action: ' + body.action });
      }
    } catch (e) {
      return send({ ok: false, error: String(e && e.message || e) });
    }
  });
});

server.listen(PORT, '0.0.0.0', () => {
  console.log((seed.app && seed.app.title ? seed.app.title : 'Generated app') + ' MOCK backend');
  console.log('Listening on http://localhost:' + PORT + '/exec');
  console.log('Access token: ' + TOKEN);
  console.log('Accounts: ' + accounts.map(function (a) { return a.username; }).join(', '));
});
