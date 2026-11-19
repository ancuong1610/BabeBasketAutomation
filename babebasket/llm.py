"""Draft post content with an LLM via OpenRouter.

Captions carry no price at all (the owner quotes prices in private messages);
a caption that mentions one is sent back to the LLM to fix.
"""

from __future__ import annotations

import json
import re

from babebasket.config import secret
from babebasket.http import request_json

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """Bạn là người viết nội dung cho "babe basket", một shop online bán đồ chăm sóc bản thân \
được tuyển chọn từ các drugstore ở Đức (dm, Rossmann). Thông tin mua hàng (giá, cọc, thời gian) sốp để ở comment, \
KHÔNG bao giờ nằm trong caption.

Khách hàng: con gái Việt Nam từ học sinh cấp 3 đến khoảng 30–35 tuổi (học sinh, sinh viên, người mới đi làm), \
quan tâm làm đẹp, dưỡng da, chăm tóc, chăm body. Viết cho đúng đối tượng này.

Giọng văn caption (tiếng Việt):
- Viết như một bạn gái thật đang đăng Threads kể cho bạn bè: câu ngắn, tự nhiên, nói như nói chuyện, \
  1–3 emoji. KHÔNG viết giọng quảng cáo hay giọng "content marketing".
- KHÔNG nhắc thời tiết, mùa (hanh khô, se lạnh, nồm, trời nóng…) hay tên thành phố (Hà Nội, Sài Gòn…): khách ở \
  khắp Việt Nam và bài đăng vào nhiều thời điểm, nhắc thời tiết dễ sai và nghe dập khuôn.
- Dùng "nhé", "nhá", "nha", "nè" vừa phải, không câu nào cũng có.
- Mọi caption kết thúc bằng một emoji dễ thương (🥰, 💖, 🫶, ✨, 😍, 🤍…), KHÔNG kết thúc bằng dấu chấm.
- Gọi người đọc là "các babe" hoặc "các bà"; shop tự xưng "sốp" hoặc "tui".
- TUYỆT ĐỐI không dùng chữ "tiền"; nếu cần thì dùng "xèng".
- KHÔNG ghi giá trong caption: không con số giá, không "🐠", không "k", không đơn vị tiền.
- Không dùng thuật ngữ marketing kiểu "Glow-Up Complex"; dịch công dụng sang tiếng Việt đơn giản, \
  không phóng đại ("giúp da trông sáng hơn").
- KHÔNG bịa trải nghiệm cá nhân (không viết "tui dùng rồi thấy..."). Có thể kể theo review của người mua ở Đức \
  ("review bên Đức khen...").
- Chỉ dựa vào công dụng do nhà sản xuất công bố và review được cung cấp. Thực phẩm chức năng/thuốc: chỉ nhắc \
  công dụng nhà sản xuất công bố, không hứa hẹn chữa bệnh.
- Nghĩ đến khách Việt khắp cả nước: nhu cầu và thói quen hằng ngày (tóc nhuộm/tẩy, da dầu/da khô, đi học, đi làm…).
- Sản phẩm hỗ trợ vóc dáng / kiểm soát cân nặng: viết thật khéo, TUYỆT ĐỐI không dùng các từ "giảm cân", \
  "giảm mỡ", "đốt mỡ", "tan mỡ", "giảm béo" (Threads/Instagram sẽ bóp tương tác). Nói gián tiếp, ví dụ \
  "giữ dáng", "cảm giác no lâu", "eo gọn hơn khi kết hợp ăn uống lành mạnh", và chỉ theo đúng công dụng \
  nhà sản xuất ghi. Không nhắm tới học sinh.
- Nhãn trên ảnh (labels) và caption chỉ bằng tiếng Việt, không để lại từ tiếng Đức (Keratin, Vitamin C… thì được).
- Caption CHỈ nói về sản phẩm: nó làm gì, review bên Đức nói gì, hợp với ai. KHÔNG kêu gọi mua và TUYỆT ĐỐI \
  không dùng các từ: order, đặt hàng, đặt trước, pre-order, hàng về, về hàng, cọc, inbox, ib, nhắn sốp, ship, \
  gom hàng, chốt đơn, giữ slot, còn hàng, số lượng có hạn, "3–4 tuần" (Threads bóp tương tác các bài bán hàng; \
  thông tin mua hàng nằm ở comment).

Câu mẫu đúng giọng của shop:
- "Lên lại cho các babe mấy em ủ tóc yêu thích của sốp 🥰 tuần ko dùng ít nhất 1 lần là ko chịu được 😌"
- "Ủa sao một tuýp kem tay mà làm mùi thơm cuốn thế này được nhỉ 😭💗 Tesori đúng là không làm tui thất vọng khoản mùi hương, chất kem dưỡng cũng tốt nữa 👌"
- "Đang vào mùa tặng quà mà chưa biết mua gì thì các bà tham khảo mấy set này nha 🎀"

Chỉ trả về JSON hợp lệ, không có chữ nào khác."""

