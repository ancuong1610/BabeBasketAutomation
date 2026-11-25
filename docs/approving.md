# Approving in Discord

How the owner reviews each post. Back to the [README](../README.md).

Two steps per post, so no paid AI image is made for a post you reject.

**1. Caption card** (the store's packshot, prices, rating, review summary, label texts, 4 captions):

- 1️⃣–4️⃣ to pick the caption (no number = caption 1)
- 🏪 🎨 🤲 🫧 to pick the image style before approving (see [Kiểu ảnh](#kiểu-ảnh))
- ✅ to approve, ❌ to reject
- 📷 to change the product photo before ✅. After the bot prompts you, send a photo, paste an image link,
  or type the shop photo number (for example, `2`). With several drafts waiting, reply to the prompt
  for the draft you want to change. For a shampoo and conditioner pair, add `xả` to change the
  conditioner photo. You can also reply directly to the caption card with a photo, link, or `ảnh 2`.
  If you already tapped ✅, the bot waits up to 10 minutes for the replacement.

**2. Image card**, sent in its own thread under the caption card on the next `check` (≤1 min).
The card shows the image version and the latest edit. When a new version arrives, the old card points to it.

- ✅ Queue the current version for posting.
- ❌ Drop the draft. This takes priority over other actions.
- ✍️ Type what to change in the thread, e.g. “sản phẩm to hơn, nền sáng hơn”. A written edit
  takes priority over ✅. Thread cards have no ✏️ (typing is the edit); old main-channel cards keep ✏️ as a reminder.
- 🔤 On K0, tap 🔤 and type the new words to reuse the same scene without an AI call.
  The first line is the title. Up to three more lines replace the points.
- 📷 Send an image to the thread to add it to the set. Use the photo picker to choose which
  supporting photos to publish.

Existing image cards without a thread and cards that fall back to the main channel use the reply flow.
The bot needs **Create Public Threads**, **Send Messages in Threads**, and **Message Content Intent**
(Discord Developer Portal → your app → Bot → Privileged Gateway Intents).

Publishing progress goes to the draft thread. If `[discord].posted_channel_id` is set, the bot sends one
summary to **#posted** with the caption choice, image version, and links or errors for each enabled
platform. A reply on the caption card points to **#posted**. The bot tries to archive the thread
after publishing or rejection.

Drafts move from `pending` to `approved` (caption), `image_pending`, then `ready` (image).
For thread drafts, the final status is `published` when every enabled platform succeeds or
`publish_failed` if any platform fails permanently.

### Kiểu ảnh

Tap one style emoji on the caption card before ✅. The approval reply confirms the style.
If you tap several, the bot uses the first one in this list.
On an older card, add a style emoji as a reaction yourself.

- 🏪 **K0 · In the store** (default) shows the product in a drugstore aisle with the title, points, and logo.
- 🎨 **K1 · Flat lay** shows the product on a pastel surface with a few small props.
- 🤲 **K3 · Close-up hand** shows the product held against a soft studio gradient.
- 🫧 **K4 · Texture** shows the product on a marble or pastel surface. A real shop photo of the contents is required for a swatch.

K1, K3, and K4 get the round logo only. The plain fallback image keeps the usual text and logo.

### Bộ ảnh của bài

Ảnh chính đứng đầu. Bot dùng quy tắc khớp đầu tiên trong `[[photos.rule]]` để chọn ảnh thật từ shop.

| Sản phẩm | Ảnh phụ theo thứ tự ưu tiên | Tổng ảnh tối đa |
|---|---|---|
| Bộ quà tặng | Ảnh gốc của shop, ảnh các món bên trong bộ | 3 |
| Đồ trang điểm | Màu trên môi, màu trên da, bảng màu, ảnh khi dùng, ảnh chất sản phẩm | 4 |
| Kem và các danh mục trong cấu hình | Ảnh chất sản phẩm | 2 |
| Nước hoa | Ảnh gốc của shop | 2 |
| Thực phẩm bổ sung | Ảnh nhãn thành phần, dinh dưỡng hoặc liều dùng | 2 |
| Cặp gội xả và các sản phẩm còn lại | — | 1 |

🏪 K0, 🎨 K1 và 🤲 K3 chỉ vẽ bao bì đóng. 🫧 K4 chỉ vẽ chất sản phẩm khi có ảnh thật của đúng sản phẩm
và đúng màu để đối chiếu. Khi thiếu ảnh thật, K4 chỉ vẽ sản phẩm trên nền marble hoặc pastel.

Bảng ảnh nằm ngay dưới thẻ ảnh. Bấm số của những ảnh bạn muốn đăng kèm hoặc bấm lại để bỏ chọn.
Khi không bấm số nào, bot dùng bộ ảnh đã tự chọn. Bấm 0️⃣ nếu chỉ muốn đăng ảnh chính.
Bấm 📷 để thêm ảnh của bạn, rồi kéo thả ảnh vào kênh sau khi bot nhắc. Bot cập nhật thẻ ảnh trong khoảng
1 phút. Kiểm tra bộ ảnh trên thẻ rồi bấm ✅.

### Old cards expire

A caption or image card that waits longer than `[discord] expire_after_days` (default 3) without a
decision is closed as `expired` on the next `check` and is no longer read; its thread is archived.
Approved and ready drafts never expire. Set it to 0 to turn this off.
