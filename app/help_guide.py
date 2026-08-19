""""Hướng dẫn và trợ giúp tìm kiếm" - nội dung admin soạn, người dùng đọc theo lô.

Lưu trong bảng `help_guide` (xem `db/migrations/007_help_feedback.sql`), MỘT dòng cho
mỗi ngôn ngữ. Admin viết nội dung KHÔNG giới hạn; trang người dùng chỉ xin một số lượng
nhỏ mỗi lần (mặc định `HELP_GUIDE_BATCH`) và cuộn tới đáy thì nạp thêm, để bài hướng
dẫn dài mấy chục nghìn ký tự vẫn không làm nặng trang đầu.

Phần thân lưu dạng văn bản trong một cột `body`. Nó được cắt thành "đoạn" ngay khi trả
về API: mỗi khối văn bản phân tách bởi dòng trắng là một mục trả về. Người dùng nhận
đúng thứ tự, admin không cần làm gì thêm để "chia chương" - văn bản họ soạn đã đủ cấu
trúc.

`updated_at` là cột để giao diện biết nội dung đã đổi: client giữ nó lại và so với lần
nạp trước (giống vai trò `version` của `notice.py`, nhưng cất trong DB thay vì JSON).
"""

from app.db import execute, fetch_all, fetch_one
from app.config import settings
from app.i18n import LANGUAGES, normalize_language

# Số mục trả về mỗi lần khi người dùng cuộn trang hướng dẫn.
HELP_GUIDE_BATCH = 20

DEFAULT_HEADINGS = {
    "vi": "Hướng dẫn và trợ giúp tìm kiếm",
    "en": "Search help & guide",
    "my": "ရှာဖွေခြင်းအတွက် အကူအညီနှင့် လမ်းညွှန်",
}

DEFAULT_BODIES = {
    "vi": (
        "Trang này tra cứu trực tiếp văn bản Pali của Tam Tạng cùng các bản dịch.\n\n"
        "Nếu quý vị nhập từ khóa không ra kết quả, xin thử các cách sau:\n"
        "1. Gõ ngắn gọn tên bài kinh (thí dụ: \"Bodhikatha\" thay vì cả câu hỏi).\n"
        "2. Bỏ dấu tiếng Việt hoặc bỏ dấu Pali sao cho chữ thuần Latin.\n"
        "3. Chọn đúng phạm vi: Chánh tạng, Chú giải, Phụ chú giải hay Ngoại điển.\n"
        "4. Nếu vẫn chưa ra, hãy dùng mục \"Góp ý và hỗ trợ tìm kiếm\" để nhờ trợ giúp."
    ),
    "en": (
        "This site searches the Pali text of the Tipiṭaka directly together with translations.\n\n"
        "If your keywords return no results, please try:\n"
        "1. Type the discourse name briefly (e.g. \"Bodhikatha\" instead of a full question).\n"
        "2. Drop diacritics so letters are plain Latin.\n"
        "3. Pick the right scope: canon, commentary, sub-commentary or extra-canonical.\n"
        "4. If it still fails, use \"Feedback & search help\" to ask for assistance."
    ),
    "my": (
        "ဤဆိုက်သည် ပါဠိကျမ်းစာများနှင့် ဘာသာပြန်များကို တိုက်ရိုက် ရှာဖွေပေးသည်။\n\n"
        "ရလဒ် မတွေ့ပါက အောက်ပါအတိုင်း စမ်းကြည့်ပါ:\n"
        "1. သုတ္တန်အမည်ကို တိုတိုရေးပါ (ဥပမာ \"Bodhikatha\")။\n"
        "2. ပါဠိအရေးအသားများကို ရိုးရိုး လက်တင်ဖြင့် ရိုက်ပါ။\n"
        "3. မှန်ကန်သော နယ်ပယ် ရွေးပါ။\n"
        "4. မရသေးပါက \"အကြံပြုချက်နှင့် ရှာဖွေမှု အကူအညီ\" ဖြင့် တောင်းပါ။"
    ),
}