USER_TEMPLATE = """Sản phẩm (dữ liệu từ {store}):
{product}

Review của người mua ở Đức (tiếng Đức):
{reviews}

Trả về JSON đúng cấu trúc sau:
{{
  "review_summary": "2–3 câu tiếng Việt tóm tắt review: khen gì, chê gì",
  "fit_for_vietnam": "1–2 câu: hợp với khách Việt nào, vì sao (nhu cầu, thói quen)",
  "labels": {{
    "kind": "skincare | body | hair | fragrance | oral | supplement | other",
    "title": "tên sản phẩm bằng tiếng Việt như người Việt hay gọi: loại sản phẩm + hãng + dòng, tối đa 34 ký tự (vd 'Kem dưỡng ẩm NIVEA Soft', 'Dầu dưỡng tóc Elvital Öl Magique'); giữ tên hãng/dòng gốc, dịch phần loại sản phẩm",
    "subtitle": "1 dòng: dành cho ai hoặc điểm nổi bật nhất, tối đa 40 ký tự",
    "points": ["2–3 dòng ngắn, mỗi dòng tối đa 36 ký tự, chọn theo kind (xem dưới)"]
  }},
  "captions": [
    {{"style": "everyday story", "text": "..."}},
    {{"style": "curiosity hook", "text": "..."}},
    {{"style": "one-liner", "text": "..."}},
    {{"style": "product highlights", "text": "..."}}
  ]
}}
Chữ trên ảnh (labels): lấy từ official_claims, description, features, usage ở trên; không bịa. points theo kind:
- skincare, body, oral: công dụng + thành phần chính (vd 'Cấp ẩm sâu tới 72 giờ', 'Vitamin E & dầu jojoba'); không ghi cách dùng.
- hair, dầu gội / dầu xả / bộ gội + xả: KHÔNG ghi cách dùng (ai cũng biết gội, xả thế nào). Ghi dành cho loại \
tóc nào, nhu cầu nào, dùng xong tóc thế nào (vd 'Cho tóc nhuộm, khô xơ', 'Tóc bóng mượt, bớt xù', 'Không sulfat, êm da đầu').
- hair, ủ tóc / xịt dưỡng / dầu dưỡng / serum: cách dùng ngắn + kết quả (vd 'Thoa lên tóc ẩm, không xả lại').
- fragrance (nước hoa, body mist): mô tả mùi: hương đầu / hương giữa / hương cuối nếu có, hoặc cảm giác mùi.
- supplement: công dụng nhà sản xuất công bố + liều dùng (vd '1 viên mỗi ngày').
Bốn caption phải thật sự khác nhau (sốp đăng xoay vòng cả bốn kiểu):
- "everyday story": một tình huống đời thường gần gũi dẫn tới sản phẩm, 2–4 câu.
- "curiosity hook": mở bằng một câu gây tò mò, rồi 1–3 câu giải thích vì sao.
- "one-liner": ĐÚNG MỘT câu ngắn, hút cực mạnh, tối đa khoảng 15 từ và 1 emoji, không giải thích thêm. \
Kiểu: "Ơ sao giờ mình mới biết đến em này 😭", "Mọi người biết cái này chưa?", "Sao cái này hay thế mà ít ai \
biết nhỉ", "Con này đỉnh thật sự luôn á 😮‍💨".
- "product highlights": nói thẳng 2–3 điểm mạnh nhất của sản phẩm, 2–4 câu.
Các caption (trừ one-liner) tối đa 450 ký tự (giới hạn của Threads là 500).{extra}"""

