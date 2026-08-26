import re

file_path = "app/i18n.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Replace "⭐ Bài đã lưu" with "Lịch sử tìm kiếm"
content = content.replace('"nav.saved": "⭐ Bài đã lưu",', '"nav.saved": "Lịch sử tìm kiếm",\n        "nav.favorites": "Bài kinh yêu thích",')
content = content.replace('"nav.saved": "⭐ Saved Items",', '"nav.saved": "Search History",\n        "nav.favorites": "Favorite Suttas",')
content = content.replace('"nav.saved": "⭐ သိမ်းဆည်းထားသော မှတ်တမ်းများ",', '"nav.saved": "ရှာဖွေမှုမှတ်တမ်း",\n        "nav.favorites": "အနှစ်သက်ဆုံးသုတ်များ",')

# Also remove icon from "⭐ Lưu bài kinh yêu thích" -> "Lưu bài kinh yêu thích"
content = content.replace('"results.saveFavorite": "⭐ Lưu bài kinh yêu thích",', '"results.saveFavorite": "Lưu bài kinh yêu thích",')
content = content.replace('"results.saveFavorite": "⭐ Save Favorite Sutta",', '"results.saveFavorite": "Save Favorite Sutta",')
content = content.replace('"results.saveFavorite": "⭐ အနှစ်သက်ဆုံးသုတ်ကို သိမ်းဆည်းရန်",', '"results.saveFavorite": "အနှစ်သက်ဆုံးသုတ်ကို သိမ်းဆည်းရန်",')

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
