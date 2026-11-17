# Post Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every draft gets one finished 1080×1350 post image (hand + store scene by Gemini, red text boxes + logo by Pillow) and the Discord card shows that image.

**Architecture:** `llm.py` writes structured image text (`labels`). New `scene.py` asks a Gemini image model (OpenRouter) to put the packshot in a hand in a drugstore aisle. New `postimage.py` draws boxes + logo on that scene (or on a plain fallback background). `cli.cmd_draft` renders and stores the file; `discord.post_card` uploads it as the embed image.

**Tech Stack:** Python 3.11, Pillow, OpenRouter chat completions with `modalities: ["image","text"]`, Discord REST multipart upload, unittest.

**Spec:** `docs/superpowers/specs/2026-11-17-post-image-design.md`

## Global Constraints

- Output image: 1080×1350 JPEG, quality 90, `data/images/draft-<id>.jpg`.
- Scene model in `config.toml` `[image] model = "google/gemini-3.1-flash-image"`; switchable to `google/gemini-3-pro-image`.
- Vietnamese text on the image is drawn only by Pillow, never by the image model.
- Font: Be Vietnam Pro Bold (`assets/fonts/BeVietnamPro-Bold.ttf`, OFL). Logo: `assets/logo.jpg` (owner's 150×150 file; replace with a larger one when supplied).
- Box style: fill `#E0262B`, radius 18 px, white text, padding 18×12 px, max text width 520 px (wrap).
- Points ≤ 35 chars, `who_for` ≤ 45 chars, `title` ≤ 32 chars (existing limits style in `llm.warnings`).
- A draft is never blocked by image problems: scene failure → fallback background; render failure → card falls back to the packshot URL.
- Tests: `.venv/bin/python -m unittest -q` from repo root.

## Review Focus

- Gemini returns text but no image (or an error/timeout) → fallback background is used, draft still created. (Task 3 + 4 tests)
- Long Vietnamese line with diacritics (e.g. "Giúp tóc chắc khỏe, giảm gãy rụng rõ rệt") → wraps inside its box, no glyph boxes (□). (Task 2 test)
- Old drafts in the DB with the old `labels` shape (`benefits`, no `points`) → Discord card and renderer still work. (Task 1 + 2 tests)
- Packshot download fails (dm image URL 404) → no scene call, draft still created without image. (Task 4 test)
- Discord upload of a ~1 MB file → multipart body correct, embed image is `attachment://post.jpg`. (Task 5 test)

---

### Task 1: Image text in the LLM draft

**Files:**
- Modify: `babebasket/llm.py` (USER_TEMPLATE `labels`, `validate`, `warnings`, new `image_labels`)
- Modify: `babebasket/discord.py:35-50` (label text in card)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `llm.image_labels(draft: dict) -> dict` returning `{"title": str, "hero": str, "who_for": str, "points": list[str], "mode": "benefits"|"usage"}`; maps old shape (`benefits`) to `points`, `mode="benefits"`, `title=""`, `hero=""`.

- [ ] **Step 1: Failing tests** `test_image_labels_new_shape` (returns fields as given), `test_image_labels_old_shape` (`{"benefits":["a","b"],"who_for":"Dành cho x"}` → `points==["a","b"]`, `mode=="benefits"`), `test_validate_labels_mode` (`mode="other"` → problem `"labels.mode must be benefits or usage"`), `test_warnings_title_too_long` (33-char title → warning starting `"label too long for the image"`).
- [ ] **Step 2: Run, expect FAIL** — `.venv/bin/python -m unittest tests.test_pipeline -q`
- [ ] **Step 3: Implement.** USER_TEMPLATE `labels` becomes:
  `"title"` (tên ngắn ≤32 ký tự, có tên hãng), `"hero"` (thành phần/con số nổi bật ≤28 ký tự, hoặc ""), `"who_for"` ("Dành cho …" ≤45), `"mode"` ("usage" nếu là đồ tóc/tạo kiểu/ủ/serum/mặt nạ cần hướng dẫn dùng; "benefits" cho kem đánh răng, dưỡng thể, sữa tắm, thực phẩm chức năng…), `"points"` (2–3 dòng ≤35: cách dùng + kết quả nếu usage, công dụng nếu benefits). Add the product's categories/breadcrumbs to the product block in `build_messages` if not there. `validate` checks `mode` and 2–3 `points`; `warnings` checks lengths of title/hero/who_for/points. Discord card shows `title`, `hero`, points (▫️), `who_for` (👤) via `image_labels`.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `feat: structured image labels in drafts`

### Task 2: Overlay renderer + assets

**Files:**
- Create: `babebasket/postimage.py`, `assets/logo.jpg`, `assets/fonts/BeVietnamPro-Bold.ttf`, `assets/fonts/OFL.txt`
- Modify: `requirements.txt` (+ `Pillow`)
- Test: `tests/test_postimage.py`

**Interfaces:**
- Consumes: `llm.image_labels` output.
- Produces: `postimage.fallback_scene(packshot: bytes) -> PIL.Image.Image` (1080×1350, warm light gradient, packshot centered at ~70% height, soft shadow); `postimage.render(scene: PIL.Image.Image, labels: dict) -> PIL.Image.Image` (cover-crops scene to 1080×1350, draws boxes + logo); `postimage.save(img, path: Path) -> Path`.

- [ ] **Step 1: Failing tests** `test_render_size` (any scene → 1080×1350 RGB), `test_render_draws_red_boxes` (pixel at title box center ≈ `#E0262B` ±10), `test_long_vietnamese_line_wraps` (a 60-char Vietnamese point → box width ≤ 520+36), `test_font_has_vietnamese` (`font.getmask("ữ").getbbox()` not None), `test_old_labels_render` (old `benefits` shape renders without error), `test_fallback_scene_size`.
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement.** Layout (Mixa reference): title box centered top (y≈70); hero box right column (x right-aligned at 1080-48, y≈260); who_for box right column below hero; points stacked lower-left (x=48, from y≈930 upward-filling); logo 180×180 circle-masked at top-left (x=40,y=40) if title is centered, else bottom-right — pick top-left. Font sizes: title 46, others 36. Fetch font once (download needs owner OK — see Execution note).
- [ ] **Step 4: Run, expect PASS**; also render one sample to `data/images/sample.jpg` and look at it.
- [ ] **Step 5: Commit** `feat: post image overlay renderer`

### Task 3: Scene generation with Gemini

**Files:**
- Create: `babebasket/scene.py`
- Modify: `config.toml` (`[image] model`, `enabled = true`)
- Test: `tests/test_scene.py`

**Interfaces:**
- Produces: `scene.generate(packshot: bytes, product: dict, cfg: dict, post=request_json) -> bytes | None` — returns image bytes, or `None` on any failure (logs reason to stderr).
- `scene.PROMPT` (English): a hand holding this exact product, German drugstore aisle with shelves softly blurred, natural light, vertical 4:5 photo, product sharp and centered slightly left, label/colours/text unchanged, no added text/logos/watermarks, empty space at top and right.

- [ ] **Step 1: Failing tests** with a fake `post`: `test_returns_image_bytes` (reply `choices[0].message.images[0].image_url.url = "data:image/png;base64,<png>"` → bytes equal), `test_no_image_returns_none` (reply text only → None), `test_error_returns_none` (post raises RuntimeError → None), `test_request_shape` (body has `model` from cfg, `modalities == ["image","text"]`, user content has an `image_url` part with a `data:` URL of the packshot).
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement** via `request_json(OPENROUTER_URL, method="POST", ..., timeout=180, retries=1)`.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `feat: Gemini scene generation`

### Task 4: Render during drafting

**Files:**
- Modify: `babebasket/cli.py` (`cmd_draft`, `cmd_try` gets `--image`), `babebasket/db.py` (+ `set_payload`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `scene.generate`, `postimage.fallback_scene/render/save`, `llm.image_labels`.
- Produces: `cli.build_image(payload: dict, out: Path, cfg: dict, fetch_bytes=..., gen=scene.generate) -> str | None` (path or None); payload gains `"image_path"` and `"image_source": "gemini"|"fallback"`. `db.set_payload(conn, draft_id: int, payload: dict) -> None`.

- [ ] **Step 1: Failing tests** `test_build_image_uses_scene` (fake gen returns PNG → file exists, source "gemini"), `test_build_image_fallback_when_scene_none`, `test_build_image_packshot_download_fails` (fetch raises → returns None, gen never called), `test_set_payload_roundtrip`.
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**: in `cmd_draft`, after `add_draft`, call `build_image(... DATA/"images"/f"draft-{id}.jpg")`, then `set_payload`. Wrap in try/except so a crash only prints `! image for draft #id: …`. `try --image` writes `data/images/try-<product_id>.jpg` and prints the path.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `feat: render post image when drafting`

### Task 5: Discord card uploads the image

**Files:**
- Modify: `babebasket/http.py` (+ `request_multipart`), `babebasket/discord.py` (`build_card`, `post_card`), `babebasket/cli.py` (`cmd_post`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `http.request_multipart(url, *, fields: dict[str,str], files: list[tuple[str, str, bytes, str]], headers=None, timeout=60) -> dict` (field name, filename, data, content type); `discord.build_card(..., has_image: bool = False)` sets embed image `attachment://post.jpg` when true; `discord.post_card(channel_id, card, n_captions, image_path: str | None = None) -> str`.

- [ ] **Step 1: Failing tests** `test_card_uses_attachment_when_image`, `test_card_uses_packshot_without_image`, `test_multipart_body` (body contains `name="payload_json"` and `name="files[0]"; filename="post.jpg"`, boundary in Content-Type).
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**; `cmd_post` passes `payload.get("image_path")` only if the file exists.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `feat: Discord card shows the post image`

### Task 6: Runtime + real check

**Files:**
- Modify: `scripts/find_python.sh` (prefer `$PWD/.venv/bin/python` first), `README.md` (image section)

- [ ] **Step 1:** `.venv/bin/python -m pip install -r requirements.txt`; `./scripts/run.sh` picks `.venv/bin/python` (print `$PY` once to verify).
- [ ] **Step 2:** `.venv/bin/python -m babebasket try dm 3050111 --image` → open the JPEG, check: product unchanged, boxes readable, logo sharp enough, nothing covers the product. Iterate prompt/layout until good; try 2 more products (one toothpaste, one hair).
- [ ] **Step 3:** Full suite passes; commit `chore: run with .venv, document post images`; push only when the owner says so.

## Execution note
Downloading the Be Vietnam Pro font (Google Fonts GitHub, ~150 KB, OFL) and real Gemini calls (a few cents) need the owner's OK at execution time.