def _load_row(language: str) -> dict | None:
    return fetch_one(
        "select language, heading, body, font_size, font_color, updated_at "
        "from help_guide where language = %s",
        [normalize_language(language)],
    )


def get_help_config() -> list[dict]:
    """Toàn bộ cấu hình cho trang admin - một mục cho mỗi ngôn ngữ."""
    rows = {r["language"]: r for r in fetch_all("select * from help_guide")}
    out = []
    for code in LANGUAGES:
        row = rows.get(code)
        out.append(
            {
                "language": code,
                "heading": str((row or {}).get("heading") or DEFAULT_HEADINGS.get(code, "")),
                "body": str((row or {}).get("body") or ""),
                "font_size": int((row or {}).get("font_size") or 16),
                "font_color": str((row or {}).get("font_color") or "#333333"),
                "updated_at": (row or {}).get("updated_at"),
            }
        )
    return out


def _split_paragraphs(body: str) -> list[str]:
    """Cắt văn bản thành các mục, phân tách bởi dòng trắng. Bỏ mục rỗng."""
    out = []
    for block in str(body or "").split("\n\n"):
        text = block.strip("\n").strip()
        if text:
            out.append(text)
    return out


def get_help_page(language: str, page: int = 1, per_page: int = HELP_GUIDE_BATCH) -> dict:
    """Một '"trang"' hướng dẫn cho người dùng.

    Trả về phần `items` là một / nhiều mục văn bản (0 nếu hết), kèm thông tin phân trang
    để client biết còn nạp tiếp hay không và hiện đúng heading / cỡ chữ / màu chữ.
    Không gộp các mục của nhiều trang: nếu `body` rỗng thì trả về mục rỗng.
    """
    language = normalize_language(language)
    row = _load_row(language)
    if not row:
        # Chưa có dữ liệu -> vẫn trả cấu trúc với nội dung trống, không lỗi.
        return {
            "language": language,
            "heading": DEFAULT_HEADINGS.get(language, ""),
            "font_size": 16,
            "font_color": "#333333",
            "has_content": False,
            "items": [],
            "page": page,
            "per_page": per_page,
            "total": 0,
            "has_more": False,
            "updated_at": None,
        }

    paragraphs = _split_paragraphs(row.get("body"))
    total = len(paragraphs)
    page = max(1, int(page or 1))
    start = (page - 1) * per_page
    items = paragraphs[start : start + per_page]
    return {
        "language": language,
        "heading": str(row.get("heading") or DEFAULT_HEADINGS.get(language, "")),
        "font_size": int(row.get("font_size") or 16),
        "font_color": str(row.get("font_color") or "#333333"),
        "has_content": bool(total),
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "has_more": start + len(items) < total,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
    }


def save_help(content: dict[str, dict]) -> None:
    """Ghi lại nội dung hướng dẫn cho từng ngôn ngữ (upsert theo `language`)."""
    now_sql = "now()"
    for code in LANGUAGES:
        entry = content.get(code) or {}
        heading = str(entry.get("heading") or "").strip()
        body = str(entry.get("body") or "")
        try:
            font_size = int(entry.get("font_size") or 16)
        except (TypeError, ValueError):
            font_size = 16
        font_size = max(8, min(72, font_size))
        font_color = str(entry.get("font_color") or "").strip() or "#333333"

        existing = _load_row(code)
        if existing:
            execute(
                "update help_guide set heading=%s, body=%s, font_size=%s, font_color=%s, "
                "updated_at=now() where language=%s",
                [heading, body, font_size, font_color, code],
            )
        else:
            execute(
                "insert into help_guide (language, heading, body, font_size, font_color, updated_at) "
                "values (%s, %s, %s, %s, %s, now())",
                [code, heading, body, font_size, font_color],
            )
