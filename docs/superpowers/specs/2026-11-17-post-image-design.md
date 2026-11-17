# Post image: one babe basket template per draft

Date: 2026-11-17 · Status: draft, waiting for approval

## Why
The approval card in Discord already has review summary, labels, captions and price.
The image is still dm's white packshot. The owner wants the card to show the exact
image that will be posted, so approving a draft = picking a caption + approving the
image, done. One format, one template; variety can come later.

## Goals
- Every draft gets one finished post image (1080×1350, Instagram 4:5) in the shop's style:
  - product held in a hand, German drugstore shelf blurred behind (as on @babe_.basket)
  - product sharp, label readable, product is the hero
  - red rounded text boxes with white text (as in the Mixa example): product name on top,
    key ingredient/number, "Dành cho …", 2–3 benefits **or** how-to-use steps
  - round beige babe basket logo sticker in a corner, never over the product
- Text on the image depends on the product type:
  hair / styling / treatment → how to use + result; toothpaste, cream, shower, supplements →
  why it's good, ingredients/doses; no "how to use" where nobody needs it.
- The Discord card shows this image instead of the packshot. Approval flow unchanged.

## Non-goals (for now)
- Multiple templates, carousels, video.
- Auto-publishing to Instagram/Threads (next step, separate).
- Using the shop's own phone photos.

## Anti-goals (failure even if it "works")
- Wrong or broken Vietnamese text on the image (missing accents, typos).
- A product that looks different from the real one (changed label, wrong color, wrong brand).
- Text boxes covering the product.

## Approaches
1. **Gemini makes the scene, our code adds the text — chosen.** Gemini image model gets the
   dm/Rossmann packshot and a fixed prompt ("a hand holding this exact product in a German
   drugstore aisle, product sharp, label unchanged, empty space at top and right").
   Pillow then draws the red boxes and the logo. Vietnamese text is exact because AI never writes it.
2. **No AI, code only** — packshot on a soft background + boxes + logo. Stable but looks like an ad.
   Used automatically as the **fallback** when Gemini fails or returns no image.
3. **Gemini draws everything incl. text** — rejected: Vietnamese diacritics come out wrong.

## Design
- **Text for the image**: extend the existing LLM draft. `labels` becomes:
  `title` (short product name), `hero` (key ingredient/number, optional), `who_for`,
  `points` (2–3 benefits or usage steps), `mode` = `benefits` | `usage`.
  The prompt gets the product's dm/Rossmann category and the rules above to choose `mode`.
  Existing length checks stay (≤35 chars per point).
- **Source image**: dm product detail `images[0]` (1200px zoom), Rossmann tile image.
- **Scene**: `google/gemini-3.1-flash-image` via OpenRouter (key already in .env),
  model name in `config.toml` so it can be switched to `gemini-3-pro-image` if quality is low.
- **Overlay** (Pillow): fixed layout — title box top center; hero/who_for/points stacked
  on the right and lower left like the Mixa example; red #E0262B-ish boxes, radius ~18px,
  white bold text; font **Be Vietnam Pro** (Google Fonts, OFL, full Vietnamese support)
  shipped in `assets/fonts/`. Logo `assets/logo.png` at ~180px, bottom-left.
- **Output**: `data/images/<draft_id>.jpg`; path stored with the draft.
- **Discord**: the card uploads that file as the embed image (multipart attachment).
- **Runtime**: Pillow added to `requirements.txt`; `run.sh` prefers `.venv/bin/python`.
- **Cost**: ~6 images/day with the flash image model: a few cents per day.

## Left to the builder
Exact box positions/sizes, prompt wording, retry count, how the fallback background looks.
