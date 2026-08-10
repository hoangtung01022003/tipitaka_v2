"""Chuỗi giao diện cho tiếng Việt, tiếng Anh và tiếng Myanmar.

Ngôn ngữ đang chọn quyết định ba thứ:
- văn bản giao diện,
- ngôn ngữ đích khi dịch bằng AI (xem `translator.py`),
- nhãn của các tùy chọn nguồn bản dịch (xem `translation_sources.py`).
"""

DEFAULT_LANGUAGE = "vi"
LANGUAGES = ("vi", "en", "my")

LANGUAGE_NAMES = {
    "vi": "Tiếng Việt",
    "en": "English",
    "my": "မြန်မာ",
}

# Tên ngôn ngữ đích đưa vào prompt dịch của Gemini.
TRANSLATION_TARGETS = {
    "vi": "tiếng Việt",
    "en": "English",
    "my": "Burmese (Myanmar)",
}

STRINGS: dict[str, dict[str, str]] = {
    "vi": {
        "site.title": "Tipiṭaka AI Search",
        "site.eyebrow": "Tra cứu Pali bằng AI",
        "site.tagline": "Tìm kiếm kinh điển Pali, hiển thị văn bản Pali gốc, nguồn trích dẫn và bản dịch bằng AI",
        "search.label": "Câu hỏi cần tìm",
        "search.corpusQuestion": "Bạn muốn tìm kiếm bài kinh ở trong phần nào?",
        "search.pitakaQuestion": "Bạn muốn tìm kiếm ở trong Tạng nào?",
        "search.submit": "🔎 Tìm kiếm",
        "search.noticeChoose": "Hãy chọn phần dữ liệu và Tạng cần tìm.",
        "search.noticeChoosePitaka": "Hãy chọn Tạng cần tìm.",
        "search.noticeReady": "Đã chọn xong, có thể tìm kiếm.",
        "search.noticeNoPitaka": "Đã chọn: {label}.",
        "search.noticePitakaRequired": "Vui lòng chọn Tạng trước khi tìm.",
        "search.loadingTitle": "Đang tìm kiếm",
        "search.loadingText": "Đang tìm kiếm, vui lòng chờ trong ít phút.",
        "search.loadMore": "Hiển thị thêm 5 kết quả",
        "search.loadingMore": "Đang tải và dịch thêm...",
        "search.loadMoreFailed": "Không tải được kết quả tiếp theo.",
        "results.sourceTitle": "{rank}. Trích nguồn",
        "results.noSourcePath": "Chưa có source path",
        "results.contextAround": "Ngữ cảnh quanh đoạn",
        "results.paragraph": "Đoạn",
        "results.meta": "Rank {rank} · Score {score}",
        "results.originalTitle": "Trích đoạn bản gốc",
        "results.originalTitleExpanded": "Trích đoạn bản gốc / ngữ cảnh quanh đoạn",
        "results.translationTitle": "Dịch nghĩa",
        "results.translationLoading": "Đang dịch đoạn này, bản Pali và nguồn đã hiển thị trước.",
        "results.openSection": "Bạn có muốn xem toàn bộ bài kinh này?",
        "results.openSectionWith": "Xem toàn bộ bài kinh · {source}",
        "results.openSectionLoading": "Đang tải toàn bộ bài kinh...",
        "results.empty": "Chưa tìm thấy ứng viên trong phạm vi đã chọn",
        "results.emptyHint": "Hãy thử đổi Tạng hoặc mở rộng phạm vi tìm kiếm. Nếu đang tìm Chú giải/Phụ chú giải, hãy chọn đúng phần dữ liệu tương ứng.",
        "results.fallbackTitle": "Không tìm thấy kết quả cho đúng câu bạn nhập",
        "results.fallbackBody": "Hệ thống đã tự rút gọn từ khóa và tìm lại với «{query}». Kết quả dưới đây có thể rộng hơn ý bạn muốn.",
        "results.fallbackLadder": "Các bước đã thử: {steps}",
        "section.allTitle": "Toàn bộ bài kinh",
        "section.compareTitle": "Đối chiếu Pali - Bản dịch",
        "section.thisSutta": "Toàn bộ bài kinh này",
        "section.passageCount": "{count} đoạn trong mục này · Bản gốc Pali hiển thị trước, bản dịch được tải sau để không làm chậm popup.",
        "section.paliTitle": "Bản gốc Pali",
        "section.paliHint": "Văn bản Pali gốc của toàn bộ mục.",
        "section.translationTitle": "Bản dịch",
        "section.translationHint": "Các bài dài được chia theo đoạn/câu rồi ghép lại.",
        "section.officialHint": "Bản dịch của dịch giả, ghép từ các đoạn của mục này.",
        "section.translationLoadingFirst": "Đang dịch phần đầu tiên. Bạn có thể đọc bản Pali trước trong khi chờ.",
        "section.translatingPart": "Đang dịch phần {part}...",
        "section.translateNext": "Dịch tiếp phần {current}/{total}",
        "section.translateNextHint": "Các phần đã dịch được hiển thị ở trên. Bấm để dịch tiếp khi cần đọc thêm.",
        "section.translateDone": "Đã dịch xong toàn bộ mục này.",
        "section.loadingTitle": "Đang tải toàn bộ bài kinh",
        "section.loadingText": "Đang tải bản gốc và bản dịch, vui lòng chờ trong ít phút.",
        "section.loadFailed": "Không tải được bài kinh này.",
        "reader.expand": "Mở rộng",
        "reader.collapse": "Thu gọn",
        "translation.failed": "Chưa dịch được đoạn này. Vui lòng thử lại sau.",
        "translation.aiWarning": "Đây là bản dịch của AI, chưa có sự kiểm chứng.",
        "translation.sourceLabel": "Bản dịch",
        "translation.noOfficial": "Hiện không có bản dịch chính thức nào",
        "translation.officialTitle": "Bản dịch chính thức",
        "translation.aiTitle": "Bản dịch bằng AI",
        "translation.noData": " (chưa có dữ liệu)",
        # Nguon co ban dich cho bai kinh nhung khong co cho DUNG doan dang hien. Khoi
        # "Ban dich chinh thuc" xet theo tung DOAN, con nut nay xet theo CA BAI, nen hai
        # cho noi khac nhau la dung - phai noi ro pham vi, khong thi doc vao tuong mau thuan.
        "translation.elsewhereInSutta": " (có ở phần khác của bài)",
        "translation.aiOptionHint": "Nguồn này chưa được nạp. Hãy chọn nguồn khác để xem bản dịch.",
        "translation.wholeSutta": "(bản dịch cả bài kinh — khúc gần đúng với đoạn trên, có thể lệch)",
        "translation.missingList": "Chưa có bản dịch cho đoạn này: {sources}",
        "translation.wholeSuttaMore": "Bản dịch này gắn theo cả bài kinh nên chỉ cắt được khúc gần đúng — nếu thấy lệch, bấm “Xem toàn bộ bài kinh” bên dưới để đọc trọn bài.",
        "language.label": "Ngôn ngữ",
        "notice.close": "Đã hiểu",
        "notice.title": "Lưu ý khi sử dụng",
        "match.exactQuote": "Chứa nguyên văn đoạn bạn đã nhập.",
        "match.concept": "Khớp mạnh với nhóm thuật ngữ Pali trọng tâm và điều kiện ý nghĩa.",
        "match.keyword": "Có nhiều thuật ngữ Pali liên quan trực tiếp, đã được xếp hạng lại theo ngữ cảnh.",
        "match.semantic": "Có độ gần nghĩa vector và vượt ngưỡng lọc nhiễu.",
        "match.threshold": "Vượt ngưỡng lọc nhiễu theo điểm lexical/proximity.",
        "match.aiRerank": "Gemini rerank đánh giá đoạn này sát ý tìm kiếm.",
    },
    "en": {
        "site.title": "Tipiṭaka AI Search",
        "site.eyebrow": "AI-assisted Pali lookup",
        "site.tagline": "Search the Pali canon: original Pali text, citation source and AI translation",
        "search.label": "What are you looking for?",
        "search.corpusQuestion": "Which part of the canon do you want to search?",
        "search.pitakaQuestion": "Which Piṭaka do you want to search?",
        "search.submit": "🔎 Search",
        "search.noticeChoose": "Please choose a text collection and a Piṭaka.",
        "search.noticeChoosePitaka": "Please choose a Piṭaka.",
        "search.noticeReady": "Ready to search.",
        "search.noticeNoPitaka": "Selected: {label}.",
        "search.noticePitakaRequired": "Please choose a Piṭaka before searching.",
        "search.loadingTitle": "Searching",
        "search.loadingText": "Searching, this may take a moment.",
        "search.loadMore": "Show 5 more results",
        "search.loadingMore": "Loading and translating more...",
        "search.loadMoreFailed": "Could not load more results.",
        "results.sourceTitle": "{rank}. Citation",
        "results.noSourcePath": "No source path",
        "results.contextAround": "Context around passage",
        "results.paragraph": "Passage",
        "results.meta": "Rank {rank} · Score {score}",
        "results.originalTitle": "Original excerpt",
        "results.originalTitleExpanded": "Original excerpt / surrounding context",
        "results.translationTitle": "Translation",
        "results.translationLoading": "Translating this passage; the Pali text and source are already shown.",
        "results.openSection": "Would you like to read the whole discourse?",
        "results.openSectionWith": "Read the whole discourse · {source}",
        "results.openSectionLoading": "Loading the whole discourse...",
        "results.empty": "No candidate found within the selected scope",
        "results.emptyHint": "Try another Piṭaka or widen the search scope. If you are looking for commentary/sub-commentary, select the matching collection.",
        "results.fallbackTitle": "No result for exactly what you typed",
        "results.fallbackBody": "The system shortened your keywords and searched again with «{query}». The results below may be broader than intended.",
        "results.fallbackLadder": "Steps tried: {steps}",
        "section.allTitle": "Full discourse",
        "section.compareTitle": "Pali - translation side by side",
        "section.thisSutta": "This whole discourse",
        "section.passageCount": "{count} passages in this section · The Pali original is shown first; the translation loads afterwards so the popup stays fast.",
        "section.paliTitle": "Pali original",
        "section.paliHint": "The original Pali text of the whole section.",
        "section.translationTitle": "Translation",
        "section.translationHint": "Long sections are split into parts and joined back together.",
        "section.officialHint": "The translator's rendering, joined from this section's passages.",
        "section.translationLoadingFirst": "Translating the first part. You can read the Pali while waiting.",
        "section.translatingPart": "Translating part {part}...",
        "section.translateNext": "Translate part {current}/{total}",
        "section.translateNextHint": "Translated parts appear above. Press to continue when you need to read further.",
        "section.translateDone": "This section has been fully translated.",
        "section.loadingTitle": "Loading the whole discourse",
        "section.loadingText": "Loading the original and the translation, please wait a moment.",
        "section.loadFailed": "Could not load this discourse.",
        "reader.expand": "Expand",
        "reader.collapse": "Collapse",
        "translation.failed": "Could not translate this passage. Please try again later.",
        "translation.aiWarning": "This is an AI translation and has not been verified.",
        "translation.sourceLabel": "Translation",
        "translation.noOfficial": "No official translation is available",
        "translation.officialTitle": "Official translation",
        "translation.aiTitle": "AI translation",
        "translation.noData": " (not imported yet)",
        "translation.elsewhereInSutta": " (covers other parts of this discourse)",
        "translation.aiOptionHint": "This source has not been imported. Pick another source to see a translation.",
        "translation.wholeSutta": "(whole-discourse translation — approximate section, may be offset)",
        "translation.missingList": "No translation for this passage from: {sources}",
        "translation.wholeSuttaMore": "This translation is attached to the whole discourse, so only an approximate section can be cut — if it looks offset, use “Read the whole discourse” below.",
        "language.label": "Language",
        "notice.close": "Got it",
        "notice.title": "Please note",
        "match.exactQuote": "Contains verbatim the text you entered.",
        "match.concept": "Strong match on the core Pali terms and the meaning constraints.",
        "match.keyword": "Contains several directly related Pali terms; re-ranked by context.",
        "match.semantic": "Vector similarity match above the noise threshold.",
        "match.threshold": "Above the noise threshold on lexical/proximity score.",
        "match.aiRerank": "Gemini re-ranking judged this passage close to your query.",
    },
    "my": {
        "site.title": "Tipiṭaka AI Search",
        "site.eyebrow": "AI ဖြင့် ပါဠိစာပေ ရှာဖွေခြင်း",
        "site.tagline": "ပါဠိကျမ်းစာများကို ရှာဖွေပါ — မူရင်းပါဠိစာသား၊ ကိုးကားရင်းမြစ်နှင့် AI ဘာသာပြန်",
        "search.label": "ဘာရှာချင်ပါသလဲ",
        "search.corpusQuestion": "မည်သည့်ကျမ်းအပိုင်းတွင် ရှာလိုပါသလဲ",
        "search.pitakaQuestion": "မည်သည့်ပိဋကတ်တွင် ရှာလိုပါသလဲ",
        "search.submit": "🔎 ရှာဖွေရန်",
        "search.noticeChoose": "ကျမ်းအပိုင်းနှင့် ပိဋကတ်ကို ရွေးပါ။",
        "search.noticeChoosePitaka": "ပိဋကတ်ကို ရွေးပါ။",
        "search.noticeReady": "ရှာဖွေရန် အဆင်သင့်ဖြစ်ပါပြီ။",
        "search.noticeNoPitaka": "ရွေးထားသည် — {label}။",
        "search.noticePitakaRequired": "မရှာမီ ပိဋကတ်ကို ရွေးပါ။",
        "search.loadingTitle": "ရှာဖွေနေသည်",
        "search.loadingText": "ရှာဖွေနေပါသည်၊ ခဏစောင့်ပါ။",
        "search.loadMore": "နောက်ထပ် ၅ ခု ပြရန်",
        "search.loadingMore": "ထပ်မံ ရယူ၍ ဘာသာပြန်နေသည်...",
        "search.loadMoreFailed": "နောက်ထပ်ရလဒ်များ မရယူနိုင်ပါ။",
        "results.sourceTitle": "{rank}. ကိုးကားရင်းမြစ်",
        "results.noSourcePath": "ရင်းမြစ်လမ်းကြောင်း မရှိပါ",
        "results.contextAround": "အပိုဒ်ပတ်ဝန်းကျင် အကြောင်းအရာ",
        "results.paragraph": "အပိုဒ်",
        "results.meta": "အဆင့် {rank} · အမှတ် {score}",
        "results.originalTitle": "မူရင်းကောက်နုတ်ချက်",
        "results.originalTitleExpanded": "မူရင်းကောက်နုတ်ချက် / ပတ်ဝန်းကျင်အကြောင်းအရာ",
        "results.translationTitle": "ဘာသာပြန်",
        "results.translationLoading": "ဤအပိုဒ်ကို ဘာသာပြန်နေသည်။ ပါဠိစာသားနှင့် ရင်းမြစ်ကို ဦးစွာပြထားပါသည်။",
        "results.openSection": "ဤသုတ္တန်တစ်ခုလုံးကို ဖတ်လိုပါသလား",
        "results.openSectionWith": "သုတ္တန်အပြည့်အစုံ · {source}",
        "results.openSectionLoading": "သုတ္တန်တစ်ခုလုံးကို ရယူနေသည်...",
        "results.empty": "ရွေးထားသော နယ်ပယ်အတွင်း ရလဒ် မတွေ့ပါ",
        "results.emptyHint": "အခြားပိဋကတ်ကို စမ်းကြည့်ပါ သို့မဟုတ် ရှာဖွေမှုနယ်ပယ်ကို ချဲ့ပါ။ အဋ္ဌကထာ/ဋီကာ ရှာနေပါက သက်ဆိုင်ရာအပိုင်းကို ရွေးပါ။",
        "results.fallbackTitle": "ရိုက်ထည့်ထားသည်အတိုင်း ရလဒ် မတွေ့ပါ",
        "results.fallbackBody": "စနစ်က သော့ချက်စာလုံးကို တိုအောင်ပြုလုပ်၍ «{query}» ဖြင့် ပြန်ရှာခဲ့သည်။ အောက်ပါရလဒ်များသည် ပိုကျယ်ပြန့်နိုင်ပါသည်။",
        "results.fallbackLadder": "စမ်းသပ်ခဲ့သည့် အဆင့်များ — {steps}",
        "section.allTitle": "သုတ္တန် အပြည့်အစုံ",
        "section.compareTitle": "ပါဠိ - ဘာသာပြန် တွဲဖက်ပြခြင်း",
        "section.thisSutta": "ဤသုတ္တန် အပြည့်အစုံ",
        "section.passageCount": "ဤအပိုင်းတွင် အပိုဒ် {count} ခု · ပါဠိမူရင်းကို ဦးစွာပြပြီး ဘာသာပြန်ကို နောက်မှ ရယူသည်။",
        "section.paliTitle": "ပါဠိ မူရင်း",
        "section.paliHint": "အပိုင်းတစ်ခုလုံး၏ မူရင်းပါဠိစာသား။",
        "section.translationTitle": "ဘာသာပြန်",
        "section.translationHint": "ရှည်လျားသောအပိုင်းများကို ပိုင်းခြား၍ ပြန်လည်ပေါင်းစပ်ထားသည်။",
        "section.officialHint": "ကတိကြာင်းဗာသာပြန်။",
        "section.translationLoadingFirst": "ပထမပိုင်းကို ဘာသာပြန်နေသည်။ စောင့်နေစဉ် ပါဠိကို ဖတ်နိုင်ပါသည်။",
        "section.translatingPart": "အပိုင်း {part} ကို ဘာသာပြန်နေသည်...",
        "section.translateNext": "အပိုင်း {current}/{total} ကို ဘာသာပြန်ရန်",
        "section.translateNextHint": "ဘာသာပြန်ပြီးအပိုင်းများ အထက်တွင်ပေါ်ပါသည်။ ဆက်ဖတ်လိုပါက နှိပ်ပါ။",
        "section.translateDone": "ဤအပိုင်းကို အပြည့်အစုံ ဘာသာပြန်ပြီးပါပြီ။",
        "section.loadingTitle": "သုတ္တန်တစ်ခုလုံးကို ရယူနေသည်",
        "section.loadingText": "မူရင်းနှင့် ဘာသာပြန်ကို ရယူနေသည်၊ ခဏစောင့်ပါ။",
        "section.loadFailed": "ဤသုတ္တန်ကို မရယူနိုင်ပါ။",
        "reader.expand": "ချဲ့ရန်",
        "reader.collapse": "ခေါက်ရန်",
        "translation.failed": "ဤအပိုဒ်ကို ဘာသာမပြန်နိုင်ပါ။ နောက်မှ ထပ်စမ်းပါ။",
        "translation.aiWarning": "ဤသည်မှာ AI ဘာသာပြန်ဖြစ်ပြီး အတည်ပြုထားခြင်း မရှိသေးပါ။",
        "translation.sourceLabel": "ဘာသာပြန်",
        "translation.noOfficial": "တရားဝင်ဘာသာပြန် မရှိသေးပါ",
        "translation.officialTitle": "တရားဝင် ဘာသာပြန်",
        "translation.aiTitle": "AI ဘာသာပြန်",
        "translation.noData": " (ဒေတာ မရှိသေးပါ)",
        "translation.elsewhereInSutta": " (ဤသုတ္တန်၏ အခြားအပိုင်းများတွင် ရှိသည်)",
        "translation.aiOptionHint": "ဤရင်းမြစ်ကို မထည့်သွင်းရသေးပါ။ အခြားရင်းမြစ်ကို ရွေးပါ။",
        "translation.wholeSutta": "(သုတ္တန် တစ်ခုလုံး၏ ဘာသာပြန် — ခန့်မှန်း အပိုင်း၊ ရွေ့နေနိုင်သည်)",
        "translation.missingList": "ဤအပိုဒ်အတွက် ဘာသာပြန် မရှိသေးသည်များ - {sources}",
        "translation.wholeSuttaMore": "ဤဘာသာပြန်သည် သုတ္တန်တစ်ခုလုံးနှင့် တွဲထားသဖြင့် ခန့်မှန်းသာ ဖြတ်နိုင်သည် — ရွေ့နေပါက အောက်ရှိ “သုတ္တန် တစ်ခုလုံး ကြည့်ရန်” ကို နှိပ်ပါ။",
        "language.label": "ဘာသာစကား",
        "notice.close": "နားလည်ပါပြီ",
        "notice.title": "သတိပြုရန်",
        "match.exactQuote": "သင်ရိုက်ထည့်ထားသည့် စာသားအတိုင်း ပါဝင်သည်။",
        "match.concept": "အဓိက ပါဠိဝေါဟာရများနှင့် အဓိပ္ပာယ်အခြေအနေနှင့် အားကောင်းစွာ ကိုက်ညီသည်။",
        "match.keyword": "တိုက်ရိုက်ဆက်စပ်သော ပါဠိဝေါဟာရများစွာ ပါဝင်ပြီး အခြေအနေအလိုက် ပြန်လည်အဆင့်သတ်မှတ်ထားသည်။",
        "match.semantic": "အဓိပ္ပာယ်တူညီမှု အနီးစပ်ဆုံးဖြစ်ပြီး စစ်ထုတ်မှုအဆင့်ကို ကျော်လွန်သည်။",
        "match.threshold": "စကားလုံး/အနီးကပ်မှု အမှတ်အရ စစ်ထုတ်မှုအဆင့်ကို ကျော်လွန်သည်။",
        "match.aiRerank": "Gemini ပြန်လည်အဆင့်သတ်မှတ်မှုက ဤအပိုဒ်သည် ရှာဖွေမှုနှင့် နီးစပ်သည်ဟု ဆုံးဖြတ်သည်။",
    },
}

