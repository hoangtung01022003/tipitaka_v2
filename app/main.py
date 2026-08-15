import re
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import execute, fetch_all, fetch_one
from .i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    corpus_options,
    language_options,
    normalize_language,
    pitaka_options,
    t,
    ui_strings,
)
from .notice import get_notice, get_notice_config, save_notice
from .normalize import normalize_pali
from .search_engine import resolve_corpus_types, resolve_pitaka_type, search_passages, _display_source
from .translation_sources import (
    AI_SOURCE,
    SOURCE_ORDER,
    normalize_source,
    official_translations_merged,
    source_label,
    sources_for_sections,
    resolve_human_translation,
    unavailable_translation,
)
from .translator import public_translation_error, translate_passage, translate_text, translate_text_cached


APP_DIR = Path(__file__).resolve().parent
SECTION_TRANSLATION_MAX_CHARS = 18000
SECTION_TRANSLATION_CHUNK_CHARS = 12000
SECTION_TRANSLATION_STREAM_CHUNK_CHARS = 3600

app = FastAPI(title="Tipiṭaka Python Search")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.add_middleware(SessionMiddleware, secret_key=settings().get("secret_key", "default_secret_key"))
templates = Jinja2Templates(directory=APP_DIR / "templates")


GATHA_RENDS = {"gatha1", "gatha2", "gatha3", "gathalast"}
LANGUAGE_COOKIE = "lang"
LANGUAGE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
# Do dai cua so trich cua ban dich cap bai kinh trong the ket qua. Cua so nay TRUOT toi
# vi tri tuong doi cua doan dang xem (xem `_excerpt`), khong phai cat tu dau bai.
# Do tren cac moc kiem chung duoc, ti le trung khuc dich dung: 1.000 chu -> 31%,
# 2.000 -> 40%, 3.000 -> 56%, 5.000 -> 65%. Chon 2.000: gap doi muc cu theo yeu cau,
# van chua lap mat phan Pali. Khong co muc nao dat do tin cay that su - muon chinh xac
# thi phai ghep duoc cap doan, xem `align_minhchau.py`.
WHOLE_SUTTA_EXCERPT_CHARS = 2000

# Đơn vị đọc hoàn chỉnh. Kinh dùng hậu tố sutta/suttanta; Tiểu Bộ và Luật dùng
# những hậu tố khác. Khandhaka là đơn vị đọc hoàn chỉnh của phần Luật dạng chương;
# không nhận vagga/nipata vì chúng thường chỉ là nhóm chứa nhiều bài độc lập.
_READER_SUTTA_SUFFIXES = ("sutta", "suttam", "suttanta", "suttantam")
_READER_NUMBERED_SUFFIXES = (
    "jatakam",
    "apadanam",
    "gatha",
    "vatthu",
    "cariya",
    "vamso",
    "sikkhapadam",
    "parajikam",
    "khandhako",
    "khandhakam",
    "puccha",
)


# Số thứ tự in trong ngoặc ở CUỐI tiêu đề, ví dụ `543. Bhūridattajātakaṃ (6)` - số 6 là
# vị trí trong Mahānipāta, không phải một phần của tên bài. Nó che mất đuôi thật khiến
# `jatakam` không khớp, nên toàn bộ Jātaka bị coi là không có đơn vị đọc: đo được 4.885
# đoạn (2.717 ở s0514m + 2.168 ở s0513m) mở nút "Xem toàn bộ bài kinh" ra một mẩu.
#
# CLAUDE.md từng ghi giả thuyết "s0514m không có section cấp jātaka, chỉ có nipāta" và
# tự đánh dấu là chưa kiểm chứng. Giả thuyết đó SAI - các section ấy có đủ, chỉ bị luật
# tiêu đề loại oan.
#
# Bỏ đuôi này chỉ làm THÊM tiêu đề được nhận, không nới danh sách đuôi: `22. Mahānipāto
# (3)` sau khi bỏ vẫn kết thúc bằng `nipato` nên vẫn bị loại đúng như trước.
_READER_TRAILING_ORDINAL = re.compile(r"\s*\(\d+\)\s*$")


def _is_reader_unit_title(title: str) -> bool:
    raw = _READER_TRAILING_ORDINAL.sub("", str(title or "").strip())
    normalized = normalize_pali(raw).replace(" ", "")
    if normalized.endswith(_READER_SUTTA_SUFFIXES):
        return True
    return bool(re.match(r"^\(?\d", raw)) and normalized.endswith(_READER_NUMBERED_SUFFIXES)


def _source_path_is_prefix(candidate: object, selected: object) -> bool:
    left = [str(item) for item in candidate] if isinstance(candidate, (list, tuple)) else []
    right = [str(item) for item in selected] if isinstance(selected, (list, tuple)) else []
    return bool(left and len(left) <= len(right) and right[: len(left)] == left)


