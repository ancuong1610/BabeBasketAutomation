# Faster, clearer Discord review (edit menu, per-draft threads, #posted) — design

Status: **Approach A approved by the owner on 2026-11-24** ("Ok làm A nào"). #posted exists: channel id `1574691879352795156`. B (buttons/forms) stays a later option.

## Why
Owner, 2026-11-24 (summary): picking a caption and image style with emoji is fine. Editing is not:
- The edit options (add a photo, edit the AI image, change the words…) aren't visible when the first AI image arrives. The owner taps ✏️, then **waits** up to a minute for the bot to ask "reply with what to change", then replies.
- Cards, edits and confirmations all land in one channel. New cards push the old messages up, so the owner loses track of which post/version an edit belongs to and never sees a clear "done".
- Proposal: a separate **#posted** channel that confirms each post and lists the platforms it went to, so review and results are split and the owner stays in control.

Evidence from today (`data/logs/2026-11-24-check.log`): #94 got ✏️ at 08:14 with no reply text → the bot asked for one and is still waiting (`cli.py:1219-1224`). Publish confirmations are replies on the caption card (`cli.py:1359-1361`), in the same channel as new cards.

## How it works today (`babebasket/discord.py`, `cli.py`)
1. Caption card: 1️⃣–6️⃣ caption, 🏪🎨🤲🫧 style, ✅/❌. Reactions are read by `check` every minute (LaunchAgent).
2. After ✅: a new **image card** message (reply to the caption card) with ✅ ❌ ✏️ 🔤 and a photo picker.
3. ✏️ alone → bot asks for a reply → owner replies → next `check` makes a new image (max 3 AI tries per post).
4. Published → reply on the caption card with links.
There is no always-on bot process: everything is REST + polling.

## Goals
- G1. From the **first** AI image on, every edit action is visible on the card with one clear way to do it. No bot question in between.
- G2. Every edit is tied to one draft and shows its version: "Draft #94 · ảnh v2 · đã sửa: sản phẩm to hơn".
- G3. New cards never bury an open review: each draft's back-and-forth stays together.
- G4. A **#posted** channel: one message per published draft, with product, chosen caption, image version and a ✅/❌ per platform (Threads / Instagram / Facebook) with links or the failure reason.
- G5. Keep emoji for caption + style picking (owner is happy with it).

## Non-goals
- No change to captions, image generation, Gemini, publishing or the 07:00 `daily`.
- No auto-publish without the owner's ✅.

## Approaches
**A. Same polling, better layout (no new process).**
- Each draft gets its own **Discord thread** under its caption card. The image card, every edit version, replies and errors go into that thread. The main channel only shows new caption cards.
- The image card lists all actions up front: "✏️ reply = sửa ảnh AI · 🔤 reply = đổi chữ (miễn phí) · 📷 gửi ảnh = thêm ảnh · ✅ đăng · ❌ bỏ". Any **reply in the thread counts as the edit**; ✏️ isn't needed first. (Today a written reply already wins over ✅, `cli.py:1205`; ✏️ just stops being a required step.)
- Each new version edits the title: "v2 (đã sửa: …)" (free 🔤 text versions count too, so no "/max"; AI tries left is shown on the edit line), and the old version is marked "(cũ)".
- #posted channel as in G4.
- Cost: still ≤ 1 min between a tap and the bot reacting. Small change, reuses everything.

**B. Discord buttons, select menus and pop-up forms (an always-on bot).**
- Native Discord components: buttons "✏️ Sửa ảnh", "🔤 Đổi chữ", "📷 Thêm ảnh", "✅ Đăng", "❌ Bỏ"; a select menu for caption and style; ✏️/🔤 open a **form (modal)** with a text box right away. Instant feedback, visible only to the owner ("✅ Đã nhận, đang sửa ảnh v2…").
- Discord requires the bot to answer a button press within **3 seconds**, so polling every minute can't serve buttons. It needs a bot that stays connected to Discord all the time: one more LaunchAgent (e.g. `com.babebasket.bot`, like `check`), plus a small dependency (e.g. `discord.py`). The image work stays in `check`; the bot only records the tap/text into the DB and confirms instantly.
- Cost: a second always-on process to monitor; bigger change; the earlier `#configure` design rejected this for that reason.

**C. A now, B later (recommended).** A fixes G2–G4 and most of G1 with low risk this week. B adds instant buttons and forms on top of the same threads and #posted channel, once the owner has used A and still wants the speed.

## Design details (A, shared by B)
- **Thread per draft:** created on the caption card when it's posted (`POST /channels/{ch}/messages/{id}/threads`), name `#94 · L'ORÉAL Elvital Shampoo`. Stored in the draft payload (`thread_id`). `check` reads reactions and replies from the thread instead of the main channel. Closed (archived) after publish or ❌.
- **Version label:** `image_version` already exists (`image card v{version}`, `cli.py:916`); show it as `v{n}/{max}` with the last edit note.
- **#posted:** `[discord] posted_channel_id`. On publish: one embed per draft (thumbnail, product, caption number, image version, per-platform line `✅ Instagram <link>` / `❌ Facebook: <reason>`). A partial failure posts ❌ for that platform and links back to the draft's thread. The main-channel reply stays short ("✅ Đã đăng — xem #posted").
- **Old/legacy cards** without a thread keep working as today.

## Owner steps
- Create a **#posted** channel and send its ID (or let the bot create it if it has *Manage Channels*).
- Bot permissions: *Create Public Threads*, *Send Messages in Threads* (and *Manage Threads* to archive).
- For B only: approve one more always-on LaunchAgent.

## Decisions needed from the owner
1. Approach A, B or C?
2. Should the bot create #posted itself, or will you create it?
3. Thread per draft: yes/no?

## Verification plan
- Failing-first unit tests for: thread creation, reading replies from the thread, a reply counting as an edit without ✏️, version labels, the #posted message with mixed platform results.
- `.venv/bin/python -m unittest` green; one real draft end to end with `try` + a test channel before switching the live channel.

## Built

The first image card starts a thread on the caption card after caption approval. Image review,
photo selection, and publish progress use that thread. The card lists the actions and shows
the version. A replacement card marks the previous card as old. If Discord rejects thread
creation, the draft uses main-channel replies.

With `[discord].posted_channel_id` set, the finished draft gets one per-platform summary and a
short reply on the caption card. The bot attempts to archive its thread after the summary or
on rejection. A permanent platform failure sets a thread draft to `publish_failed`, including
when other platforms succeeded. Existing main-channel drafts keep their prior status rules.