CORPUS_OPTIONS: dict[str, list[dict[str, str]]] = {
    "vi": [
        {"value": "all", "label": "Tìm kiếm tất cả", "description": "Toàn bộ kinh tạng, không giới hạn phần nào"},
        {"value": "mul", "label": "Tam Tạng", "description": "Tipiṭaka Mūla, gồm Tam tạng gốc"},
        {"value": "att", "label": "Chú giải", "description": "Aṭṭhakathā"},
        {"value": "tik", "label": "Phụ chú giải", "description": "Ṭīkā"},
        {"value": "nrf", "label": "Ngoại điển", "description": "Añña"},
    ],
    "en": [
        {"value": "all", "label": "Search everything", "description": "The whole canon, no collection filter"},
        {"value": "mul", "label": "Tipiṭaka", "description": "Tipiṭaka Mūla, the root canon"},
        {"value": "att", "label": "Commentary", "description": "Aṭṭhakathā"},
        {"value": "tik", "label": "Sub-commentary", "description": "Ṭīkā"},
        {"value": "nrf", "label": "Extra-canonical", "description": "Añña"},
    ],
    "my": [
        {"value": "all", "label": "အားလုံး ရှာရန်", "description": "ကျမ်းစာ တစ်ခုလုံး၊ အပိုင်းကန့်သတ်မှု မရှိ"},
        {"value": "mul", "label": "ပိဋကတ်သုံးပုံ", "description": "Tipiṭaka Mūla"},
        {"value": "att", "label": "အဋ္ဌကထာ", "description": "Aṭṭhakathā"},
        {"value": "tik", "label": "ဋီကာ", "description": "Ṭīkā"},
        {"value": "nrf", "label": "ကျမ်းပြင်ပ", "description": "Añña"},
    ],
}

