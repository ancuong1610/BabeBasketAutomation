# Publish approved drafts to Threads — design (approved 2026-11-18)

## Why
Close the loop: owner taps a caption number + ✅ on the Discord card, the post goes to Threads with no manual copy/paste.

## Goals / non-goals
- Approved draft → Threads post (post image + chosen caption) on the next `check` run (≤10 min); Discord reply with the post link; status `published`.
- Non-goals: Instagram, scheduled time slots, editing captions in Discord.
- Anti-goals: never post twice; never post without the owner's ✅; never print the token.

## Approaches
- **Discord CDN link as the image URL (picked):** the card already uploaded post.jpg; fetch the message fresh at publish time for a non-expired signed URL. No hosting.
- Upload to own hosting (S3/imgbb): more moving parts, another credential.
- Text-only posts: loses the image.

## Design
- `babebasket/threads.py`
  - `token()` — `data/threads_token.json` if present (refreshed token), else `THREADS_ACCESS_TOKEN` from `.env`.
  - `publish(text, image_url|None) -> {"id", "permalink"}` — `GET /me` → `POST /{uid}/threads` (IMAGE or TEXT) → poll container `status` until `FINISHED` (≤ ~60 s, `ERROR` raises) → `POST /{uid}/threads_publish` (no automatic retry) → `GET permalink`.
  - `maybe_refresh()` — when the saved token is older than 7 days, call `refresh_access_token` and save to `data/threads_token.json`.
  - Token goes only in query strings; errors already strip the query.
- `cli.cmd_publish(cfg, conn)` — runs at the end of `check`, and as `babebasket publish`.
  - Only when `[threads].enabled = true`.
  - For each `approved` draft: caption = captions[chosen_caption-1]; > 500 chars → mark failed with reason.
  - Image URL from the Discord card message (attachment or embed image); none → text post.
  - Success: payload `threads = {id, permalink, published_at}`, status `published`, Discord reply with link.
  - Failure: payload `publish_attempts += 1`, Discord ⚠️ reply with reason; after 3 attempts status `publish_failed`.
- Config: `[threads] enabled = false` until the first demo post is confirmed by the owner, then `true`.

## Test plan (TDD, network faked)
- publish: image flow calls create → status → publish → permalink in order; TEXT when no image; container ERROR raises; publish call not retried.
- cmd_publish: disabled → no calls; success → status published + reply with link; failure → attempts 1, still approved; 3rd failure → publish_failed; long caption → failed without API call.
