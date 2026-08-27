"""Nút "Cách tìm kiếm cho kết quả chuẩn xác" trên trang /help.

Cùng khuôn với `notice.py` (nội dung admin soạn, lưu trong `app/data/`, không phải DB -
schema Postgres do dự án Next.js ở thư mục cha sở hữu, xem CLAUDE.md) nhưng KHÁC ở chỗ
nội dung chỉ hiện khi người đọc chủ động bấm nút, không tự bật lên như bảng thông báo.

Không có cờ `enabled` như `notice.py`: nút tự ẩn cho một ngôn ngữ khi admin chưa nhập nội
dung (`body` rỗng) - không cần thêm một công tắc nữa cho việc đã có sẵn tín hiệu.

`version` tăng mỗi lần nội dung thực sự đổi, tuy hiện tại chưa dùng để nhớ trạng thái đã
xem (khác bảng thông báo, nút này không cần "chỉ hiện một lần") - giữ lại vì cùng khuôn
với `notice.py` và có thể cần sau này nếu muốn báo cho admin biết nội dung vừa đổi.
"""

import json
from pathlib import Path
from threading import Lock

from .i18n import LANGUAGES, normalize_language

DATA_DIR = Path(__file__).resolve().parent / "data"
SEARCH_TIPS_FILE = DATA_DIR / "search_tips.json"
_WRITE_LOCK = Lock()

DEFAULT_LABELS = {
    "vi": "Cách tìm kiếm cho kết quả chuẩn xác",
    "en": "How to search for accurate results",
    "my": "တိကျသော ရလဒ်ရရှိရန် ရှာဖွေနည်း",
}

DEFAULT_SEARCH_TIPS = {
    "version": 1,
    "content": {code: {"label": DEFAULT_LABELS[code], "body": ""} for code in LANGUAGES},
}


def _read_raw() -> dict:
    if not SEARCH_TIPS_FILE.exists():
        return dict(DEFAULT_SEARCH_TIPS)
    try:
        data = json.loads(SEARCH_TIPS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SEARCH_TIPS)
    if not isinstance(data, dict):
        return dict(DEFAULT_SEARCH_TIPS)
    return data


def get_search_tips_config() -> dict:
    """Toàn bộ cấu hình, dùng cho trang admin."""
    data = _read_raw()
    content = data.get("content") if isinstance(data.get("content"), dict) else {}
    merged = {}
    for code in LANGUAGES:
        entry = content.get(code) if isinstance(content.get(code), dict) else {}
        merged[code] = {
            "label": str(entry.get("label") or DEFAULT_LABELS[code]),
            "body": str(entry.get("body") or ""),
        }
    return {"version": int(data.get("version") or 1), "content": merged}


def get_search_tips(language: str) -> dict | None:
    """Nút + nội dung cho một ngôn ngữ, hoặc None nếu admin chưa soạn gì cho ngôn ngữ đó."""
    config = get_search_tips_config()
    language = normalize_language(language)
    entry = config["content"].get(language) or {}
    body = str(entry.get("body") or "").strip()
    if not body:
        return None
    return {
        "label": str(entry.get("label") or DEFAULT_LABELS[language]).strip(),
        "body": body,
    }


def save_search_tips(content: dict[str, dict[str, str]]) -> dict:
    """Ghi lại nội dung và tăng version nếu nội dung thay đổi."""
    current = get_search_tips_config()
    normalized = {
        code: {
            "label": str((content.get(code) or {}).get("label") or "").strip() or DEFAULT_LABELS[code],
            "body": str((content.get(code) or {}).get("body") or "").strip(),
        }
        for code in LANGUAGES
    }
    changed = normalized != current["content"]
    version = current["version"] + 1 if changed else current["version"]

    payload = {"version": version, "content": normalized}
    with _WRITE_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SEARCH_TIPS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