PITAKA_OPTIONS: dict[str, list[dict[str, str]]] = {
    "vi": [
        {"value": "all", "label": "Tất cả các Tạng", "description": "Không giới hạn Tạng nào"},
        {"value": "vinaya", "label": "Tạng Luật", "description": "Vinayapiṭaka"},
        {"value": "sutta", "label": "Tạng Kinh", "description": "Suttapiṭaka"},
        {"value": "abhidhamma", "label": "Tạng Vi Diệu Pháp", "description": "Abhidhammapiṭaka"},
    ],
    "en": [
        {"value": "all", "label": "All Piṭakas", "description": "No Piṭaka filter"},
        {"value": "vinaya", "label": "Vinaya", "description": "Vinayapiṭaka"},
        {"value": "sutta", "label": "Suttas", "description": "Suttapiṭaka"},
        {"value": "abhidhamma", "label": "Abhidhamma", "description": "Abhidhammapiṭaka"},
    ],
    "my": [
        {"value": "all", "label": "ပိဋကတ် အားလုံး", "description": "ကန့်သတ်မှု မရှိ"},
        {"value": "vinaya", "label": "ဝိနယပိဋကတ်", "description": "Vinayapiṭaka"},
        {"value": "sutta", "label": "သုတ္တန်ပိဋကတ်", "description": "Suttapiṭaka"},
        {"value": "abhidhamma", "label": "အဘိဓမ္မာပိဋကတ်", "description": "Abhidhammapiṭaka"},
    ],
}


def normalize_language(value: str | None) -> str:
    candidate = str(value or "").strip().lower()[:2]
    return candidate if candidate in LANGUAGES else DEFAULT_LANGUAGE


def t(language: str, key: str, **kwargs: object) -> str:
    language = normalize_language(language)
    template = STRINGS.get(language, {}).get(key) or STRINGS[DEFAULT_LANGUAGE].get(key) or key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template


def corpus_options(language: str) -> list[dict[str, str]]:
    return CORPUS_OPTIONS.get(normalize_language(language), CORPUS_OPTIONS[DEFAULT_LANGUAGE])


def pitaka_options(language: str) -> list[dict[str, str]]:
    return PITAKA_OPTIONS.get(normalize_language(language), PITAKA_OPTIONS[DEFAULT_LANGUAGE])


def language_options() -> list[dict[str, str]]:
    return [{"value": code, "label": LANGUAGE_NAMES[code]} for code in LANGUAGES]


def ui_strings(language: str) -> dict[str, str]:
    """Toàn bộ chuỗi của một ngôn ngữ, để nhúng vào JS phía client."""
    language = normalize_language(language)
    merged = dict(STRINGS[DEFAULT_LANGUAGE])
    merged.update(STRINGS.get(language, {}))
    return merged