NO_REVIEWS_NOTE = (
    "\nKHÔNG có review nào được cung cấp. review_summary phải ghi đúng: \"Chưa có review chi tiết, "
    "chỉ có điểm {rating}.\" TUYỆT ĐỐI không bịa người mua khen/chê gì, không viết \"khách khen\", "
    "\"review bên Đức khen\" trong caption."
)
NO_DETAILS_NOTE = (
    "\nKHÔNG có mô tả hay công dụng chính thức. Chỉ dùng những gì suy ra chắc chắn từ tên sản phẩm và loại sản phẩm; "
    "không bịa thành phần, con số hay công dụng đặc biệt (vd không tự suy 'tạo màu tóc' từ chữ 'Black'). "
    "subtitle và points không ghi con số hay thành phần nếu không chắc."
)
NO_REVIEWS_WARNING = "no reviews or description given: check the summary and claims"

PAIR_NOTE = (
    "\nĐây là BỘ ĐÔI dầu gội + dầu xả cùng dòng, bán theo cặp (khách Việt quen gội và xả cùng một dòng). "
    "Caption và nhãn ảnh nói về cả bộ gội + xả, không chỉ một chai."
)

SUPPLEMENT_NOTE = (
    "\nĐây là thực phẩm chức năng: chỉ dùng công dụng nhà sản xuất công bố ở trên, "
    "không hứa hẹn tác dụng điều trị."
)


def is_supplement(detail: dict, product: dict) -> bool:
    text = " ".join(detail.get("breadcrumbs", [])) + " " + (product.get("categories") or "")
    return bool(re.search(r"Nahrungserg|Vitamin|Gesundheit|Arznei", text))


def _product_info(product: dict, detail: dict) -> dict:
    return {
        "brand": product["brand"],
        "name": product["name"],
        "category": " > ".join(detail.get("breadcrumbs", [])) or product.get("categories"),
        "official_claims": detail.get("claims", []),
        "description": detail.get("description", "")[:1200],
        "features": detail.get("features", []),
        "usage": detail.get("usage", "")[:400],
        "rating": (f"{product['rating']:.1f}/5 from {product['review_count']} reviews"
                   if product.get("review_count") and product.get("rating") is not None
                   else "no ratings yet (new product)"),
    }


