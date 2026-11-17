"""Draft post content with an LLM via OpenRouter.

The LLM never writes prices: captions contain the literal placeholder {PRICE},
which code replaces with the computed sale price.
"""

from __future__ import annotations

import datetime as dt
import json
import re

from babebasket.config import secret
from babebasket.http import request_json

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """Bạn là người viết nội dung cho "babe basket", một shop online ở Hà Nội bán đồ chăm sóc bản thân \
được tuyển chọn từ các drugstore ở Đức (dm, Rossmann). Khách đặt trước, hàng về sau 3–4 tuần, cọc 50%.

Giọng văn caption (tiếng Việt):
- Nhẹ nhàng, thân mật như nói chuyện với bạn, vài emoji, dùng "nhé", "nhá", "nha", "nè".
- Gọi người đọc là "các babe" hoặc "các bà"; shop tự xưng "sốp" hoặc "tui".
- TUYỆT ĐỐI không dùng chữ "tiền"; nếu cần thì dùng "xèng".
- Không viết giá bằng số. Chỗ nào cần giá thì ghi đúng chuỗi {PRICE} (code sẽ thay bằng giá, ví dụ "340🐠"), \
  rồi tự thêm đơn vị và dung tích, ví dụ "{PRICE}/hũ 340ml" hoặc "{PRICE}/tuýp 75ml".
- Không dùng thuật ngữ marketing kiểu "Glow-Up Complex"; dịch công dụng sang tiếng Việt đơn giản, \
  không phóng đại ("giúp da trông sáng hơn").
- KHÔNG bịa trải nghiệm cá nhân (không viết "tui dùng rồi thấy..."). Có thể kể theo review của người mua ở Đức \
  ("review bên Đức khen...").
- Chỉ dựa vào công dụng do nhà sản xuất công bố và review được cung cấp. Thực phẩm chức năng/thuốc: chỉ nhắc \
  công dụng nhà sản xuất công bố, không hứa hẹn chữa bệnh.
- Nghĩ đến khách Việt: khí hậu nóng ẩm, nắng, nước cứng/clo, thói quen hằng ngày.
- Nhãn trên ảnh (labels) và caption chỉ bằng tiếng Việt, không để lại từ tiếng Đức (Keratin, Vitamin C… thì được).
- Thiên về bán hàng, kết bằng một lời rủ mua nhẹ nhàng (inbox/order sốp nha).

Câu mẫu đúng giọng của shop:
- "Lên lại cho các babe mấy em ủ tóc yêu thích của sốp 🥰 tuần ko dùng ít nhất 1 lần là ko chịu được 😌"
- "Ủa sao một tuýp kem tay mà làm mùi thơm cuốn thế này được nhỉ 😭💗 Tesori đúng là không làm tui thất vọng khoản mùi hương, chất kem dưỡng cũng tốt nữa 👌"
- "Đang vào mùa tặng quà mà chưa biết mua gì thì các bà tham khảo mấy set này nha 🎀"

Chỉ trả về JSON hợp lệ, không có chữ nào khác."""

USER_TEMPLATE = """Hôm nay là {today} (mùa ở Hà Nội: {season}). Đừng nhắc sai mùa.

Sản phẩm (dữ liệu từ {store}):
{product}

Review của người mua ở Đức (tiếng Đức):
{reviews}

Trả về JSON đúng cấu trúc sau:
{{
  "review_summary": "2–3 câu tiếng Việt tóm tắt review: khen gì, chê gì",
  "fit_for_vietnam": "1–2 câu: hợp với khách Việt nào, vì sao (khí hậu, thói quen)",
  "labels": {{
    "title": "tên ngắn trên ảnh, có tên hãng, tối đa 32 ký tự (vd 'Mixa Urea Cica Repair+')",
    "hero": "thành phần/con số nổi bật, tối đa 28 ký tự (vd '10% Urea & Amino acid'), hoặc \"\"",
    "who_for": "1 dòng 'Dành cho ...', tối đa 45 ký tự",
    "mode": "usage hoặc benefits",
    "points": ["2–3 dòng ngắn, mỗi dòng tối đa 35 ký tự"]
  }},
  "captions": [
    {{"style": "everyday story", "text": "..."}},
    {{"style": "curiosity hook", "text": "..."}},
    {{"style": "engagement question", "text": "..."}},
    {{"style": "direct sales", "text": "..."}}
  ]
}}
Chữ trên ảnh (labels): mode = "usage" cho đồ tóc, tạo kiểu, ủ, serum, mặt nạ (sản phẩm cần biết cách dùng): \
points = cách dùng ngắn + kết quả. mode = "benefits" cho kem đánh răng, dưỡng thể, sữa tắm, kem tay, \
thực phẩm chức năng…: points = công dụng/thành phần, không hướng dẫn cách dùng.
Bốn caption phải thật sự khác nhau. Caption "engagement question" là bài tương tác mềm, không bán trực tiếp. \
Mỗi caption 2–5 câu.{extra}"""