def _canonical_reader_section(section: dict) -> dict:
    """Đưa một mục con lên đơn vị đọc hoàn chỉnh gần nhất trong cây section.

    `passages.section_id` trỏ vào mục sâu nhất. Ví dụ đoạn 11 của DN 14 trỏ vào
    `Pubbenivāsapaṭisaṃyuttakathā` (4-35), trong khi bài kinh thật là
    `Mahāpadānasuttaṃ` (4-207). Dựa vào tiền tố `source_path` giúp loại các section
    bao trùm do XML nhiễu nhưng không phải tổ tiên thật.
    """
    candidates = fetch_all(
        """
        select id, document_id, title, source_path, start_sort_order,
               coalesce(end_sort_order, start_sort_order) as end_sort_order
        from sections
        where document_id = %s
          and start_sort_order <= %s
          and coalesce(end_sort_order, start_sort_order) >= %s
        order by coalesce(end_sort_order, start_sort_order) - start_sort_order,
                 cardinality(source_path) desc
        """,
        [section["document_id"], section["start_sort_order"], section["end_sort_order"]],
    )
    valid = [
        row
        for row in candidates
        if _is_reader_unit_title(str(row.get("title") or ""))
        and _source_path_is_prefix(row.get("source_path"), section.get("source_path"))
    ]
    return valid[0] if valid else section


def _reader_section_by_id(section_id: str) -> dict | None:
    section = fetch_one(
        """
        select id, document_id, title, source_path, start_sort_order,
               coalesce(end_sort_order, start_sort_order) as end_sort_order
        from sections where id = %s
        """,
        [section_id],
    )
    return _canonical_reader_section(section) if section else None


def _language_from_header(header: str) -> str | None:
    for part in header.split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGUAGES:
            return code
    return None


def request_language(request: Request, override: str | None = None) -> str:
    """Ngôn ngữ giao diện: tham số của request > cookie > Accept-Language > tiếng Việt."""
    if override and str(override).strip().lower()[:2] in LANGUAGES:
        return normalize_language(override)
    cookie = request.cookies.get(LANGUAGE_COOKIE)
    if cookie and str(cookie).strip().lower()[:2] in LANGUAGES:
        return normalize_language(cookie)
    return _language_from_header(request.headers.get("accept-language", "")) or DEFAULT_LANGUAGE


def _template_context(request: Request, language: str, **extra: object) -> dict:
    context = {
        "request": request,
        "lang": language,
        "t": lambda key, **kwargs: t(language, key, **kwargs),
    }
    context.update(extra)
    return context


def _admin_filter_labels() -> tuple[dict[str, str], dict[str, str]]:
    """Nhãn bộ lọc cho trang admin, luôn dùng tiếng Việt."""
    return (
        {item["value"]: item["label"] for item in corpus_options(DEFAULT_LANGUAGE)},
        {item["value"]: item["label"] for item in pitaka_options(DEFAULT_LANGUAGE)},
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request, lang: str | None = Query(None)):
    language = request_language(request, lang)
    response = templates.TemplateResponse(
        "index.html",
        _template_context(
            request,
            language,
            strings=ui_strings(language),
            corpus_options=corpus_options(language),
            pitaka_options=pitaka_options(language),
            language_options=language_options(),
            notice=get_notice(language),
            default_query="",
            ga_measurement_id=settings().get("ga_measurement_id", ""),
        ),
    )
    response.set_cookie(
        LANGUAGE_COOKIE,
        language,
        max_age=LANGUAGE_COOKIE_MAX_AGE,
        samesite="lax",
        httponly=False,
    )
    return response


@app.post("/search")
def search_api(payload: dict, request: Request):
    query = str(payload.get("query", "")).strip()
    filters = payload.get("filters") or {}
    corpus_types = resolve_corpus_types(filters.get("corpusType"))
    pitaka_type = resolve_pitaka_type(filters.get("pitakaType"))
    page = int(payload.get("page") or 1)
    page_size = min(20, int(payload.get("pageSize") or 5))
    if not query:
        raise HTTPException(status_code=400, detail="Missing query.")
    include_translations = bool(payload.get("includeTranslations", True))
    language = request_language(request, payload.get("language"))
    return search_passages(
        query,
        corpus_types,
        pitaka_type,
        page,
        page_size,
        include_translations=include_translations,
        language=language,
    )


