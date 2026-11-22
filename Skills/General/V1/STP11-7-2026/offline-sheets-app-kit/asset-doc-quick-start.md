# Quick start

You have a complete, working app. Nothing here needs a paid account.

## 1. Open it right now

| What you want | What to do |
| --- | --- |
| Fastest look | Double-click `app/{{ARTIFACT_SLUG}}.html`. One file, no server, works offline. |
| Normal development | Open `app/index.html` in Chrome, Edge, Firefox or Safari. |
| Put it on a phone | See [03-android-build.md](03-android-build.md). |
| Put it on a Windows PC | See [04-windows-build.md](04-windows-build.md). |

Sign in with **{{OWNER_USER}} / {{OWNER_PASSWORD}}** (from your specification).
The other test accounts are listed in `README.md`.

## 2. Try the counter flow

1. Tap a few products. Long-press (or right-click) a product button to add five at once.
2. Type the quantity directly into the cart box — `50`, `Enter`. No clicking.
3. Press **Pay & Save**, pick a payment type, type the cash received, watch the change.
4. The queue card is assigned automatically; the Kitchen screen now shows the order.
5. On Kitchen press **Preparing → Ready → Served**. The card returns to the pool.

## 3. Connect Google Sheets when you are ready

Follow [01-google-sheets-setup.md](01-google-sheets-setup.md). Until you do, the app
runs fully offline and keeps records Pending on the device.

## 4. Make it yours

* Prices, products, branches, accounts: edit the specification JSON and re-run the
  generator, **or** edit them inside the app (Products & Menu / Accounts) and use
  **Export** in Settings to keep a copy.
* Colours, logo, business name: re-run the generator with a different
  `app.brandColor` and `app.businessName`.
* Which modules exist at all: `features` in the specification
  (`kitchen`, `queueCards`, `discounts`, `multiBranch`, `productVolume`, `unpaidOrders`).

## 5. Before you hand it to real staff

- [ ] Change every password in the app (Settings → My account).
- [ ] Replace `ACCESS_TOKEN` in `google-apps-script/Config.gs`.
- [ ] Delete the test-account rows you do not need.
- [ ] Run `python tools/test_app.py --spec spec.json` and confirm it passes.
- [ ] Do one real order, then check the `Orders` sheet row it produced.
