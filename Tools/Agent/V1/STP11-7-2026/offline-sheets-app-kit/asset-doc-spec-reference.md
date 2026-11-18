# Specification reference

Every generated project is one JSON file. This page documents every field.

## Top level

| Field | Required | Meaning |
| --- | --- | --- |
| `specVersion` | no | Always `1`. |
| `app` | yes | Identity, colours, ids. |
| `categories` | no | Product groups. Derived from products if omitted. |
| `products` | yes | The catalogue. |
| `paymentMethods` | yes | Checkout buttons, in order. First is the default. |
| `nonRevenueTypes` | yes | Reasons to log stock that left without a sale. |
| `branches` | yes | Locations and the queue-card range. |
| `roles` | yes | Permissions. At least one must have `isAdmin: true`. |
| `accounts` | yes | Sign-in rows. |
| `features` | no | Switch modules on/off. |
| `queue` | no | `{ "min": 1, "max": 18 }`. |
| `settings` | yes | Extra `Settings` rows (currency, timings, footer). |
| `settingNotes` | no | Human explanation shown in the Settings sheet. |
| `token` | no | Leave `default` empty to get a random `CHANGE-ME-…`. |

## app

```json
"app": {
  "name": "Brew Street",              // APK / EXE shortcut
  "title": "Brew Street Coffee POS",  // page title
  "slug": "brew-street",              // storage prefix + artifact names
  "businessName": "Brew Street Coffee",
  "tagline": "one line under the title",
  "author": "Brew Street Operations",
  "authorEmail": "ops@example.com",
  "version": "1.0.0",
  "brandColor": "#d9a05b",            // icon + accents
  "brandColorAlt": "#b8763a",         // icon gradient end
  "brandInk": "#2a1705",              // text on the icon
  "themeColor": "#0f0c0a",            // status bar / window chrome
  "initials": "BS",                   // 1-3 characters on the icon
  "androidAppId": "ph.brewstreet.pos",
  "orientation": "default",
  "keystoreAlias": "brewstreet",
  "timeZone": "Asia/Manila"
}
```

## products

```json
{
  "id": "P-ESP-01",                   // 2-16 capitals/digits/hyphens, unique
  "name": "Espresso",
  "category": "CAT-ESPRESSO",
  "price": 85,
  "volumeLabel": "shot",              // what one unit is, shown on the button
  "volume": [{ "unit": "Espresso Shot", "qty": 1 }],
  "contents": "Combo contents, shown to staff",
  "status": "Active",
  "sort": 10
}
```

`volume` is what the volume report counts. A combo with three components adds to
all three when sold, while revenue counts the combo once at its own price.

## roles and permissions

```json
{
  "id": "ROLE-BARISTA",
  "name": "Barista",
  "permissions": ["pos.use", "payment.collect", "order.void.own", "reports.view"],
  "isAdmin": false, "canPOS": true, "canKitchen": true,
  "allBranches": false, "canManage": false, "canExport": false
}
```

Permissions: `pos.use`, `kitchen.view`, `kitchen.update`, `payment.collect`,
`order.void.own`, `order.void.any`, `reports.view`, `nonrev.log`, `eod.manage`,
`products.manage`, `export`.

## accounts

```json
"accounts": {
  "ownerUsername": "owner",
  "ownerPassword": "BrewOwner@2026",
  "list": [
    { "accountId": "ACC-OWNER", "username": "owner", "displayName": "Mia Santos",
      "roleId": "ROLE-ADMIN", "branchId": "BR-01",
      "password": "BrewOwner@2026", "pin": "1234" }
  ]
}
```

Passwords must be at least 8 characters. The generator hashes them once and the
same hash goes into the workbook, the app seed and the Apps Script installer, so
the first sign-in works in all three places.

## features

```json
"features": {
  "kitchen": true,        // kitchen screen + live strip
  "queueCards": true,     // numbered cards
  "discounts": true,      // discount button at checkout
  "productVolume": true,  // volume counts in reports
  "multiBranch": true,    // branch selector
  "unpaidOrders": true    // Order Only / pay later
}
```

Each one becomes a `Feature*` row in the `Settings` sheet, so the owner can flip it
later without regenerating.

## Validation

`python <kit>/tools-generate-kit.py validate --spec spec.json` prints every
problem with its JSON path, for example:

```json
{
  "ok": false,
  "errors": [
    "products[3].price: must be a number, 0 or more",
    "accounts.list[1].branchId: \"BR-09\" is not declared in branches[]"
  ]
}
```

Nothing is written until the specification is clean.
