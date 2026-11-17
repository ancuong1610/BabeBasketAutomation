# Backlog

Parked work, not scheduled. Pick one up by name.

## Edit config from Discord (#configure)
- Status: design written, not approved, nothing built (parked 2026-11-17).
- Design: [superpowers/specs/2026-11-17-discord-configure-design.md](superpowers/specs/2026-11-17-discord-configure-design.md)
- Chosen approach: the 10-min `check` job reads #configure, validates, applies to `data/config.live.toml`, replies with the diff; `show` / `undo`; history in `data/config-history/`.
- Also moves shortlist constants and the Gemini image prompt into config.
- Owner steps when resumed: create #configure, enable Message Content Intent.

## Pricing rules
- Deferred by owner. Coupons today are fixed assumptions in `[coupons]` (dm 0%, Rossmann 10%).
- Ideas: capture Rossmann `price.strikethrough` / `promotionLabel` (sale price), flag dm "Nur Online" / "Nur im dm Markt" products.

## Shortlist improvements
- Skip products not seen in the latest crawl (`last_seen`).
- Balance dm vs Rossmann per day.
- Factor margin into the score.

## Smaller
- Higher-resolution logo (`assets/logo.jpg` is 150 px).
- Image deferred minors: unbreakable long words, malformed Gemini reply → no fallback, NFD diacritics, Gemini not always keeping the bottom 20% clear.
- Worst case ~6.5 min per draft vs the n8n SSH timeout.
