# Extraction

## Adapters

| Adapter | Kind | Method | Extracts |
|---|---|---|---|
| `pricehistory_app` | price_history | static JSON-LD + optional headless render of the Chart.js chart | name, brand, product code, retailer, image, current & offer price, stated lowest/average/highest/MRP/lowest offer, selling & offer price history, sale bands |
| `generic_jsonld` | price_history | static schema.org Product JSON-LD | name, brand, codes, current price (no history) |
| `chart_page` | price_history | headless render + vendored chart reader | chart series mapped by `adapter_config.series_map`, sale bands, JSON-LD current price |
| `vendor_generic` | vendor | static (or `render_js`) JSON-LD + visible-text EMI patterns | vendor price, availability, seller, aggregate rating, reviews, EMI plan text (heuristic) |

`auto` picks a host-specific adapter (by `hosts`) or the generic one for the source kind.

Every adapter returns an `ExtractionResult` listing `fields_found` and `fields_missing`. Missing data
is never stored as 0. Run an inspection any time:

```bash
python scripts/test_source.py https://pricehistory.app/p/<slug> --config '{"respect_robots_for_subrequests": false}'
python scripts/test_source.py <vendor URL> --kind vendor
```

## pricehistory.app — inspection notes (2026-10-16)

* `robots.txt`: `Disallow: /api/`, `/redirect/`, `/images/*` sizes, `/cdn-cgi/`. Product pages `/p/...` are allowed.
* JSON-LD `@graph` contains `Product` with `offers` named **"Price"** and **"Offer Price"**, `offeredBy`
  (`flipkart.com`), `additionalProperty: Product Code`, and a description with
  `Lowest Price … Average Price … Highest Price … MRP …`. Visible text has `Lowest Offer: ₹…`.
* The chart (`#PriceHistoryChart`, Chart.js) is filled by JavaScript from `POST /api/price/<id>`;
  datasets "Prices" (step line) and "Offer Price" (scatter with nulls). Shaded bands = sale events.
* The page also shows a site-generated "chance that price will increase" — **not used** (unknown method).

### The robots.txt decision

The history data endpoint is under `/api/`, which robots.txt disallows. SmartBuy:

* never requests `/api/` directly with its HTTP client (the `SafeFetcher` enforces robots.txt);
* in the headless browser, blocks sub-requests to robots-disallowed paths of the page's host **by default**;
* lets an operator turn that off per source with `adapter_config.respect_robots_for_subrequests: false`,
  which reproduces what `data/extract_dashboard_timeseries.py` (and a normal visitor's browser) does.

`config/products.yaml` currently sets it to `false` for product 1 to keep the history complete, at most
once every 6 hours. If you set it to `true`, current prices and stated statistics are still collected and
history continues from your own observations and earlier imports.

## Vendor pages (inspected 2026-10-16 for the Olay Total Effects 50 g)

| Adapter | Site | Where the data is | Extracted |
|---|---|---|---|
| `amazon_in` | amazon.in/dp/ASIN | element ids: `#corePriceDisplay_desktop_feature_div .priceToPay`, `.basisPrice` (MRP), `#availability`, `#sellerProfileTriggerId`, `#deliveryBlockMessage`, `#vsxoffers_feature_div` (offer cards), `#acrPopover` / `#acrCustomerReviewText`, `[data-hook=review]` | price ₹449, MRP ₹999, seller, delivery, 3 offers, rating 4.3 (12,614), ~13 reviews with stars/date/verified |
| `myntra` | myntra.com/.../ID/buy | embedded JSON `window.__myx.pdpData` | price ₹669, MRP, seller, stock, discount end time, `flags.emiEnabled` (false), bank offers with min spend / caps, exchange-only returns, rating distribution, Myntra-selected top reviews |
| `nykaa` | nykaa.com / nykaaman.com /p/ID | embedded JSON `window.__PRELOADED_STATE__.productPage.product` | price ₹799, MRP, seller, stock, return policy, product expiry, rating distribution; recent reviews here had no text |
| `vendor_generic` | anything else | schema.org JSON-LD + EMI wording in visible text | price, rating, reviews, heuristic EMI text |

Notes from the inspection:

* All three pages work with plain HTTP (no JavaScript). Amazon rendered in a browser showed no extra EMI data.
* **EMI:** none of the three offers EMI for this ₹449–₹799 product. Myntra says so explicitly (`emiEnabled: false`,
  stored as `emi_status = not_offered`); Amazon and Nykaa show no EMI section (`not_shown`). The EMI calculator
  and comparison remain in place for higher-priced products.
* **Bank offers** are stored in `vendor_bank_offers`. A saving is estimated only when the terms on the page allow
  it (percentage, cap, minimum spend); "up to ₹X" offers whose conditions sit in a pop-up are shown as unknown.
  Offers whose minimum spend exceeds the price are marked not applicable.
* **Robots.txt:** the product paths used are allowed for all three sites.
* **Blocking:** after a few requests on 2026-10-16, Nykaa Man's CDN answered `403 Access Denied` to every request
  from this network (including robots.txt and browser user agents). This is respected: the vendor shows as
  unavailable, the failure is negative-cached for 15 minutes, and no evasion is attempted.
* Tracking parameters (`gclid`, `utm_*`, `ref=`, `dib`…) were removed from the registered URLs.

## Seller lists (seller discovery, inspected 2026-10-24)

| Adapter | Where | Notes |
|---|---|---|
| `amazon_in.extract_sellers` | `/dp/<ASIN>?aod=1` rendered in the headless browser; `#aod-pinned-offer` + `#aod-offer` cards | seller id from the `seller=` link, price (`.aok-offscreen`), MRP, ships from, seller rating / count / % positive, minimum order quantity, delivery text; in stock = an Add-to-Cart button. The panel opens only after the page's assets load, so images are loaded for this render (still address-checked). It loads more offers on scroll; on 2026-10-24 it showed 19 of 25 after scrolling (logged as a warning). `/gp/aod/ajax` returns 404 to plain HTTP. robots.txt allows `/dp/` and `/gp/offer-listing/`. |
| `myntra.extract_sellers` | same embedded `pdpData` as the product page | `sellers[]` + `sizes[].sizeSellerData` (price, units per seller) |
| `nykaa` | — | no seller list on the page (one seller); Nykaa Man still answers 403 to this network |

## Text on product images (seller discovery)

`extraction/image_text.py` reads text printed on product photos with **RapidOCR** (`rapidocr_onnxruntime` 1.4.4 — 1.3.x pins numpy < 2 and cannot be installed with this project —
Apache-2.0: PaddleOCR detection + recognition models converted to ONNX and bundled in the wheel, ~15 MB, CPU only,
~1–2 s per image, no network at run time). Used only when a candidate shop page's text has no pack size. Images
come from the page's own JSON-LD `image`; each is fetched through `SafeFetcher` with only the page host and that
image host allowed (public addresses, 5 MB limit, robots.txt), checked with Pillow (no non-images, no images over
16 MP) and read until a size is found (`DISCOVERY_MAX_IMAGES`, default 3; `DISCOVERY_READ_IMAGES=false` turns it off).
OCR digit fix: a letter O next to a unit is read as zero ("2Og" → "20g"). Live check 2026-10-24: Flipkart's Olay
listing has no size in its text; the first image reads "… SPF15 20g 20g" → rejected as a different pack size.

## Validation rules

* Prices: positive, finite, ≤ ₹10 crore; `₹`, `Rs.`, commas and Indian grouping handled.
* Timestamps: timezone-aware, after 2000-01-01, not more than one day in the future; date-only values are IST dates.
* Currency: ISO-4217 codes; symbols mapped (`₹` → INR).
* Chart `null`s are gaps; duplicate (price_type, timestamp) pairs keep the first.

## Security of outbound requests (`app/security/url_validation.py`)

* Only `http`/`https`, default ports, no `user:pass@`, no `localhost`/`*.internal`/metadata hostnames,
  no literal private/loopback/link-local IPs.
* Host must be on the allowlist (registered source hosts + `EXTRA_ALLOWED_HOSTS`).
* Every DNS answer must be public (blocks RFC1918, CGNAT, link-local incl. 169.254.169.254, ULA, multicast…).
* The connection is pinned to the validated IP (TLS still verified for the hostname) — prevents DNS rebinding.
* Redirects are followed manually; each hop is re-validated (scheme, allowlist, DNS).
* Response size (5 MB) and time limits; per-domain minimum interval (5 s); retries with exponential backoff
  for timeouts, 429 and 5xx only.
* Headless browser: every sub-request is checked for public addresses; images/media/fonts blocked; downloads
  and service workers disabled; page scripts run only inside the sandboxed browser.
