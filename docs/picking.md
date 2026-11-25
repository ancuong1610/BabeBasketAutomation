# What gets picked, priced and posted

Product selection, pricing, coupons, settings and data sources. Back to the [README](../README.md).

## What gets picked

Customers are Vietnamese girls and young women, from high school to about 35: students and
young workers buying self-care consumables. Each day 11 drafts, by group (`[groups]` in `config.toml`):

| Group | Per day | What | Minimum |
|---|---|---|---|
| hair | 5 | shampoo, conditioner, masks, leave-in, oils, styling | 4.0★, 10 reviews |
| body | 3 | body lotion/cream/oil, deo roll-on/stick, hand & foot care, shower gel, scrub, bath salt, intimate wash | 4.0★, 10 reviews |
| extra | 2 | sun care, skincare, beauty supplements (skin/hair/nails/figure), gift sets, men's products | 4.5★, 100 reviews |
| makeup | 1 | coloured lip make-up: lipstick, lip gloss, lip oil/tint, lip liner | 4.0★, 10 reviews |

If a group has nothing good enough that day, the best of the others fills the gap.

Shampoo and conditioner are sold as a pair: when a shampoo (or conditioner) is picked, the
bot looks for its twin of the same brand and line ("Shampoo Traube Hydraboost" +
"Conditioner Traube Hydraboost"; dm lists them apart). The pair is one post: one caption
for the set, both bottles in the picture, combined shelf price on the card; the caption card
shows each bottle with its own link and photo. With no twin the product is sold on its own; if a
twin exists but cannot go out, neither is picked. 2in1 products never need one. Only a rinse-out
conditioner counts as a twin: express/spray conditioners, leave-ins, masks and treatments are
sold alone (a shampoo's line must match exactly, or differ only by harmless words).
At most 2 products per brand per day, and no product again within 45 days.

**Every ❌ is replaced.** When a caption or image card of today's list is rejected, the next
`check` drafts a replacement (same group first, never a product already offered today) and
posts its card, so the day keeps `[shortlist] per_day` posts. At most `refill_max_per_day`
replacements a day.

**Prices on the card** follow the shop's price sheet (`[pricing]`, `babebasket/pricing.py`):
shelf price minus coupon → VND at the buffered rate + cargo by weight + packaging + 5 % risk =
cost; suggested sale price = cost ÷ (1 − 5 % fee − 30 % margin), rounded up; plus CTV price
and deposit. The weight is estimated from the pack size in the name (marked "ước lượng"). The
card says whether a coupon or a store offer is already counted. Captions never show a price.

Never picked (`babebasket/catalog.py`):
- anything with a flammability or pressure warning (cannot be shipped to Vietnam). For dm this is
  read from the product page's warnings ("Extrem entzündbares Aerosol…"), so pump sprays stay
  in. Rossmann pages cannot be read, so Rossmann products named spray or mousse are skipped.
- dry shampoo, hair spray, accessories (brushes, sponges, hair ties), electrical devices, massage items
- perfume, except Jean & Len hair perfume
- supplements that are not for skin, hair, nails or figure

Price: most customers are students, so products over €10 rank lower and over €20 are skipped.
Gift sets may cost up to €40.

Captions for figure/weight products never use "giảm cân", "giảm mỡ", "đốt mỡ", "tan mỡ" or
"giảm béo" (Threads and Instagram cut their reach). They are written indirectly and stick
to the maker's claims.

## Coupons (#coupons)

App coupons sit behind the owner's login, so they are never scraped. Instead:

- Post a **screenshot** of the dm or Rossmann app's coupon tab in the Discord channel
  **#coupons** (one screenshot can hold many coupons), or type a short note such as
  `dm 20% Balea đến 30.11` or `Rossmann 10-fach Payback Haarpflege bis 24.11.`
- On the next `check` (≤1 min) the text model reads it, saves each coupon with its end date
  and replies with what it understood. ❌ on that reply deletes those coupons.
- While a coupon is valid, products it covers rank higher (`[shortlist] coupon_weight`:
  20 % → score ×1.4; PAYBACK "10-fach" counts as 5 %). Whole-purchase coupons don't change the
  ranking but lower the cost. The caption card shows "🎟️ Coupon … · đến 30.11" and the cost
  after the coupon. Expired coupons stop counting by themselves.

Rossmann's public offers pages (`[rossmann] offer_pages`: Angebote and Neu im Sortiment) are read
on every run that includes Rossmann. Each discounted product ("20% Sparen", struck-through old
price, "Aus der Werbung" = weekly flyer) becomes a 7-day boost, replaced by the next run. The
scraped price already includes the discount, so it is not subtracted again. "Neu" products
feed the daily new-product pick like any unrated product. To look for more offers pages:
`python3 -m babebasket.stores.rossmann probe-offers`.

## Settings

All in `config.toml`: product groups and their dm categories, shortlist thresholds, preferred brands,
the pricing rule (exchange rate, shipping, markup), standing coupon per store, LLM model,
Discord channel and owner.

Captions carry no price: they end by inviting customers to inbox the shop, and the owner
quotes prices privately. Captions that mention a price or use "tiền" are sent back to the
LLM to fix. The caption card still shows the store's shelf price (€) for the owner.

## Data sources

### dm

| Data | Endpoint |
|---|---|
| Category listing (price, rating, review count, images) | `product-search.services.dmtech.com/de/search/crawl?categoryNames=…` |
| Product detail (claims, usage, ingredients) | `products.dm.de/product/products/detail/DE/dan/{dan}` |
| Ratings, batched | `stars.services.dmtech.com/api/DE/v3/ratings/{dan,dan,…}/summary` |
| Review texts | `stars.services.dmtech.com/api/DE/v3/reviews/{dan}?sortation=relevance` |

These are the JSON endpoints dm.de's own pages call. dm rate-limits quickly, so the
scraper waits 10 s between categories and backs off on 429. Crawl approach after
[badlogic/heissepreise](https://github.com/badlogic/heissepreise) (MIT).

### Rossmann

Rossmann's robots.txt blocks Claude's crawlers, so this module was written without
looking at rossmann.de. Run the probe yourself once:

```sh
python3 -m babebasket.stores.rossmann probe --keyword haar
```

It saves `rossmann_probe/report.json` and two sample pages (both gitignored). The parser was fitted
to those pages on 2026-11-17; run the probe again only if Rossmann changes its page structure.
Category URLs go in `config.toml` → `[rossmann] categories`. An empty first page is retried once
after 20 s before the source counts as failed. The code keeps to Rossmann's robots.txt
(no search pages, no query strings except `?pageIndex=`).