def build_messages(product: dict, detail: dict, reviews: list[dict],
                   pair: dict | None = None, pair_detail: dict | None = None) -> list[dict]:
    info = _product_info(product, detail)
    if pair:
        info = {"bộ đôi bán theo cặp": [info, _product_info(pair, pair_detail or {})]}
    review_lines = [
        f"- {r['rating']}★ {r['title']}: {r['text'][:500]} ({r['reviewer']})"
        for r in reviews
        if not r.get("incentivized")
    ][:12]
    extra = SUPPLEMENT_NOTE if is_supplement(detail, product) else ""
    if pair:
        extra += PAIR_NOTE
    if not review_lines:
        rated = product.get("review_count") and product.get("rating") is not None
        extra += NO_REVIEWS_NOTE.replace(
            "{rating}", f"{product['rating']:.1f}/5 ({product['review_count']} lượt đánh giá)" if rated else "chưa có (sản phẩm mới)")
    if not (detail.get("description") or detail.get("claims") or detail.get("features")):
        extra += NO_DETAILS_NOTE
    user = USER_TEMPLATE.format(
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


# Selling words stay out of captions (Threads cuts the reach of sales posts); purchase info goes in the comment.
SALES_WORDS = re.compile(
    r"\border\b|pre-?order|đặt\s*hàng|đặt\s*trước|hàng\s*về|về\s*hàng|\bcọc\b|inbox|\bib\b|nhắn\s*(cho\s*)?sốp|"
    r"\bship\b|gom\s*hàng|chốt\s*đơn|giữ\s*slot|còn\s*hàng|số\s*lượng\s*có\s*hạn|3\s*[-–]\s*4\s*tuần", re.I)
ONE_LINER_MAX = 120
# Weather, seasons and city names: customers live all over Vietnam and posts go out any time of year.
WEATHER = re.compile(r"thời\s*tiết|hanh\s*khô|se\s*lạnh|nồm|trời\s*(nóng|lạnh|nắng|mưa|rét|hanh|oi)|"
                     r"mùa\s*(đông|hè|thu|xuân|lạnh|nóng|khô|mưa|hanh)|hà\s*nội|sài\s*gòn|tp\.?\s*hcm|đà\s*nẵng", re.I)

# Words Threads/Instagram punish: weight-loss products must be described indirectly.
SENSITIVE = re.compile(r"giảm\s*cân|giảm\s*mỡ|đốt\s*(cháy\s*)?mỡ|tan\s*mỡ|giảm\s*béo|weight\s*loss", re.I)


END_EMOJIS = ["🥰", "💖", "🫶", "✨", "😍", "🤍"]


def soften_endings(draft: dict) -> dict:
    """Captions end with a cute emoji, never with a full stop (a final '.' reads stiff on Threads)."""
    for i, c in enumerate(draft.get("captions") or []):
        text = (c.get("text") or "").rstrip()
        if text.endswith(".") and not text.endswith(".."):
            c["text"] = text[:-1].rstrip() + " " + END_EMOJIS[i % len(END_EMOJIS)]
    return draft


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
        sensitive = SENSITIVE.search(text)
        if sensitive:
            problems.append(f"caption {i} uses the sensitive word '{sensitive.group(0)}'; say it indirectly")
        sales = SALES_WORDS.search(text)
        if sales:
            problems.append(f"caption {i} uses the selling word '{sales.group(0)}'; captions only talk about "
                            "the product (purchase info goes in the comment)")
        weather = WEATHER.search(text)
        if weather:
            problems.append(f"caption {i} mentions '{weather.group(0)}'; no weather, seasons or city names")
        if c.get("style") == "one-liner" and (len(text) > ONE_LINER_MAX or len(re.findall(r"[.!?…]+\s+\S", text)) > 0):
            problems.append(f"caption {i} (one-liner) must be ONE short sentence")
        if "{PRICE}" in text or re.search(r"\d+[.,]?\d*\s*(€|🐠|(eur|đ|vnđ|vnd|k|nghìn|ngàn)\b)", text, re.I):
            problems.append(f"caption {i} mentions a price; captions must not contain prices")
    return problems


LABEL_LIMITS = {"title": 38, "subtitle": 44, "point": 40}  # prompt asks for 34/40/36; the image fits a bit more
KINDS = ("skincare", "body", "hair", "fragrance", "oral", "supplement", "other")


def image_labels(draft: dict) -> dict:
    """The text for the post image, also for drafts made before subtitle/kind existed (who_for/hero/mode)."""
    labels = draft.get("labels") or {}
    if not isinstance(labels, dict):
        labels = {}
    points = labels.get("points") or labels.get("benefits") or []
    if not isinstance(points, list):
        points = [points]
    subtitle = labels.get("subtitle") or labels.get("who_for") or labels.get("hero") or ""
    return {
        "title": str(labels.get("title") or ""),
        "subtitle": str(subtitle),
        "points": [str(x) for x in points if x][:3],
        "kind": labels.get("kind") if labels.get("kind") in KINDS else "other",
    }


def warnings(draft: dict, had_reviews: bool = True) -> list[str]:
    """Soft issues shown on the Discord card."""
    out = [] if had_reviews else [NO_REVIEWS_WARNING]
    for i, c in enumerate(draft.get("captions", []), 1):
        if len(c.get("text", "")) > 500:
            out.append(f"caption {i} is too long for Threads ({len(c.get('text', ''))}/500)")
    raw = draft.get("labels") if isinstance(draft.get("labels"), dict) else {}
    if raw.get("kind") not in KINDS:
        out.append(f"labels.kind must be one of {', '.join(KINDS)}")
    raw_points = raw.get("points") or raw.get("benefits") or []
    if not 2 <= len(raw_points if isinstance(raw_points, list) else [raw_points]) <= 3:
        out.append("labels.points must have 2–3 lines")
    if not raw.get("title"):
        out.append("image has no title")
    labels = image_labels(draft)
    lines = [(labels[k], LABEL_LIMITS[k]) for k in ("title", "subtitle")]
    lines += [(p, LABEL_LIMITS["point"]) for p in labels["points"]]
    for line, limit in lines:
        if len(line) > limit:
            out.append(f"label too long for the image: {line[:30]}…")
    return out


def draft_post(product: dict, detail: dict, reviews: list[dict], cfg: dict, attempts: int = 3,
               pair: dict | None = None, pair_detail: dict | None = None) -> dict:
    llm = cfg["llm"]
    messages = build_messages(product, detail, reviews, pair=pair, pair_detail=pair_detail)
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
            soften_endings(draft)
            draft["_model"] = reply.get("model", llm["model"])
            draft["_usage"] = reply.get("usage", {})
            return draft
        messages = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": "Sửa các lỗi sau và trả lại JSON đầy đủ: " + "; ".join(last_problems)},
        ]
    raise RuntimeError(f"LLM draft failed: {'; '.join(last_problems)}")
