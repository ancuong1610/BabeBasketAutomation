# babe basket automation

Daily pipeline: scrape German drugstores → shortlist → LLM drafts in the shop's voice →
Discord approval cards → (next) publish to Instagram and Threads.
Nothing is published without an approval tap in Discord.

Python 3.11+ standard library only: nothing to `pip install`.

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

`scripts/run.sh <command>` is what n8n calls: it finds Python, logs to `data/logs/`,
and posts a ⚠️ alert to the Discord channel if a run fails.

Import both workflows (n8n → Workflows → ⋯ → Import from File), then toggle them Active:

| File | Schedule (Europe/Berlin) | Runs |
|---|---|---|
| `n8n/daily.json` | 07:00 daily | `run.sh daily`: scrape, draft, send cards |
| `n8n/check.json` | every 10 min, 07–23h | `run.sh check`: read approvals |

They expect the repo at `~/BabeBasketAutomation`; edit the command in the node if it lives elsewhere.

Requirements:
- n8n installed natively (npm/Homebrew), so its Execute Command node can run Mac programs.
  If n8n runs in Docker, the command runs inside the container instead; use the SSH node to
  `host.docker.internal` (System Settings → General → Sharing → Remote Login) with the same command.
- n8n 2.0+ hides the Execute Command node by default. If it shows as unknown after import,
  start n8n with `NODES_EXCLUDE="[]" n8n start`.

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