@app.post("/search-page", response_class=HTMLResponse)
def search_page(
    request: Request,
    query: str = Form(...),
    corpus_type: str = Form(...),
    pitaka_type: str | None = Form(None),
    page: int = Form(1),
    lang: str | None = Form(None),
):
    language = request_language(request, lang)
    result = search_passages(
        query,
        resolve_corpus_types(corpus_type),
        resolve_pitaka_type(pitaka_type),
        page,
        5,
        include_translations=False,
        language=language,
    )
    # Bản dịch của dịch giả đọc thẳng từ DB nên hiển thị được ngay cùng kết quả,
    # không phải chờ tải sau như bản dịch AI.
    # Phải lấy theo TẤT CẢ các đoạn đang hiển thị: đoạn trích hay được mở rộng ngữ cảnh,
    # chỉ lấy đoạn neo thì phần dịch không phủ hết phần Pali, nhìn vào tưởng ghép lệch.
    # Lấy cả bản cấp bài kinh (Minh Châu) theo yêu cầu của khách - hiện đủ ba dịch giả
    # ngay tại kết quả. Bản cấp bài kinh chỉ in đoạn đầu; muốn đọc trọn thì đã có nút
    # "Xem toàn bộ bài kinh" ngay dưới thẻ, mở trang đọc với đủ nguyên văn.
    results = result.get("results") or []
    for item in results:
        passage_ids = [part["id"] for part in (item.get("snippetParagraphs") or [])] or [item["id"]]
        item["officialTranslations"] = official_translations_merged(
            passage_ids, language, whole_sutta_excerpt_chars=WHOLE_SUTTA_EXCERPT_CHARS
        )
        # Nguon nao khong co ban dich cho dung doan nay thi noi thang ra, thay vi im lang
        # bo qua - im lang khien nguoi doc tuong nguon do khong ton tai.
        present = {str(entry["source"]) for entry in item["officialTranslations"]}
        item["missingTranslations"] = [
            source_label(source_id, language)
            for source_id in SOURCE_ORDER
            if source_id != AI_SOURCE and source_id not in present
        ]

        # Nút đọc toàn bài phải trỏ vào bài kinh cha, không phải mục con sâu nhất mà
        # đoạn tìm kiếm đang nằm trong đó.
        reader_section = _reader_section_by_id(str(item.get("sectionId"))) if item.get("sectionId") else None
        item["readerSectionId"] = str(reader_section["id"]) if reader_section else item.get("sectionId")

    # Tra độ phủ theo BÀI KINH CHA. `passages.section_id` thường là mục con, nên tra
    # theo section cũ làm Sujato/Indacanda có ở phần khác của bài bị báo nhầm là trống.
    section_sources = sources_for_sections([item.get("readerSectionId") for item in results], language)
    for item in results:
        section_entries = section_sources.get(str(item.get("readerSectionId")), [])
        with_data = {str(entry["source"]) for entry in section_entries}
        # Hình dạng bản dịch mà dịch giả này có cho CẢ BÀI - dùng khi đoạn đang hiện
        # không có bản dịch nhưng nút "Xem toàn bộ bài kinh" vẫn bấm được.
        section_shapes = {str(entry["source"]): entry.get("shape") for entry in section_entries}
        here = {str(entry["source"]) for entry in item["officialTranslations"]}
        translations_by_source = {
            str(entry["source"]): entry for entry in item["officialTranslations"]
        }
        is_abhidhamma = "abhidhammapitaka" in normalize_pali(str(item.get("sourcePath") or ""))
        entries = []
        for source_id in SOURCE_ORDER:
            if source_id == AI_SOURCE:
                continue
            if source_id == "brahmali" and source_id not in with_data:
                unavailable_reason = t(language, "translation.brahmaliVinayaOnly")
            elif is_abhidhamma and source_id not in with_data:
                unavailable_reason = t(language, "translation.noAbhidhammaCoverage")
            else:
                unavailable_reason = t(language, "translation.noOfficial")
            translation = translations_by_source.get(source_id)
            # Nhãn phải nói đúng thứ người đọc sắp thấy: hình dạng của bản dịch đang in
            # ra, hoặc - khi đoạn này chưa có - hình dạng mà nút toàn bài sẽ mở ra.
            shape = translation.get("shape") if translation else section_shapes.get(source_id)
            entries.append(
                {
                    "source": source_id,
                    "label": source_label(source_id, language, shape),
                    "shape": shape,
                    "translation": translation,
                    "available": source_id in with_data,
                    "elsewhereOnly": source_id in with_data and source_id not in here,
                    "unavailableReason": unavailable_reason,
                }
            )
        item["translationEntries"] = entries
        # Giữ field cũ cho các đoạn HTML/cache cũ đang mở trong trình duyệt; template
        # mới dùng `translationEntries` để đặt nút ngay dưới đúng bản dịch.
        item["sectionSources"] = entries

    return templates.TemplateResponse(
        "results.html",
        _template_context(
            request,
            language,
            result=result,
            query=query,
            corpus_type=corpus_type,
            pitaka_type=pitaka_type,
            append_mode=page > 1,
        ),
    )


def _translation_or_error(passage_id: str, language: str = DEFAULT_LANGUAGE) -> dict:
    try:
        return translate_passage(passage_id, language)
    except Exception:
        return {
            "vi": None,
            "text": None,
            "fromCache": False,
            "error": public_translation_error(),
        }


