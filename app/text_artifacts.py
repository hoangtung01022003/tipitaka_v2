"""Chuẩn hóa các ký tự vô hình/glyph riêng lọt ra từ nguồn PDF và HTML."""

from __future__ import annotations

from collections import Counter
import unicodedata


EXPLICIT_ARTIFACTS = {
    "\ufffd": "ký tự thay thế U+FFFD",
    "\ufeff": "BOM nằm giữa nội dung U+FEFF",
    "\u200b": "khoảng trắng zero-width U+200B",
}


def clean_unicode_artifacts(text: str) -> str:
    """Sửa đúng các hiện vật đã đối chiếu được với nguồn, không xóa đoán ký tự lạ.

    - U+FEFF nằm giữa ``bṛhaspati`` ở chú giải Sujato chỉ là BOM vô hình.
    - Font PDF Indacanda ánh xạ dấu chấm dưới của ``ṃ`` thành glyph riêng U+F01E;
      ảnh trang gốc xác nhận cụm đúng là ``pappoṭakojaṃ``.
    """
    return str(text or "").replace("\ufeff", "").replace("m\uf01e", "ṃ")


def unicode_artifacts(text: str) -> Counter[str]:
    """Thống kê ký tự chắc chắn không nên tồn tại trong nội dung tìm kiếm."""
    found: Counter[str] = Counter()
    for char in str(text or ""):
        if char in EXPLICIT_ARTIFACTS:
            found[EXPLICIT_ARTIFACTS[char]] += 1
            continue
        category = unicodedata.category(char)
        if category == "Co":
            found[f"glyph private-use U+{ord(char):04X}"] += 1
        elif category == "Cc" and char not in "\r\n\t":
            found[f"ký tự điều khiển U+{ord(char):04X}"] += 1
        elif (ord(char) & 0xFFFF) in {0xFFFE, 0xFFFF}:
            found[f"Unicode noncharacter U+{ord(char):04X}"] += 1
    return found
