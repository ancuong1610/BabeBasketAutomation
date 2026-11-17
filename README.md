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
python3 -m babebasket check             # read your reactions: approve / reject / caption choice
python3 -m babebasket daily             # scrape + draft + post
python3 -m babebasket try dm 3050111    # one product end to end, printed; no Discord, no DB
python3 -m unittest                     # tests, no network
```

## Approving in Discord

Each card shows the photo, shelf price → cost → sale price, rating, the review summary,
the image label texts and 4 caption options. React:

- 1️⃣–4️⃣ to pick the caption (no number = caption 1)
- ✅ to approve, ❌ to reject

`check` picks up the decision and replies under the card. Approved drafts stay in the
database with status `approved`, ready for the publishing step.

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
| `n8n/check.json` | every 10 min, 07–23h | `check`: read approvals |

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

## Settings

All in `config.toml`: dm categories, shortlist size and thresholds, preferred brands,
the pricing rule (exchange rate, shipping, markup), standing coupon per store, LLM model,
Discord channel and owner.

Prices only ever come from the store data. The LLM writes `{PRICE}` and the code fills
in the computed sale price (e.g. `115🐠`). Captions using "tiền" or writing their own
prices are sent back to the LLM to fix.

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
