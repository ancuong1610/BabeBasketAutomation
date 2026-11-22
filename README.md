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
- 🏪 🎨 🤲 🫧 to pick the image style before approving (see [Kiểu ảnh](#kiểu-ảnh))
- ✅ to approve, ❌ to reject
- 🖼️ the store photo is not good: **reply to the caption card** with a photo (or a link to one)
  before ✅. The AI image is then made from that photo; for a shampoo + conditioner pair write
  "xả" in the reply to replace the conditioner's photo. Free; the original link is kept.
  The card shows up to 4 photos of the dm product page (repeats and photos dm marks as AI-made
  are dropped); reply "ảnh 2" (or "xả ảnh 2") to use one of them instead.

**2. Image card**, sent as a reply under the caption card on the next `check` (≤1 min):

- ✅ **accept** → queued for posting
- ❌ **reject** → the post is dropped (wins over everything)
- ✏️ **edit** → **reply to the image card** with what to change, in Vietnamese or English
  ("sản phẩm to hơn, nền sáng hơn", "hand from the left", "less blur"). The AI edits *that*
  image as asked and keeps the packaging identical to the store photo, then sends a new card.
  Tapping ✏️ without replying just gets a reminder to reply. A reply wins over ✅.
- 🔤 **new words on K0** (free, no AI): tap 🔤 and reply with the text. First line = title,
  next lines = the points (up to 3); one line changes only the title. The same scene is
  lettered again and sent as a new card.

Replies need the bot's **Message Content Intent** switched on (Discord Developer Portal →
your app → Bot → Privileged Gateway Intents). Without it the bot says so under the card.

Status in the database: `pending` → `approved` (caption) → `image_pending` → `ready` (image) → `published`.

### Kiểu ảnh

Tap one style emoji on the caption card before ✅. The approval reply confirms the style.
If you tap several, the bot uses the first one in this list.
On an older card, add a style emoji as a reaction yourself.

- 🏪 **K0 · In the store** (default) shows the product in a drugstore aisle with the title, points, and logo.
- 🎨 **K1 · Flat lay** shows the product on a pastel surface with a few small props.
- 🤲 **K3 · Close-up hand** shows the product held against a soft studio gradient.
- 🫧 **K4 · Texture** shows the product beside a swatch of its contents.

K1, K3, and K4 get the round logo only. The plain fallback image keeps the usual text and logo.

Most posts have one image. Products in the configured texture categories, such as creams and
lotions, can have two if the shop has a texture photo. Makeup can have up to four, including
three shop photos of the shade. The image card previews any extras under the main image.
Reply "không ảnh phụ" to post the main image alone.

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
| `n8n/check.json` | **inactive since 2026-11-22** | replaced by the LaunchAgent below |

`check` (every minute: read approvals, make images, publish) runs as a LaunchAgent in the
logged-in GUI session instead: `scripts/install_check_agent.sh` (`remove` to uninstall). Reason:
Chrome started over SSH cannot read the login keychain and loses the Gemini sign-in for good,
so `gemini_web` refuses to run over SSH. Do not reactivate the n8n check workflow while the
agent is installed. Status: `launchctl print gui/$(id -u)/com.babebasket.check`.

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
   (`[image] model` in `config.toml`), which returns a photo in the approved style.
   Any failure → `postimage.fallback_scene` (packshot on a cream background).
   Before that, the text model reads the packshot and writes down the packaging facts
   (container, opaque or transparent, colours and finish, cap/pump), which go into the image
   prompt as hard rules: iridescent bottles were coming out as clear plastic without them.
   In K0 and K3, the product's amount from its name ("1.000 ml") sets its true size relative
   to the hand. After the image is made, the text model
   compares it with the packshot; if a customer would notice a difference, the image card
   says what differs ("⚠️ Có thể khác bao bì thật: …") so the owner can ✏️ or ❌.
2. `postimage.py` adds the round logo from `assets/logo.jpg`. For K0 and the plain fallback,
   it also draws the text from `labels` in Be Vietnam Pro.
3. Saved as `data/images/draft-<id>-v<n>.jpg` and sent as the image card.

Cost control (`[image]` in `config.toml`): each AI image costs about $0.08. `max_per_post`
(default 3: the first image + two ✏️ edits) and `max_per_day` (default 30) cap the paid image requests; past a
cap the plain background is used and the card says so. `enabled = false` turns AI images off entirely.

Every OpenRouter call (caption, packaging, image, image_edit, fidelity, coupon) is stored in the
`ai_calls` table with its draft, tokens and the cost OpenRouter reports. See it with
`python3 -m babebasket costs` (last 7 days; `--days 30` for more).

Try one product: `.venv/bin/python -m babebasket try dm 3050111 --image`.
Needs `pip install -r requirements.txt` in `.venv` (Pillow); `run.sh` uses `.venv` automatically.

### Free images: Gemini in Chrome (`[image] backend = "gemini_web"`)

Instead of the paid OpenRouter image model, `babebasket/gemini_web.py` drives real Google Chrome
(Playwright, visible window) with the owner's Gemini Plus account: a new chat per image,
"Create image", the packshot uploaded, ratio 3:4 (1792×2400, cropped to 1080×1350 later),
the first image Gemini returns downloaded straight into memory (never via `~/Downloads`).
The packaging description and fidelity check still use the text model on OpenRouter.

1. Sign in once (owner only, by hand): `.venv/bin/python -m babebasket gemini-login`, sign in to
   Gemini in the Chrome that opens, then quit that Chrome with Cmd+Q. The profile lives in
   `~/.babebasket/gemini-profile`; it must not be open while the pipeline runs.
2. Test one image, no DB/Discord: `.venv/bin/python -m babebasket gemini-test data/gemini_test/serum.png`.

Safety: at most 30 images a day (`[image.gemini_web] daily_cap`, counted in
`~/.babebasket/gemini-daily-count.json`), a random 5–10 s gap between images. A sign-in page,
captcha or "unusual traffic" stops it: one ⚠️ on Discord, plain backgrounds until
`gemini-login` is run again. Chrome needs the Mac mini's GUI session to be logged in.
Automating the Gemini web app may break Google's terms; the account could be limited.
Switch back any time with `backend = "openrouter"`.

## What gets picked

Customers are Vietnamese girls and young women, from high school to about 35: students and
young workers buying self-care consumables. Each day 10 drafts, by group (`[groups]` in `config.toml`):

| Group | Per day | What | Minimum |
|---|---|---|---|
| hair | 5 | shampoo, conditioner, masks, leave-in, oils, styling | 4.0★, 10 reviews |
| body | 3 | body lotion/cream/oil, deo roll-on/stick, hand & foot care, shower gel, scrub, bath salt, intimate wash | 4.0★, 10 reviews |
| extra | 2 | sun care, skincare, beauty supplements (skin/hair/nails/figure), gift sets, men's products | 4.5★, 100 reviews |
| makeup | 1 | coloured lip make-up: lipstick, lip gloss, lip oil/tint, lip liner | 4.0★, 10 reviews |

**Makeup posts are carousels.** When a makeup caption is approved, one vision call labels the
product page's extra photos (lip/skin swatch, shade range, texture, application…) and picks up to
3 that show *this* shade (never a swatch of another shade). The image card shows them in a grid
under the AI image; they are posted unchanged after it, as a Threads carousel. Reply
"không ảnh phụ" to the image card to post the AI image alone.

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

It saves `rossmann_probe/report.json` and two sample pages. Commit that folder, and the
parser gets fitted to the real page structure. Then add category URLs to
`config.toml` → `[rossmann] categories`. The code keeps to Rossmann's robots.txt
(no search pages, no query strings except `?pageIndex=`).