HANOI_SEASON = {
    1: "đông, lạnh khô", 2: "cuối đông, nồm ẩm", 3: "xuân, nồm ẩm", 4: "xuân, ấm dần", 5: "đầu hè, nóng",
    6: "hè, nắng nóng", 7: "hè, nắng nóng, mưa rào", 8: "hè, mưa nhiều", 9: "đầu thu", 10: "thu, hanh khô",
    11: "cuối thu, hanh khô, se lạnh", 12: "đông, lạnh khô",
}

NO_REVIEWS_NOTE = (
    "\nKHÔNG có review nào được cung cấp. review_summary phải ghi đúng: \"Chưa có review chi tiết, "
    "chỉ có điểm {rating}.\" TUYỆT ĐỐI không bịa người mua khen/chê gì, không viết \"khách khen\", "
    "\"review bên Đức khen\" trong caption."
)
NO_DETAILS_NOTE = (
    "\nKHÔNG có mô tả hay công dụng chính thức. Chỉ dùng những gì suy ra chắc chắn từ tên sản phẩm và loại sản phẩm; "
    "không bịa thành phần, con số hay công dụng đặc biệt (vd không tự suy 'tạo màu tóc' từ chữ 'Black'). "
    "hero để \"\" nếu không chắc."
)
NO_REVIEWS_WARNING = "no reviews or description given: check the summary and claims"

SUPPLEMENT_NOTE = (
    "\nĐây là thực phẩm chức năng: chỉ dùng công dụng nhà sản xuất công bố ở trên, "
    "không hứa hẹn tác dụng điều trị."
)


def is_supplement(detail: dict, product: dict) -> bool:
    text = " ".join(detail.get("breadcrumbs", [])) + " " + (product.get("categories") or "")
    return bool(re.search(r"Nahrungserg|Vitamin|Gesundheit|Arznei", text))


