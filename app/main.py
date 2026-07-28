import re
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import fetch_all, fetch_one
from .search_engine import search_passages
from .translator import public_translation_error, translate_passage, translate_text


APP_DIR = Path(__file__).resolve().parent
SECTION_TRANSLATION_MAX_CHARS = 18000
SECTION_TRANSLATION_CHUNK_CHARS = 12000

app = FastAPI(title="Tipiṭaka Python Search")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


CORPUS_OPTIONS = [
    {"value": "mul", "label": "Tam Tạng", "description": "Tipiṭaka Mūla, gồm Tam tạng gốc"},
    {"value": "att", "label": "Chú giải", "description": "Aṭṭhakathā"},
    {"value": "tik", "label": "Phụ chú giải", "description": "Ṭīkā"},
    {"value": "nrf", "label": "Ngoại điển", "description": "Añña"},
]
PITAKA_OPTIONS = [
    {"value": "vinaya", "label": "Tạng Luật", "description": "Vinayapiṭaka"},
    {"value": "sutta", "label": "Tạng Kinh", "description": "Suttapiṭaka"},
    {"value": "abhidhamma", "label": "Tạng Vi Diệu Pháp", "description": "Abhidhammapiṭaka"},
]
AI_TRANSLATION_WARNING = "Đây là bản dịch của AI, chưa có sự kiểm chứng."


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "corpus_options": CORPUS_OPTIONS,
            "pitaka_options": PITAKA_OPTIONS,
            "default_query": "",
        },
    )


@app.post("/search")
def search_api(payload: dict):
    query = str(payload.get("query", "")).strip()
    filters = payload.get("filters") or {}
    corpus_types = filters.get("corpusType") or ["mul"]
    pitaka_type = filters.get("pitakaType")
    page = int(payload.get("page") or 1)
    page_size = min(20, int(payload.get("pageSize") or 5))
    if not query:
        raise HTTPException(status_code=400, detail="Missing query.")
    return search_passages(query, corpus_types, pitaka_type, page, page_size)


@app.post("/search-page", response_class=HTMLResponse)
def search_page(
    request: Request,
    query: str = Form(...),
    corpus_type: str = Form(...),
    pitaka_type: str | None = Form(None),
    page: int = Form(1),
):
    result = search_passages(query, [corpus_type], pitaka_type or None, page, 5)
    return templates.TemplateResponse(
        "results.html",
        {
            "request": request,
            "result": result,
            "query": query,
            "corpus_type": corpus_type,
            "pitaka_type": pitaka_type,
            "append_mode": page > 1,
        },
    )


def _translation_or_error(passage_id: str) -> dict:
    try:
        return translate_passage(passage_id)
    except Exception:
        return {
            "vi": None,
            "fromCache": False,
            "error": public_translation_error(),
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


def _translate_section_text(pali_text: str) -> tuple[dict, bool]:
    chunks = _chunk_section_text(pali_text)
    if not chunks:
        return {"vi": "", "notes": None, "model": None, "fromCache": False}, False

    try:
        if len(chunks) == 1 and len(pali_text) <= SECTION_TRANSLATION_MAX_CHARS:
            return translate_text(pali_text), True

        translated_parts: list[str] = []
        failed_parts: list[int] = []
        models: list[str] = []

        for index, chunk in enumerate(chunks, start=1):
            try:
                translated = translate_text(chunk)
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

        return (
            {
                "vi": "\n\n".join(translated_parts),
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


def _paragraph_label(row: dict) -> str:
    paragraph_no = row.get("paragraph_no")
    if paragraph_no and row.get("xml_paragraph_no"):
        return f"Đoạn {paragraph_no}"
    if paragraph_no:
        return f"Đoạn {paragraph_no} (tiếp)"
    return "Đoạn tiếp theo"


def _section_payload(section_id: str) -> dict:
    section = fetch_one(
        """
        select id, title, source_path
        from sections
        where id = %s
        """,
        [section_id],
    )
    if not section:
        raise HTTPException(status_code=404, detail="Section not found.")

    rows = fetch_all(
        """
        select id, coalesce(display_paragraph_no, xml_paragraph_no, paragraph_no) as paragraph_no, xml_paragraph_no, pali_text
        from passages
        where section_id = %s
        order by sort_order asc
        """,
        [section_id],
    )
    parts: list[str] = []
    for row in rows:
        label = _paragraph_label(row)
        parts.append(f"{label}\n{row['pali_text']}")

    pali_text = "\n\n".join(parts)
    translation, attempted_translation = _translate_section_text(pali_text)
    source_path = section.get("source_path") or []
    return {
        "sectionId": str(section["id"]),
        "title": section["title"],
        "sourcePath": " -> ".join(source_path) if isinstance(source_path, list) else "",
        "passageCount": len(rows),
        "paliText": pali_text,
        "translation": translation,
        "attemptedTranslation": attempted_translation,
        "warning": AI_TRANSLATION_WARNING,
    }


@app.get("/api/sections/{section_id}")
def section_api(section_id: str):
    return _section_payload(section_id)


@app.get("/section-page/{section_id}", response_class=HTMLResponse)
def section_page(request: Request, section_id: str):
    section = _section_payload(section_id)
    return templates.TemplateResponse(
        "section.html",
        {
            "request": request,
            "section": section,
        },
    )


@app.get("/api/passages/{passage_id}")
def passage_api(passage_id: str):
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
        "translation": _translation_or_error(passage_id),
        "nearbyPassages": nearby,
    }


@app.exception_handler(Exception)
def handle_exception(_request: Request, exc: Exception):
    status = exc.status_code if isinstance(exc, HTTPException) else 500
    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
    return JSONResponse({"error": detail}, status_code=status)