@app.post("/api/translate-result")
def translate_result_api(payload: dict, request: Request):
    passage_id = str(payload.get("passageId") or "").strip()
    pali_text = str(payload.get("paliText") or "").strip()
    use_passage_cache = bool(payload.get("usePassageCache"))
    language = request_language(request, payload.get("language"))
    source = normalize_source(payload.get("source"))

    if not passage_id and not pali_text:
        raise HTTPException(status_code=400, detail="Missing passageId or paliText.")

    warning = t(language, "translation.aiWarning")

    if source != AI_SOURCE:
        # Bản dịch của dịch giả thật: có dữ liệu thì trả về, chưa có thì báo rõ
        # thay vì lặng lẽ chuyển sang bản dịch AI.
        human = resolve_human_translation(source, passage_id or None, pali_text, language)
        if human:
            return {"ok": True, "translation": human, "warning": None, "source": source}
        return {"ok": False, "translation": unavailable_translation(language), "warning": None, "source": source}

    try:
        if use_passage_cache and passage_id:
            translation = translate_passage(passage_id, language)
        elif pali_text:
            translation = translate_text_cached(pali_text, language)
        else:
            translation = translate_passage(passage_id, language)
        return {"ok": True, "translation": translation, "warning": warning, "source": source}
    except Exception:
        return {
            "ok": False,
            "translation": {
                "vi": None,
                "text": None,
                "fromCache": False,
                "error": public_translation_error(),
            },
            "warning": warning,
            "source": source,
        }


