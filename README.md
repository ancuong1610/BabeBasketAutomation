# babe basket automation

Daily pipeline: scrape German drugstores → shortlist → LLM drafts in the shop's voice →
Discord approval cards → (next) publish to Instagram and Threads.
Nothing is published without an approval tap in Discord.

Python 3.11+; install runtime deps with `pip install -r requirements.txt`. The browser downloads its Chromium runtime on first launch.

## Commands

```sh
python3 -m babebasket scrape            # dm (+ Rossmann once configured) -> data/babebasket.db + CSV
python3 -m babebasket shortlist         # show today's picks with prices
python3 -m babebasket draft             # product details + reviews + LLM drafts for the shortlist
python3 -m babebasket post              # send the drafts to Discord as approval cards
python3 -m babebasket check             # read caption + image approvals, make images, publish ready posts
python3 -m babebasket daily             # scrape + draft + post
python3 -m babebasket try dm 3050111    # one product end to end, printed; no Discord, no DB
python3 -m unittest                     # tests, no network
```

## Approving in Discord

Two steps per post, so no paid AI image is made for a post you reject.

**1. Caption card** (the store's packshot, prices, rating, review summary, label texts, 4 captions):

- 1️⃣–4️⃣ to pick the caption (no number = caption 1)
- ✅ to approve, ❌ to reject
- 🖼️ the store photo is not good: **reply to the caption card** with a photo (or a link to one)
  before ✅. The AI image is then made from that photo; for a shampoo + conditioner pair write
  "xả" in the reply to replace the conditioner's photo. Free; the original link is kept.

**2. Image card**, sent as a reply under the caption card on the next `check` (≤1 min):

- ✅ **accept** → queued for posting
- ❌ **reject** → the post is dropped (wins over everything)
- ✏️ **edit** → **reply to the image card** with what to change, in Vietnamese or English
  ("sản phẩm to hơn, nền sáng hơn", "hand from the left", "less blur"). The AI edits *that*
  image as asked and keeps the packaging identical to the store photo, then sends a new card.
  Tapping ✏️ without replying just gets a reminder to reply. A reply wins over ✅.
- 🔤 **new words only** (free, no AI): tap 🔤 and reply with the text. First line = title,
  next lines = the points (up to 3); one line changes only the title. The same scene is
  lettered again and sent as a new card.

Replies need the bot's **Message Content Intent** switched on (Discord Developer Portal →
your app → Bot → Privileged Gateway Intents). Without it the bot says so under the card.

Status in the database: `pending` → `approved` (caption) → `image_pending` → `ready` (image) → `published`.

## Setup on the Mac mini

1. `git clone` this repo, `cd` into it.
2. `cp .env.example .env` and fill in:
   - `OPENROUTER_API_KEY`: openrouter.ai → Keys
   - `DISCORD_BOT_TOKEN`: Discord Developer Portal → your app → Bot → Reset Token
   - `DISCORD_CHANNEL_ID`: in Discord turn on Developer Mode, right-click the approval channel → Copy Channel ID
3. Put your own Discord user ID in `config.toml` → `[discord] owner_user_ids`, so only your taps count.
4. Run `./scripts/setup_mac.sh`: runs the tests, one LLM draft, and a Discord test message.

### n8n

n8n runs in Docker, so it reaches the Mac over SSH (`host.docker.internal`) and runs
`scripts/run.sh` there: it finds Python 3.11+, logs to `data/logs/`, and posts a ⚠️ alert to
the Discord channel if a run fails.

| File | Schedule (Europe/Berlin) | SSH command |
|---|---|---|
| `n8n/daily.json` | 07:00 daily | `daily`: scrape, draft, send cards |
| `n8n/check.json` | every minute, day and night | `check`: read approvals |

One-time setup (already done on the Mac mini):
1. System Settings → General → Sharing → Remote Login on.
2. A dedicated key `~/.ssh/n8n_babebasket`, listed in `~/.ssh/authorized_keys` as
   `restrict,command="~/BabeBasketAutomation/scripts/ssh_gate.sh" …`, so it can only run
   `daily` or `check` (no shell, no forwarding).
3. Import: `docker cp n8n/daily.json n8n-n8n-1:/tmp/ && docker exec n8n-n8n-1 n8n import:workflow --input=/tmp/daily.json`
   (same for `check.json`). The fixed workflow ids make re-imports update in place.
4. In n8n: Credentials → New → SSH Private Key, name `Mac mini (babebasket)`, host
   `host.docker.internal`, port 22, user = your Mac user, private key = contents of
   `~/.ssh/n8n_babebasket`. Select it in each workflow's SSH node, run once, then set Active.

## Post image

Made only after the caption is approved (step 2 above), as one finished image (1080×1350):

1. `scene.py` sends the dm/Rossmann packshot to a Gemini image model on OpenRouter
   (`[image] model` in `config.toml`), which returns a photo of a hand holding the product
   in a drugstore aisle. Any failure → `postimage.fallback_scene` (packshot on a cream background).
   Before that, the text model reads the packshot and writes down the packaging facts
   (container, opaque or transparent, colours and finish, cap/pump), which go into the image
   prompt as hard rules: iridescent bottles were coming out as clear plastic without them.
   The hand is a small young Asian woman's hand, and the product's amount from its name
   ("1.000 ml") sets its true size in the hand. After the image is made, the text model
   compares it with the packshot; if a customer would notice a difference, the image card
   says what differs ("⚠️ Có thể khác bao bì thật: …") so the owner can ✏️ or ❌.
2. `postimage.py` draws the text the LLM wrote in `labels` (title, hero, who_for, 2–3 points;
   `mode` = `usage` for hair/styling, `benefits` otherwise) as red boxes in Be Vietnam Pro,
   plus the logo from `assets/logo.jpg` (replace it with a larger file when available).
3. Saved as `data/images/draft-<id>-v<n>.jpg` and sent as the image card.

Cost control (`[image]` in `config.toml`): each AI image costs about $0.08. `max_per_post`
(default 3: the first image + two ✏️ edits) and `max_per_day` (default 30) cap the paid image requests; past a
cap the plain background is used and the card says so. `enabled = false` turns AI images off entirely.

Every OpenRouter call (caption, packaging, image, image_edit, fidelity, coupon) is stored in the
`ai_calls` table with its draft, tokens and the cost OpenRouter reports. See it with
`python3 -m babebasket costs` (last 7 days; `--days 30` for more).

Try one product: `.venv/bin/python -m babebasket try dm 3050111 --image`.
Needs `pip install -r requirements.txt` in `.venv` (Pillow); `run.sh` uses `.venv` automatically.

## What gets picked

Customers are Vietnamese girls and young women, from high school to about 35: students and
young workers buying self-care consumables. Each day 10 drafts, by group (`[groups]` in `config.toml`):

| Group | Per day | What | Minimum |
|---|---|---|---|
| hair | 5 | shampoo, conditioner, masks, leave-in, oils, styling | 4.2★, 30 reviews |
| body | 3 | body lotion/cream/oil, deo roll-on/stick, hand & foot care, shower gel, scrub, bath salt, intimate wash | 4.2★, 30 reviews |
| extra | 2 | sun care, skincare, beauty supplements (skin/hair/nails/figure), gift sets, men's products | 4.5★, 100 reviews |

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

It saves `rossmann_probe/report.json` and two sample pages. Commit that folder, and the
parser gets fitted to the real page structure. Then add category URLs to
`config.toml` → `[rossmann] categories`. The code keeps to Rossmann's robots.txt
(no search pages, no query strings except `?pageIndex=`).
