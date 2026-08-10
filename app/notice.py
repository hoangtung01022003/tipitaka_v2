"""Bảng thông báo hiện khi người dùng vào web.

Nội dung nằm trong `app/data/notice.json` chứ không nằm trong DB: schema Postgres do
dự án Next.js ở thư mục cha sở hữu (xem CLAUDE.md), nên thêm bảng ở đây là sai quy ước.
File JSON đi kèm repo nên `deploy_vps.bat` (git pull) là cập nhật được luôn.

`version` tăng lên mỗi lần admin sửa nội dung; trình duyệt nhớ version đã đóng nên
người đọc chỉ thấy lại thông báo khi nội dung thực sự thay đổi.
"""

import json
from pathlib import Path
from threading import Lock

from .i18n import LANGUAGES, normalize_language

DATA_DIR = Path(__file__).resolve().parent / "data"
NOTICE_FILE = DATA_DIR / "notice.json"
_WRITE_LOCK = Lock()

DEFAULT_NOTICE = {
    "enabled": True,
    "version": 1,
    "content": {
        "vi": {
            "title": "Lưu ý khi sử dụng",
            "body": (
                "Trang này tra cứu trực tiếp văn bản Pali của Tam Tạng.\n"
                "Bản dịch hiển thị kèm là do AI thực hiện, chưa được kiểm chứng, "
                "chỉ nên dùng để tham khảo và định hướng tra cứu.\n"
                "Khi cần trích dẫn chính thức, xin đối chiếu lại với bản Pali gốc và các bản dịch có thẩm quyền."
            ),
        },
        "en": {
            "title": "Please note",
            "body": (
                "This site searches the Pali text of the Tipiṭaka directly.\n"
                "The translations shown alongside are produced by AI and have not been verified; "
                "please treat them as a reading aid only.\n"
                "For formal citation, check against the original Pali and an authoritative translation."
            ),
        },
        "my": {
            "title": "သတိပြုရန်",
            "body": (
                "ဤဝဘ်ဆိုက်သည် ပိဋကတ်၏ ပါဠိစာသားကို တိုက်ရိုက် ရှာဖွေပေးသည်။\n"
                "ဘေးတွင်ပြသည့် ဘာသာပြန်များမှာ AI ဖြင့် ပြုလုပ်ထားပြီး အတည်ပြုထားခြင်း မရှိသေးပါ။ "
                "ကိုးကားရန်အတွက်သာ အသုံးပြုပါ။\n"
                "တရားဝင်ကိုးကားလိုပါက မူရင်းပါဠိနှင့် အာဏာရှိသော ဘာသာပြန်များနှင့် တိုက်ဆိုင်စစ်ဆေးပါ။"
            ),
        },
    },
}


def _read_raw() -> dict:
    if not NOTICE_FILE.exists():
        return dict(DEFAULT_NOTICE)
    try:
        data = json.loads(NOTICE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_NOTICE)
    if not isinstance(data, dict):
        return dict(DEFAULT_NOTICE)
    return data


def get_notice_config() -> dict:
    """Toàn bộ cấu hình thông báo, dùng cho trang admin."""
    data = _read_raw()
    content = data.get("content") if isinstance(data.get("content"), dict) else {}
    merged = {}
    for code in LANGUAGES:
        entry = content.get(code) if isinstance(content.get(code), dict) else {}
        fallback = DEFAULT_NOTICE["content"][code]
        merged[code] = {
            "title": str(entry.get("title") or fallback["title"]),
            "body": str(entry.get("body") or ""),
        }
    return {
        "enabled": bool(data.get("enabled", True)),
        "version": int(data.get("version") or 1),
        "content": merged,
    }


def get_notice(language: str) -> dict | None:
    """Thông báo cho một ngôn ngữ, hoặc None nếu đang tắt / không có nội dung."""
    config = get_notice_config()
    if not config["enabled"]:
        return None
    language = normalize_language(language)
    entry = config["content"].get(language) or {}
    body = str(entry.get("body") or "").strip()
    if not body:
        return None
    return {
        "version": config["version"],
        "title": str(entry.get("title") or "").strip(),
        "body": body,
    }


def save_notice(enabled: bool, content: dict[str, dict[str, str]]) -> dict:
    """Ghi lại nội dung thông báo và tăng version nếu nội dung thay đổi."""
    current = get_notice_config()
    normalized = {
        code: {
            "title": str((content.get(code) or {}).get("title") or "").strip(),
            "body": str((content.get(code) or {}).get("body") or "").strip(),
        }
        for code in LANGUAGES
    }
    changed = normalized != current["content"]
    version = current["version"] + 1 if changed else current["version"]

    payload = {"enabled": bool(enabled), "version": version, "content": normalized}
    with _WRITE_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        NOTICE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
