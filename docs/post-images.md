# Post images

How the post image is made and what it costs. Back to the [README](../README.md).

Made only after the caption is approved ([image card](approving.md), step 2), as one finished image (1080×1350):

1. `scene.py` sends the dm/Rossmann packshot to a Gemini image model on OpenRouter
   (`[image] model` in `config.toml`), which returns a photo in the approved style.
   Any failure → `postimage.fallback_scene` (packshot on a cream background).
   Before that, the text model reads the packshot and writes down the packaging facts
   (container, opaque or transparent, colours and finish, cap/pump), which go into the image
   prompt as hard rules: iridescent bottles were coming out as clear plastic without them.
   In K0 and K3, the product's amount from its name ("1.000 ml") sets its true size relative
   to the hand. After the image is made, the text model
   compares it with the packshot; if a customer would notice a difference, the image card
   says what differs ("⚠️ Có thể khác bao bì thật: …") so the owner can type a change in the thread or tap ❌.
2. `postimage.py` adds the round logo from `assets/logo.jpg`. For K0 and the plain fallback,
   it also draws the text from `labels` in Be Vietnam Pro.
3. Saved as `data/images/draft-<id>-v<n>.jpg` and sent as the image card.

Cost control (`[image]` in `config.toml`): each AI image costs about $0.08. `max_per_post`
(default 3: the first image + two typed edits) and `max_per_day` (default 30) cap the paid image requests; past a
cap the plain background is used and the card says so. `enabled = false` turns AI images off entirely.

Every OpenRouter call (caption, packaging, image, image_edit, fidelity, coupon) is stored in the
`ai_calls` table with its draft, tokens and the cost OpenRouter reports. See it with
`python3 -m babebasket costs` (last 7 days; `--days 30` for more).

Try one product: `.venv/bin/python -m babebasket try dm 3050111 --image`.
Needs `pip install -r requirements.txt` in `.venv` (Pillow); `run.sh` uses `.venv` automatically.

### Free images: Gemini in Chrome (`[image] backend = "gemini_web"`)

Instead of the paid OpenRouter image model, `babebasket/gemini_web.py` drives real Google Chrome
(Playwright, visible window) with the owner's Gemini Plus account: a new chat per image,
"Create image", the packshot uploaded, ratio 3:4 (1792×2400, cropped to 1080×1350 later),
the first image Gemini returns downloaded straight into memory (never via `~/Downloads`).
The packaging description and fidelity check still use the text model on OpenRouter.

1. Sign in once (owner only, by hand): `.venv/bin/python -m babebasket gemini-login`, sign in to
   Gemini in the Chrome that opens, then quit that Chrome with Cmd+Q. The profile lives in
   `~/.babebasket/gemini-profile`; it must not be open while the pipeline runs.
2. Test one image, no DB/Discord: `.venv/bin/python -m babebasket gemini-test data/gemini_test/serum.png`.

Safety: at most 30 images a day (`[image.gemini_web] daily_cap`, counted in
`~/.babebasket/gemini-daily-count.json`), a random 5–10 s gap between images. A sign-in page,
captcha or "unusual traffic" stops it: one ⚠️ on Discord, plain backgrounds until
`gemini-login` is run again. Chrome needs the Mac mini's GUI session to be logged in.
Automating the Gemini web app may break Google's terms; the account could be limited.
Switch back any time with `backend = "openrouter"`.
