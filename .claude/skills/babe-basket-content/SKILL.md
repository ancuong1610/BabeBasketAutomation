---
name: babe-basket-content
description: Write or review Vietnamese social captions for Babe Basket (German drugstore beauty products for Vietnamese girls) in the shop's own voice. Use when drafting Threads/Instagram/Facebook captions, product-info comments, fragrance descriptions, or when changing the caption prompt in babebasket/llm.py.
---

# Babe Basket content voice

The source of truth is `docs/content-guide.md` (the owner's handover, v1.0, 2026-11-22). Read it in full
before writing; this file only says how to apply it here.

## Hard rules (win over the guide; enforced by `babebasket/llm.py` `validate()`)

- No weather, seasons or city names (customers live all over Vietnam; posts go out any time).
- No prices, "🐠", "k" or currency in captions; never the word "tiền" ("xèng" if needed).
- No selling words: order, đặt hàng, đặt trước, pre-order, hàng về, cọc, inbox, ib, nhắn sốp, ship, gom hàng,
  chốt đơn, giữ slot, còn hàng, số lượng có hạn, "3–4 tuần". Purchase info lives in the comment.
- Never "giảm cân / giảm mỡ / đốt mỡ / tan mỡ / giảm béo"; figure products are described indirectly.
- No invented personal experience, reviews, popularity, stock or discounts. German buyers' reviews may be
  quoted only when supplied ("review bên Đức khen…").
- At most one caption per draft opens with Bà nào / Mấy bà ơi / Tui mới phát hiện / Em này / Ai đang tìm /
  Không biết các bà.
- Captions end with a cute emoji, not a full stop.

## How to write

1. Identify the product exactly (brand, line, variant, size). Use only the label, the supplied description,
   official claims and reviews.
2. Pick the strongest angle: scent? an everyday situation? a real tip? humor? a question?
3. Write 4 options with 4 different strategies from the guide (A–J: relatable situation, curiosity hook,
   beauty tip, honest observation, playful humor, discussion starter, sensory story, shopping discovery,
   soft recommendation, gift idea; or a one-liner). Not the same set every time.
4. 1–3 short sentences each, 1–3 emojis, soft particles (nha, nhá, nè, í, ấy), "tui", "các bà", "các babe".
5. Run the guide's anti-repetition checklist (§11). If two options differ only by adjectives or emojis, redo.
6. Product-info comment only when asked: compact facts, "Giá: …", no pitch (§12).

## In this repo

- Prompt: `babebasket/llm.py` (`SYSTEM_PROMPT` + `system_prompt()` appends the guide; `USER_TEMPLATE`
  asks for the JSON with `captions[].style` = strategy name). Validator: `validate()`.
- Try one product end to end without Discord: `.venv/bin/python -m babebasket try dm <DAN>`.
- Tests: `.venv/bin/python -m unittest` (no network).
