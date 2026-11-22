/**
 * =====================================================================
 *  {{KIT_TITLE}} - Google Apps Script backend
 *  File: Config.gs
 * ---------------------------------------------------------------------
 *  EDIT THE THREE VALUES IN THE "USER SETTINGS" BLOCK, THEN RUN
 *  installKit() ONCE.
 * =====================================================================
 */

/** ================== USER SETTINGS - EDIT THESE ==================== */
var USER_SETTINGS = {

  // 1) ACCESS TOKEN
  //    Any long random text. It is the shared password between the app and
  //    this spreadsheet. Paste the SAME value into the app when it asks for
  //    the "Access token". Generate one with: bash tools/generate-token.sh
  ACCESS_TOKEN: '{{DEFAULT_TOKEN}}',

  // 2) OWNER USERNAME
  //    The account created by the installer is always "owner" with the
  //    password below. You can change it later inside the app.
  OWNER_PASSWORD: '{{OWNER_PASSWORD}}',

  // 3) TIME ZONE
  //    Used for BusinessDate so daily sales match the branch's real day.
  TIME_ZONE: '{{TIME_ZONE}}'
};
/** ================================================================== */

/** Spreadsheet this script is bound to (or the standalone ID below). */
var SPREADSHEET_ID = '{{SPREADSHEET_ID}}';            // leave '' when the script is bound to the Sheet
var CACHE_TTL_SECONDS = 600;        // how long getMenu caches reference data
var MAX_PULL_ORDERS = 800;          // orders returned by a pull request
var LOCK_TIMEOUT_MS = 20000;

/** Sheet definitions: name -> header row. Order matters for reports. */
var SHEETS = {
  Accounts: ['AccountID','Username','DisplayName','RoleID','BranchID','Status','PasswordSalt','PasswordHash','PinHash','CreatedAt','LastLogin','ForceChange','UpdatedAt'],
  Roles: ['RoleID','RoleName','Description','Permissions','IsAdmin','CanUsePOS','CanUseKitchen','CanViewAllBranches','CanManageData','CanExport'],
  Branches: ['BranchID','BranchName','Address','Contact','Status','QueueMin','QueueMax','Note'],
  Products: ['ProductID','ProductName','Category','Price','VolumeLabel','VolumeJSON','ComboContents','Status','SortOrder'],
  Orders: ['RecordID','OrderNo','BusinessDate','CreatedAt','UpdatedAt','BranchID','BranchName','AccountID','CashierName','DeviceID','QueueNumber','OrderType','ItemCount','Subtotal','DiscountType','DiscountValue','DiscountAmount','Total','PaymentMethod','PaymentStatus','AmountTendered','ChangeDue','PaidAt','KitchenStatus','KitchenUpdatedAt','ServedAt','VoidedAt','VoidReason','VoidedBy','Notes','VolumeTotalsJSON','Revision','SyncStatus','ReceivedAt'],
  OrderItems: ['RecordID','LineNo','OrderNo','BusinessDate','BranchID','ProductID','ProductName','Qty','UnitPrice','LineTotal','VolumeJSON'],
  KitchenQueue: ['RecordID','OrderNo','BusinessDate','BranchID','QueueNumber','KitchenStatus','PreviousStatus','UpdatedAt','UpdatedBy','Note'],
  NonRevenueLogs: ['RecordID','LogType','BusinessDate','CreatedAt','BranchID','LoggedBy','LoggedByName','ItemsJSON','VolumeTotalsJSON','Note','TotalQty','ReceivedAt'],
  Settings: ['Key','Value','Note'],
  Reports: ['ReportDate','BranchID','PaidRevenue','PaymentBreakdownJSON','UnpaidTotal','VoidedTotal','OrderCount','AvgOrder','VolumeJSON','GeneratedAt'],
  SyncLog: ['SyncID','RecordID','RecordType','DeviceID','DeviceLabel','SentAt','Result','Note','BranchID','AccountID']
};

/** Column order used by the app when it reads rows back. */
var API_MAPS = {
  branches: ['BranchID','BranchName','Address','Contact','Status','QueueMin','QueueMax'],
  products: ['ProductID','ProductName','Category','Price','VolumeLabel','VolumeJSON','ComboContents','Status','SortOrder'],
  roles:    ['RoleID','RoleName','Description','Permissions','IsAdmin','CanUsePOS','CanUseKitchen','CanViewAllBranches','CanManageData','CanExport'],
  accounts: ['AccountID','Username','DisplayName','RoleID','BranchID','Status','PasswordSalt','PasswordHash','PinHash','CreatedAt','LastLogin','ForceChange'],
  settings: ['Key','Value','Note']
};