def build_messages(product: dict, detail: dict, reviews: list[dict]) -> list[dict]:
    info = {
        "brand": product["brand"],
        "name": product["name"],
        "category": " > ".join(detail.get("breadcrumbs", [])) or product.get("categories"),
        "official_claims": detail.get("claims", []),
        "description": detail.get("description", "")[:1200],
        "features": detail.get("features", []),
        "usage": detail.get("usage", "")[:400],
        "rating": f"{product['rating']:.1f}/5 from {product['review_count']} reviews",
    }
    review_lines = [
        f"- {r['rating']}★ {r['title']}: {r['text'][:500]} ({r['reviewer']})"
        for r in reviews
        if not r.get("incentivized")
    ][:12]
    extra = SUPPLEMENT_NOTE if is_supplement(detail, product) else ""
    if not review_lines:
        extra += NO_REVIEWS_NOTE.replace("{rating}", f"{product['rating']:.1f}/5 ({product['review_count']} lượt đánh giá)")
    if not (detail.get("description") or detail.get("claims") or detail.get("features")):
        extra += NO_DETAILS_NOTE
    today = dt.date.today()
    user = USER_TEMPLATE.format(
        today=today.strftime("%d/%m/%Y"),
        season=HANOI_SEASON[today.month],
        store=product["store"],
        product=json.dumps(info, ensure_ascii=False, indent=1),
        reviews="\n".join(review_lines) or "(không có review)",
        extra=extra,
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def parse_json_reply(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


def validate(draft: dict) -> list[str]:
    """Problems that must be fixed before the draft is usable."""
    problems = []
    for key in ("review_summary", "fit_for_vietnam", "labels", "captions"):
        if key not in draft:
            problems.append(f"missing {key}")
    captions = draft.get("captions", [])
    if len(captions) < 3:
        problems.append("fewer than 3 captions")
    for i, c in enumerate(captions, 1):
        text = c.get("text", "")
        if re.search(r"\btiền\b", text, re.I):
            problems.append(f"caption {i} uses 'tiền'")
        if re.search(r"\d+[.,]?\d*\s*(€|eur|đ|vnđ|vnd|k\b|nghìn|ngàn)", text, re.I):
            problems.append(f"caption {i} writes a price itself")
    return problems


LABEL_LIMITS = {"title": 32, "hero": 28, "who_for": 45, "point": 35}


def image_labels(draft: dict) -> dict:
    """The text for the post image, also for drafts made before title/hero/points existed."""
    labels = draft.get("labels") or {}
    if not isinstance(labels, dict):
        labels = {}
    points = labels.get("points") or labels.get("benefits") or []
    if not isinstance(points, list):
        points = [points]
    mode = labels.get("mode") if labels.get("mode") in ("usage", "benefits") else "benefits"
    return {
        "title": str(labels.get("title") or ""),
        "hero": str(labels.get("hero") or ""),
        "who_for": str(labels.get("who_for") or ""),
        "points": [str(x) for x in points if x][:3],
        "mode": mode,
    }


def warnings(draft: dict, had_reviews: bool = True) -> list[str]:
    """Soft issues shown on the Discord card."""
    out = [] if had_reviews else [NO_REVIEWS_WARNING]
    for i, c in enumerate(draft.get("captions", []), 1):
        if c.get("style") != "engagement question" and "{PRICE}" not in c.get("text", ""):
            out.append(f"caption {i} has no price")
    raw = draft.get("labels") if isinstance(draft.get("labels"), dict) else {}
    if raw.get("mode") not in ("usage", "benefits"):
        out.append("labels.mode must be benefits or usage")
    raw_points = raw.get("points") or raw.get("benefits") or []
    if not 2 <= len(raw_points if isinstance(raw_points, list) else [raw_points]) <= 3:
        out.append("labels.points must have 2–3 lines")
    if not raw.get("title"):
        out.append("image has no title")
    labels = image_labels(draft)
    lines = [(labels[k], LABEL_LIMITS[k]) for k in ("title", "hero", "who_for")]
    lines += [(p, LABEL_LIMITS["point"]) for p in labels["points"]]
    for line, limit in lines:
        if len(line) > limit:
            out.append(f"label too long for the image: {line[:30]}…")
    return out


def fill_price(draft: dict, price_text: str) -> dict:
    for c in draft.get("captions", []):
        c["text"] = c.get("text", "").replace("{PRICE}", price_text)
    return draft


def draft_post(product: dict, detail: dict, reviews: list[dict], cfg: dict, attempts: int = 2) -> dict:
    llm = cfg["llm"]
    messages = build_messages(product, detail, reviews)
    last_problems: list[str] = []
    for _ in range(attempts):
        reply = request_json(
            OPENROUTER_URL,
            method="POST",
            headers={
                "Authorization": f"Bearer {secret('OPENROUTER_API_KEY')}",
                "X-Title": "babe basket",
            },
            body={
                "model": llm["model"],
                "messages": messages,
                "temperature": llm.get("temperature", 0.8),
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        content = reply["choices"][0]["message"]["content"]
        try:
            draft = parse_json_reply(content)
        except (ValueError, json.JSONDecodeError):
            last_problems = ["reply was not valid JSON"]
            continue
        last_problems = validate(draft)
        if not last_problems:
            draft["_model"] = reply.get("model", llm["model"])
            draft["_usage"] = reply.get("usage", {})
            return draft
        messages = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": "Sửa các lỗi sau và trả lại JSON đầy đủ: " + "; ".join(last_problems)},
        ]
    raise RuntimeError(f"LLM draft failed: {'; '.join(last_problems)}")