def _split_long_paragraph_safely(paragraph: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    remaining = paragraph.strip()

    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut_at = -1

        sentence_matches = list(re.finditer(r"[.!?;:।॥](?:[’”'\")\]]+)?\s+", window))
        if sentence_matches:
            cut_at = sentence_matches[-1].end()

        if cut_at < int(max_chars * 0.55):
            soft_matches = list(re.finditer(r"[,–—-](?:[’”'\")\]]+)?\s+", window))
            if soft_matches:
                cut_at = soft_matches[-1].end()

        if cut_at < int(max_chars * 0.45):
            whitespace = window.rfind(" ")
            if whitespace > int(max_chars * 0.45):
                cut_at = whitespace + 1

        if cut_at <= 0:
            cut_at = max_chars

        pieces.append(remaining[:cut_at].strip())
        remaining = remaining[cut_at:].strip()

    if remaining:
        pieces.append(remaining)

    return pieces


def _chunk_section_text(pali_text: str, max_chars: int = SECTION_TRANSLATION_CHUNK_CHARS) -> list[str]:
    paragraphs = [part.strip() for part in pali_text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush_current() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

    for paragraph in paragraphs:
        paragraph_pieces = (
            [paragraph]
            if len(paragraph) <= max_chars
            else _split_long_paragraph_safely(paragraph, max_chars)
        )

        for piece in paragraph_pieces:
            piece_len = len(piece)
            if piece_len > max_chars:
                flush_current()
                chunks.append(piece)
                continue

            next_len = current_len + piece_len + (2 if current else 0)
            if current and next_len > max_chars:
                flush_current()
                current = [piece]
                current_len = piece_len
            else:
                current.append(piece)
                current_len = next_len

    flush_current()

    return chunks


def _translate_section_text(pali_text: str, language: str = DEFAULT_LANGUAGE) -> tuple[dict, bool]:
    chunks = _chunk_section_text(pali_text)
    if not chunks:
        return {"vi": "", "text": "", "notes": None, "model": None, "fromCache": False}, False

    try:
        if len(chunks) == 1 and len(pali_text) <= SECTION_TRANSLATION_MAX_CHARS:
            return translate_text_cached(pali_text, language), True

        translated_parts: list[str] = []
        failed_parts: list[int] = []
        models: list[str] = []

        for index, chunk in enumerate(chunks, start=1):
            try:
                translated = translate_text_cached(chunk, language)
                text = str(translated.get("vi") or "").strip()
                if text:
                    translated_parts.append(text)
                else:
                    failed_parts.append(index)

                model = translated.get("model")
                if model and model not in models:
                    models.append(str(model))
            except Exception:
                failed_parts.append(index)

        if not translated_parts:
            return (
                {
                    "vi": None,
                    "fromCache": False,
                    "error": public_translation_error(),
                },
                True,
            )

        notes = f"Dịch theo {len(chunks)} phần lớn rồi ghép lại để giữ mạch văn."
        if failed_parts:
            notes += f" Một số phần chưa dịch được: {', '.join(map(str, failed_parts))}."

        joined = "\n\n".join(translated_parts)
        return (
            {
                "vi": joined,
                "text": joined,
                "notes": notes,
                "model": ", ".join(models) if models else None,
                "fromCache": False,
                "chunkCount": len(chunks),
                "failedChunks": failed_parts,
            },
            True,
        )
    except Exception:
        return (
            {
                "vi": None,
                "fromCache": False,
                "error": public_translation_error(),
            },
            True,
        )


def _passage_rend(row: dict) -> str:
    hierarchy = row.get("hierarchy") or {}
    if isinstance(hierarchy, dict):
        return str(hierarchy.get("rend") or "")
    return ""


def _paragraph_label(row: dict, language: str) -> str | None:
    paragraph_no = row.get("paragraph_no")
    if paragraph_no and row.get("xml_paragraph_no"):
        return f"{t(language, 'results.paragraph')} {paragraph_no}"
    return None


def _join_section_passages(rows: list[dict], language: str = DEFAULT_LANGUAGE) -> str:
    parts: list[str] = []
    previous_rend = ""

    for row in rows:
        text = str(row["pali_text"]).strip()
        if not text:
            continue

        rend = _passage_rend(row)
        label = _paragraph_label(row, language)
        line = f"{label}\n{text}" if label else text

        if not parts:
            parts.append(line)
        elif previous_rend in GATHA_RENDS and rend in GATHA_RENDS:
            parts[-1] = f"{parts[-1]}\n{line}"
        else:
            parts.append(line)

        previous_rend = rend

    return "\n\n".join(parts)


def _section_payload(
    section_id: str,
    include_translation: bool = True,
    language: str = DEFAULT_LANGUAGE,
    source: str = AI_SOURCE,
) -> dict:
    selected = normalize_source(source)
    section = fetch_one(
        """
        select id, document_id, title, source_path, start_sort_order,
               coalesce(end_sort_order, start_sort_order) as end_sort_order
        from sections
        where id = %s
        """,
        [section_id],
    )
    if not section:
        raise HTTPException(status_code=404, detail="Section not found.")

    # Mọi nguồn của dịch giả đều dùng cùng một Pali trọn bài. Trước đây chỉ hai nguồn
    # cấp bài được nâng lên section cha, còn Indacanda đoạn/Sujato vẫn mở mục con nên
    # cùng một nút "toàn bộ bài kinh" cho ra ba phạm vi khác nhau.
    section = _canonical_reader_section(section)

    rows = fetch_all(
        """
        select
          id,
          coalesce(display_paragraph_no, xml_paragraph_no, paragraph_no) as paragraph_no,
          xml_paragraph_no,
          pali_text,
          hierarchy
        from passages
        where document_id = %s
          and sort_order between %s and %s
        order by sort_order asc
        """,
        [section["document_id"], section["start_sort_order"], section["end_sort_order"]],
    )
    pali_text = _join_section_passages(rows, language)
    if include_translation:
        translation, attempted_translation = _translate_section_text(pali_text, language)
    else:
        translation, attempted_translation = {"vi": None, "text": None, "fromCache": False, "pending": True}, False
    source_path = section.get("source_path") or []
    # Bản dịch của dịch giả cho cả bài, ghép theo đúng thứ tự các đoạn đang có.
    # `covers_whole_sutta=True`: ở đây `rows` là TOÀN BỘ đoạn của bài kinh, nên nguồn cấp
    # đoạn phủ đủ 100% được dán nhãn "(toàn bộ bài kinh)" - đúng thứ đang hiện ra.
    official_list = official_translations_merged(
        [str(row["id"]) for row in rows], language, covers_whole_sutta=True
    )
    # Liet ke DU moi dich gia chu khong chi nguon co du lieu, dung nhu khach yeu cau:
    # nguon nao chua co ban dich cho muc nay van hien tab, bam vao thi bao "Hien khong co
    # ban dich chinh thuc nao". Truoc day chi dung tab tu `official_list` nen tab
    # Indacanda bien mat o moi bo kinh chua nap - giong het loi ben trang ket qua.
    with_data = {str(item["source"]) for item in official_list}
    # Nhãn tab lấy thẳng từ `official_list` chứ không tự dựng lại: chỉ nơi đã ghép xong
    # mới biết nguồn cấp đoạn có phủ đủ bài hay không, tự tính lại ở đây sẽ ra khác.
    label_by_source = {str(item["source"]): str(item["label"]) for item in official_list}
    shape_by_source = {str(item["source"]): item.get("labelShape") for item in official_list}
    is_abhidhamma = "abhidhammapitaka" in normalize_pali(" ".join(map(str, source_path)))
    available = []
    for source_id in SOURCE_ORDER:
        if source_id == AI_SOURCE:
            continue
        if source_id == "brahmali" and source_id not in with_data:
            unavailable_reason = t(language, "translation.brahmaliVinayaOnly")
        elif is_abhidhamma and source_id not in with_data:
            unavailable_reason = t(language, "translation.noAbhidhammaCoverage")
        else:
            unavailable_reason = t(language, "translation.noOfficial")
        available.append(
            {
                "source": source_id,
                "label": label_by_source.get(source_id)
                or source_label(source_id, language, shape_by_source.get(source_id)),
                "shape": shape_by_source.get(source_id),
                "available": source_id in with_data,
                "unavailableReason": unavailable_reason,
            }
        )
    if selected != AI_SOURCE and selected not in {item["source"] for item in available}:
        selected = AI_SOURCE
    chosen = next((item for item in official_list if item["source"] == selected), None)

    return {
        "sectionId": str(section["id"]),
        "officialTranslations": official_list,
        "availableSources": available,
        "selectedSource": selected,
        "selectedTranslation": chosen,
        "title": section["title"],
        "sourcePath": " -> ".join(source_path) if isinstance(source_path, list) else "",
        "passageCount": len(rows),
        "paliText": pali_text,
        "translation": translation,
        "attemptedTranslation": attempted_translation,
        "warning": t(language, "translation.aiWarning"),
    }


@app.get("/api/sections/{section_id}")
def section_api(section_id: str, request: Request, lang: str | None = Query(None)):
    return _section_payload(section_id, include_translation=False, language=request_language(request, lang))


@app.get("/api/sections/{section_id}/translate")
def section_translate_api(section_id: str, request: Request, lang: str | None = Query(None)):
    language = request_language(request, lang)
    section = _section_payload(section_id, include_translation=True, language=language)
    return {
        "ok": bool(section.get("translation", {}).get("vi")),
        "sectionId": section["sectionId"],
        "translation": section["translation"],
        "warning": section["warning"],
    }


@app.get("/api/sections/{section_id}/translate-chunk")
def section_translate_chunk_api(
    section_id: str,
    request: Request,
    chunk: int = Query(0, ge=0),
    lang: str | None = Query(None),
    source: str | None = Query(None),
):
    language = request_language(request, lang)
    section = _section_payload(section_id, include_translation=False, language=language)
    translation_source = normalize_source(source)
    chunks = _chunk_section_text(
        str(section.get("paliText") or ""),
        max_chars=SECTION_TRANSLATION_STREAM_CHUNK_CHARS,
    )
    total_chunks = len(chunks)
    if total_chunks == 0:
        return {
            "ok": True,
            "sectionId": section["sectionId"],
            "chunkIndex": 0,
            "totalChunks": 0,
            "hasMore": False,
            "translation": {"vi": "", "text": "", "fromCache": False},
            "warning": section["warning"],
        }
    if chunk >= total_chunks:
        raise HTTPException(status_code=404, detail="Translation chunk not found.")

    if translation_source != AI_SOURCE:
        human = resolve_human_translation(translation_source, None, chunks[chunk], language)
        return {
            "ok": bool(human),
            "sectionId": section["sectionId"],
            "chunkIndex": chunk,
            "totalChunks": total_chunks,
            "hasMore": False,
            "translation": human or unavailable_translation(language),
            "warning": None,
        }

    try:
        translation = translate_text_cached(chunks[chunk], language)
        ok = bool(translation.get("vi"))
    except Exception:
        translation = {
            "vi": None,
            "text": None,
            "fromCache": False,
            "error": public_translation_error(),
        }
        ok = False

    return {
        "ok": ok,
        "sectionId": section["sectionId"],
        "chunkIndex": chunk,
        "totalChunks": total_chunks,
        "hasMore": chunk + 1 < total_chunks,
        "translation": translation,
        "warning": section["warning"],
    }


@app.get("/section-page/{section_id}", response_class=HTMLResponse)
def section_page(
    request: Request,
    section_id: str,
    lang: str | None = Query(None),
    source: str | None = Query(None),
):
    language = request_language(request, lang)
    section = _section_payload(section_id, include_translation=False, language=language, source=source or AI_SOURCE)
    return templates.TemplateResponse(
        "section.html",
        _template_context(request, language, section=section),
    )


@app.get("/api/passages/{passage_id}")
def passage_api(passage_id: str, request: Request):
    row = fetch_one(
        "select id, document_id, sort_order, paragraph_no, pali_text, hierarchy from passages where id = %s",
        [passage_id],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Passage not found.")

    nearby = fetch_all(
        """
        select id, paragraph_no, pali_text
        from passages
        where document_id = %s and sort_order between %s and %s and id <> %s
        order by sort_order asc
        """,
        [row["document_id"], row["sort_order"] - 2, row["sort_order"] + 2, row["id"]],
    )
    source_path = row.get("hierarchy", {}).get("sourcePath")
    return {
        "id": str(row["id"]),
        "sourcePath": " -> ".join(source_path) if isinstance(source_path, list) else "",
        "paragraphNo": row["paragraph_no"],
        "paliText": row["pali_text"],
        "translation": _translation_or_error(passage_id, request_language(request)),
        "nearbyPassages": nearby,
    }


@app.exception_handler(Exception)
def handle_exception(_request: Request, exc: Exception):
    status = exc.status_code if isinstance(exc, HTTPException) else 500
    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
    return JSONResponse({"error": detail}, status_code=status)
def get_current_admin(request: Request):
    if not request.session.get("admin_logged_in"):
        raise HTTPException(status_code=status.HTTP_302_FOUND, headers={"Location": "/admin/login"})
    return True


@app.get("/admin", include_in_schema=False)
def admin_root(request: Request):
    if request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/history", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/history", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("admin_login.html", {"request": request})


@app.post("/admin/login")
def admin_login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    current_username_bytes = username.encode("utf8")
    correct_username_bytes = str(settings().get("admin_username", "")).encode("utf8")
    is_correct_username = secrets.compare_digest(current_username_bytes, correct_username_bytes)

    current_password_bytes = password.encode("utf8")
    correct_password_bytes = str(settings().get("admin_password", "")).encode("utf8")
    is_correct_password = secrets.compare_digest(current_password_bytes, correct_password_bytes)

    if not (is_correct_username and is_correct_password):
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": "Sai tên đăng nhập hoặc mật khẩu"})
    
    request.session["admin_logged_in"] = True
    return RedirectResponse(url="/admin/history", status_code=status.HTTP_302_FOUND)


@app.get("/admin/logout")
def admin_logout(request: Request):
    request.session.pop("admin_logged_in", None)
    return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)


# Không còn trần: cuộn tới đâu tải tới đó, mỗi lượt một mẻ nhỏ. Trước đây phải chọn số
# dòng mỗi trang rồi bấm chuyển trang; khách muốn xem hết nên bỏ hẳn phân trang.
ADMIN_HISTORY_BATCH = 20
# Chặn trên cho tham số của client, đề phòng ai đó gọi thẳng API xin vài trăm nghìn dòng.
ADMIN_HISTORY_MAX_BATCH = 100


def _admin_history_where(keyword: str, only_empty: bool) -> tuple[str, list[object]]:
    conditions: list[str] = []
    params: list[object] = []
    if keyword:
        conditions.append("query ilike %s")
        params.append(f"%{keyword}%")
    if only_empty:
        # Đúng các lượt tìm không ra kết quả nào - chính là nhóm khách muốn soi.
        conditions.append("coalesce(array_length(result_passage_ids, 1), 0) = 0")
    return (("where " + " and ".join(conditions)) if conditions else ""), params


def _admin_history_rows(keyword: str, only_empty: bool, limit: int,
                        before_time: str | None, before_id: str | None) -> list[dict]:
    """Một mẻ lịch sử, cũ dần kể từ mốc `before`.

    Phân trang theo CON TRỎ chứ không theo `offset`: `search_logs` được ghi thêm sau mỗi
    lượt tìm kiếm, nên trong lúc người dùng cuộn thì offset bị đẩy lệch - dòng đã xem lại
    hiện lại, dòng chưa xem thì trượt mất. Mốc `(created_at, id)` không bị ảnh hưởng.
    """
    where_sql, params = _admin_history_where(keyword, only_empty)
    if before_time and before_id:
        cursor_sql = "(created_at, id) < (%s::timestamptz, %s::uuid)"
        where_sql = f"{where_sql} and {cursor_sql}" if where_sql else f"where {cursor_sql}"
        params = [*params, before_time, before_id]
    return fetch_all(
        f"""
        select id, query, filters,
               coalesce(array_length(result_passage_ids, 1), 0) as result_count,
               created_at
        from search_logs
        {where_sql}
        order by created_at desc, id desc
        limit %s
        """,
        [*params, limit],
    )


@app.get("/admin/history", response_class=HTMLResponse)
def admin_history(
    request: Request,
    q: str = Query(""),
    only_empty: bool = Query(False),
    _: str = Depends(get_current_admin),
):
    keyword = q.strip()
    where_sql, params = _admin_history_where(keyword, only_empty)

    # Chỉ mẻ đầu; phần còn lại do trình duyệt xin thêm khi cuộn tới đáy.
    logs = _admin_history_rows(keyword, only_empty, ADMIN_HISTORY_BATCH, None, None)

    total_row = fetch_one(f"select count(*) as cnt from search_logs {where_sql}", params)
    total_logs = total_row["cnt"] if total_row else 0

    all_row = fetch_one("select count(*) as cnt from search_logs")
    all_logs = all_row["cnt"] if all_row else 0

    empty_row = fetch_one(
        "select count(*) as cnt from search_logs where coalesce(array_length(result_passage_ids, 1), 0) = 0"
    )
    empty_logs = empty_row["cnt"] if empty_row else 0

    top_queries = fetch_all(
        """
        select query, count(*) as count
        from search_logs
        group by query
        order by count desc
        limit 10
        """
    )

    corpus_labels, pitaka_labels = _admin_filter_labels()

    return templates.TemplateResponse(
        "admin_history.html",
        {
            "request": request,
            "logs": logs,
            "total_logs": total_logs,
            "all_logs": all_logs,
            "empty_logs": empty_logs,
            "corpus_options": corpus_labels,
            "pitaka_options": pitaka_labels,
            "top_queries": top_queries,
            "batch": ADMIN_HISTORY_BATCH,
            "q": keyword,
            "only_empty": only_empty,
            "ga_measurement_id": settings().get("ga_measurement_id", ""),
        },
    )


@app.get("/api/admin/history/rows")
def api_admin_history_rows(
    q: str = Query(""),
    only_empty: bool = Query(False),
    limit: int = Query(ADMIN_HISTORY_BATCH, ge=1, le=ADMIN_HISTORY_MAX_BATCH),
    before_time: str = Query(""),
    before_id: str = Query(""),
    _: str = Depends(get_current_admin),
):
    """Mẻ lịch sử tiếp theo cho việc cuộn vô hạn ở `/admin/history`.

    Xin dư MỘT dòng rồi cắt bỏ, để biết còn dữ liệu phía sau hay không mà không phải chạy
    thêm một câu `count(*)` cho mỗi lần cuộn.
    """
    corpus_labels, pitaka_labels = _admin_filter_labels()
    rows = _admin_history_rows(
        q.strip(), only_empty, limit + 1, before_time or None, before_id or None
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    payload = []
    for row in rows:
        filters = row.get("filters") or {}
        badges = [corpus_labels.get(c, c) for c in (filters.get("corpusType") or [])]
        pitaka = filters.get("pitakaType")
        if pitaka:
            badges.append(pitaka_labels.get(pitaka, pitaka))
        created = row.get("created_at")
        payload.append(
            {
                "id": str(row["id"]),
                "time": created.strftime("%H:%M:%S %d/%m/%Y") if created else "N/A",
                "createdAt": created.isoformat() if created else None,
                "query": row.get("query") or "",
                "badges": badges,
                "resultCount": int(row.get("result_count") or 0),
            }
        )
    return {"rows": payload, "hasMore": has_more}


@app.get("/admin/notice", response_class=HTMLResponse)
def admin_notice_page(request: Request, saved: bool = Query(False), _: str = Depends(get_current_admin)):
    return templates.TemplateResponse(
        "admin_notice.html",
        {
            "request": request,
            "notice": get_notice_config(),
            "languages": LANGUAGES,
            "language_options": language_options(),
            "saved": saved,
            "ga_measurement_id": settings().get("ga_measurement_id", ""),
        },
    )


@app.post("/admin/notice")
async def admin_notice_save(request: Request, _: str = Depends(get_current_admin)):
    form = await request.form()
    enabled = str(form.get("enabled") or "").strip() in {"1", "on", "true"}
    content = {
        code: {
            "title": str(form.get(f"title_{code}") or ""),
            "body": str(form.get(f"body_{code}") or ""),
        }
        for code in LANGUAGES
    }
    save_notice(enabled, content)
    return RedirectResponse(url="/admin/notice?saved=1", status_code=status.HTTP_302_FOUND)


@app.get("/api/admin/history/{log_id}")
def api_admin_history_detail(log_id: str, _: str = Depends(get_current_admin)):
    log = fetch_one("select * from search_logs where id = %s", [log_id])
    if not log:
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch sử này.")

    passage_ids = log.get("result_passage_ids") or []
    passages = []
    
    if passage_ids:
        rows = fetch_all(
            """
            select 
                p.id, p.pali_text, p.paragraph_no, p.display_paragraph_no, p.xml_paragraph_no, p.hierarchy,
                d.file_name, d.corpus_type,
                s.title as section_title, s.source_path as section_source_path,
                t.translated_text
            from passages p
            join documents d on d.id = p.document_id
            left join sections s on s.id = p.section_id
            left join translations t on t.passage_id = p.id
            where p.id = any(%s::uuid[])
            """,
            [passage_ids]
        )
        passage_map = {str(row["id"]): row for row in rows}
        passages = [passage_map[str(pid)] for pid in passage_ids if str(pid) in passage_map]

    pitaka_type = None
    if log.get("filters"):
        pitaka_type = log["filters"].get("pitakaType")

    return {
        "log": {
            "id": log["id"],
            "query": log["query"],
            "created_at": log["created_at"].strftime('%H:%M:%S %d/%m/%Y') if log["created_at"] else "N/A",
            "filters": log["filters"],
        },
        "passages": [
            {
                "file_name": p["file_name"],
                "section_title": p["section_title"],
                "paragraph_no": p.get("display_paragraph_no") or p.get("xml_paragraph_no") or p.get("paragraph_no"),
                "pali_text": p["pali_text"],
                "translated_text": p["translated_text"],
                "breadcrumb": _display_source(p, [p["corpus_type"]], pitaka_type)
            } for p in passages
        ]
    }


@app.post("/api/admin/history/clear")
def clear_admin_history(_: str = Depends(get_current_admin)):
    execute("truncate table search_logs restart identity;")
    return {"ok": True, "message": "Đã xóa toàn bộ lịch sử tìm kiếm."}
