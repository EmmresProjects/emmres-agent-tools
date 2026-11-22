# Using the app

## Screens

| Screen | Who sees it | What it is for |
| --- | --- | --- |
| Point of Sale | roles with `pos.use` | Big product buttons, cart, checkout. |
| Kitchen | roles with `kitchen.view` | Live queue strip and Preparing / Ready / Served / Voided. |
| Orders | roles with `reports.view` | Every order with filters, unpaid list, void, reprint. |
| Reports | roles with `reports.view` | Sales by day, payment split, branch comparison, volume counts, CSV. |
| Non-Revenue | roles with `nonrev.log` | Stock that left without a sale: {{NONREV_LABELS}}. |
| Products & Menu | roles with `products.manage` | Add or edit products and prices. |
| Accounts | admins | Add users, reset passwords, disable leavers. |
| Sync | everyone | Pending / Sent / Needs Review, retry, backup export. |
| Settings | everyone | Business details, feature flags, Google Sheets connection. |

## The counter flow

1. **Add items.** Tap a product. Long-press (or right-click) adds 5.
   A `COMBO` tag means the price covers several items; the volume report still
   counts every component.
2. **Fix the quantity.** Type into the quantity box in the cart — `50` and press
   `Enter`. The line total and the cart total update immediately.
3. **Discount** (if `features.discounts`). Percent or fixed amount, applied to
   the whole cart, shown as a separate line.
4. **Pay & Save.** Pick the payment type ({{METHOD_LIST}}). For cash the keypad
   shows exact-cash shortcuts and the live change. For the rest the amount is the
   total.
5. **Order Only (Unpaid)** leaves the sale unpaid but still sends the order to the
   kitchen. Collect later from **Orders → Mark paid**.

## Queue cards

Cards run from `QueueMin` to `QueueMax` ({{QUEUE_RANGE}} in this build). One card
is assigned per order, the lowest free card is suggested, and the card returns to
the pool when the order is Served or Voided (setting `AutoReleaseQueue`).
Turning off `features.queueCards` hides the whole mechanism — orders still save.

## Kitchen

The strip at the top of Point of Sale shows what is live. The Kitchen screen has
one column per status. Orders older than `KitchenWarnMinutes` turn amber; much
older turns red. Every change is written to `KitchenQueue` with the staff member
and timestamp.

## Non-revenue

{{NONREV_LABELS}} are recorded per item and never added to revenue. They exist so
stock counts reconcile: sold + non-revenue + on-hand = deliveries.

## Roles and permissions

| Permission | Means |
| --- | --- |
| `pos.use` | Can take orders |
| `kitchen.view` / `kitchen.update` | Can see / advance the queue |
| `payment.collect` | Can take payment |
| `order.void.own` | Can void their own order inside `VoidWindowMinutes` |
| `order.void.any` | Can void any order, any time, with a reason |
| `reports.view` | Can open Orders and Reports |
| `nonrev.log` | Can record non-revenue entries |
| `eod.manage` | Can close the day |
| `products.manage` | Can edit products and prices |
| `export` | Can export CSV |

A role with `allBranches` sees every branch; everyone else sees only their own.

## Offline behaviour

Everything works with the network down. Records are written to the device first
and marked **Pending**. When the connection returns (or every `SyncIntervalSec`),
pending records are pushed. A record that the server rejects becomes **Needs
Review** and is never silently dropped.

Storage on the device: IndexedDB when available, localStorage otherwise. If
neither is writable (for example a sandboxed preview frame) the app says
`preview` in Settings and keeps records in memory for the session only.

## Printing

Receipts are a print stylesheet, so **Ctrl+P** on the receipt dialog prints just
the receipt. The footer text is `ReceiptFooter` in Settings.
