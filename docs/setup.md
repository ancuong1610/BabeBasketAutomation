# Setup and scheduling

One-time setup on the Mac mini and how runs are scheduled. Back to the [README](../README.md).

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

Python 3.11+; install runtime deps with `pip install -r requirements.txt`. The browser downloads its Chromium runtime on first launch.

## Setup on the Mac mini

1. `git clone` this repo, `cd` into it.
2. `cp .env.example .env` and fill in:
   - `OPENROUTER_API_KEY`: openrouter.ai → Keys
   - `DISCORD_BOT_TOKEN`: Discord Developer Portal → your app → Bot → Reset Token
   - `DISCORD_CHANNEL_ID`: in Discord turn on Developer Mode, right-click the approval channel → Copy Channel ID
3. Put your own Discord user ID in `config.toml` → `[discord] owner_user_ids`, so only your taps count.
4. Run `./scripts/setup_mac.sh`: runs the tests, one LLM draft, and a Discord test message.

### Facebook & Instagram

Link an Instagram Business or Creator account to your Facebook Page. Give the Meta app the
`instagram_content_publish`, `instagram_basic`, `pages_manage_posts`, and `pages_read_engagement`
permissions for the accounts it will publish to.

- `META_PAGE_ACCESS_TOKEN` is the long-lived Facebook Page token shared by both publishers.
- `FACEBOOK_PAGE_ID` is the Facebook Page ID.
- `INSTAGRAM_USER_ID` is the linked Instagram professional account ID.

Set these values in `.env`, then set `enabled = true` under `[facebook]`, `[instagram]`, or both in
`config.toml`. Both are disabled by default. `[meta] graph_version` defaults to `v21.0`.

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
