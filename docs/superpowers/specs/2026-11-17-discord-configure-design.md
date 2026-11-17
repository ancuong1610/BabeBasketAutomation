# Edit config from Discord (#configure) — design

## Why
The owner wants to change how the automation behaves (categories, shortlist rules, pricing, image prompt) from Discord, without SSH or git, while it stays fully automatic.

## Goals
- A `#configure` channel where the bot posts the current config as a `config.toml` attachment.
- Owner pastes (or attaches) a whole edited file → applied on the next `check` run (≤10 min), bot replies with what changed.
- Bad file → nothing changes, bot replies with the error (line number for TOML errors).
- `show` re-posts the current file; `undo` restores the previous version.
- Selection constants and the Gemini image prompt become config values.

## Non-goals
- No always-on bot, no new n8n workflow, no instant apply.
- No new pricing rules (deferred by owner).
- No git commit/push of Discord edits.

## Anti-goals
- A bad paste must never break the 07:00 run or lose the last good config.
- No one but the owner can change config; a paste must not be able to lock the owner out.

## Approach (picked: A)
- **A. `check` (every 10 min) also reads #configure** — picked: reuses the running job and Discord REST, nothing new to operate; cost is up to 10 min delay.
- B. Always-on gateway bot — instant, but a second long-running process to babysit.
- C. n8n Discord trigger — no reliable message trigger; ends up polling like A.

## Design
- **Live file:** `data/config.live.toml` (git-ignored). `config.load()` reads it if present, else `config.toml`. Repo `config.toml` stays the default; `git pull` never conflicts.
- **History:** every applied change first copies the current live file to `data/config-history/<UTC timestamp>.toml`. `undo` restores the newest history entry (and removes it), replies with the diff.
- **Channel:** `[discord] configure_channel_id`. `check` fetches messages after the last processed id (stored in `data/configure-state.json`), oldest first, ignores non-owners and the bot itself.
- **Message handling:** text `show` / `undo`; otherwise TOML from an attached `.toml` file or the message text (``` code fences stripped).
- **Validation** (all must pass, else reply ❌ + reason, no change):
  - parses as TOML;
  - required sections present: dm, rossmann, shortlist, pricing, coupons, llm, discord, image;
  - types: numbers where the current default is a number, lists where it's a list, strings where it's a string (checked against repo `config.toml`);
  - `[image].prompt` contains `{product}`;
  - protected keys equal to the current values: `discord.owner_user_ids`, `discord.configure_channel_id` (changing them only via the repo file).
- **Reply on success:** ✅ plus a short changed-keys list, e.g. `pricing.markup: 0.35 → 0.4`; list/long string changes shown as `changed`.
- **New config values** (defaults = today's hardcoded values, so behaviour is unchanged):
  - `[shortlist] prior_rating = 4.0, prior_weight = 50, score_floor = 3.5, brand_bonus = 1.15, max_per_brand = 2`
  - `[image] prompt = """…current scene.PROMPT…"""`
- **Failure isolation:** errors in the configure step are logged and replied if possible; they never stop approval checking.

## Owner steps (one time)
- Create `#configure`, give its channel id.
- Discord Developer Portal → Bot → turn on **Message Content Intent** (without it the bot sees empty messages and no attachments).

## Left to the builder
- Exact reply wording, diff formatting, module name (`babebasket/configure.py` suggested).
