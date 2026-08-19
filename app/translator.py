import json
import re
import hashlib
from itertools import count
from threading import Lock

from google import genai
from pydantic import BaseModel

from .config import settings
from .db import execute, fetch_one
from .i18n import DEFAULT_LANGUAGE, TRANSLATION_TARGETS, normalize_language


PROMPT_VERSION = "python-pali-vi-contextual-v5"
CHUNKED_PROMPT_VERSION = f"{PROMPT_VERSION}-chunked"
TRANSLATION_FALLBACK_CHUNK_CHARS = 3200
TRANSLATION_RESCUE_CHUNK_CHARS = 900
BAD_TEXT_MODELS = {"gemini-2.5-flash"}
FALLBACK_TEXT_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]
PUBLIC_TRANSLATION_ERROR = "Chưa dịch được đoạn này. Vui lòng kiểm tra GEMINI_API_KEY hoặc thử lại sau."
_MODEL_CURSOR = count()
_MODEL_LOCK = Lock()


class Translation(BaseModel):
    translatedText: str
    notes: str | None = None
    model: str | None = None


class SummaryPoint(BaseModel):
    summary_text: str
    passage_ids: list[str]


class SectionSummary(BaseModel):
    points: list[SummaryPoint]


def _client() -> genai.Client:
    api_key = str(settings()["gemini_api_key"])
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    return genai.Client(
        api_key=api_key,
        http_options={"timeout": int(settings()["gemini_request_timeout_ms"])},
    )


def _models() -> list[str]:
    configured = [str(item).strip() for item in settings()["gemini_text_models"] if str(item).strip()]
    merged: list[str] = []
    for model in [*configured, *FALLBACK_TEXT_MODELS]:
        if model and model not in merged and model not in BAD_TEXT_MODELS:
            merged.append(model)
    return merged


def _models_for_call() -> list[str]:
    models = _models()
    if len(models) <= 1:
        return models
    with _MODEL_LOCK:
        offset = next(_MODEL_CURSOR) % len(models)
    return [*models[offset:], *models[:offset]]


def public_translation_error() -> str:
    return PUBLIC_TRANSLATION_ERROR


def _translation_payload(text: str | None, notes: str | None, model: str | None, language: str, from_cache: bool) -> dict:
    # Khoá "vi" là tên cũ, giữ lại để template và JS hiện có không phải đổi;
    # "text" là tên trung lập dùng cho mọi ngôn ngữ.
    return {
        "vi": text,
        "text": text,
        "language": language,
        "notes": notes,
        "model": model,
        "fromCache": from_cache,
    }


def translate_passage(passage_id: str, language: str = DEFAULT_LANGUAGE) -> dict:
    language = normalize_language(language)
    active_models = _models()
    cached = fetch_one(
        """
        select translated_text, notes, model
        from translations
        where passage_id = %s
          and language = %s
          and model = any(%s)
          and prompt_version = %s
        order by created_at desc
        limit 1
        """,
        [passage_id, language, active_models, PROMPT_VERSION],
    )
    if cached:
        return _translation_payload(cached["translated_text"], cached["notes"], cached["model"], language, True)

    passage = fetch_one("select pali_text from passages where id = %s", [passage_id])
    if not passage:
        raise RuntimeError("Passage not found.")

    translated = _translate_text_resilient(passage["pali_text"], language)
    model = translated.model or active_models[0]
    execute(
        """
        insert into translations (passage_id, language, model, prompt_version, translated_text, notes)
        values (%s, %s, %s, %s, %s, %s)
        on conflict (passage_id, language, model, prompt_version)
        do update set translated_text = excluded.translated_text, notes = excluded.notes, created_at = now()
        """,
        [passage_id, language, model, PROMPT_VERSION, translated.translatedText, translated.notes],
    )
    return _translation_payload(translated.translatedText, translated.notes, model, language, False)


def translate_text(pali_text: str, language: str = DEFAULT_LANGUAGE) -> dict:
    language = normalize_language(language)
    translated = _translate_text_resilient(pali_text, language)
    model = translated.model or (_models()[0] if _models() else None)
    return _translation_payload(translated.translatedText, translated.notes, model, language, False)


def _text_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def translate_text_cached(pali_text: str, language: str = DEFAULT_LANGUAGE) -> dict:
    language = normalize_language(language)
    active_models = _models()
    text_hash = _text_hash(pali_text)
    cached = fetch_one(
        """
        select translated_text, notes, model
        from text_translations
        where text_hash = %s
          and language = %s
          and model = any(%s)
          and prompt_version = %s
        order by created_at desc
        limit 1
        """,
        [text_hash, language, active_models, PROMPT_VERSION],
    )
    if cached:
        payload = _translation_payload(cached["translated_text"], cached["notes"], cached["model"], language, True)
        payload["textHash"] = text_hash
        return payload

    translated = _translate_text_resilient(pali_text, language)
    model = translated.model or active_models[0]
    execute(
        """
        insert into text_translations (text_hash, language, model, prompt_version, source_text, translated_text, notes)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (text_hash, language, model, prompt_version)
        do update set translated_text = excluded.translated_text, notes = excluded.notes, source_text = excluded.source_text, created_at = now()
        """,
        [text_hash, language, model, PROMPT_VERSION, pali_text, translated.translatedText, translated.notes],
    )
    payload = _translation_payload(translated.translatedText, translated.notes, model, language, False)
    payload["textHash"] = text_hash
    return payload


def _strip_code_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _parse_translation_response(raw_text: str, model: str) -> Translation:
    text = _strip_code_fence(raw_text or "")
    if not text:
        raise ValueError("Empty Gemini translation response.")

    attempts = [text]
    if text.startswith("{") and not text.endswith("}"):
        attempts.append(text + "}")
    if text.startswith("{") and text.count("{") > text.count("}"):
        attempts.append(text + ("}" * (text.count("{") - text.count("}"))))

    for candidate in attempts:
        try:
            parsed = Translation.model_validate_json(candidate)
            parsed.model = model
            if parsed.translatedText.strip():
                return parsed
        except Exception:
            pass

    try:
        data = json.loads(attempts[-1])
        translated = str(data.get("translatedText") or data.get("translation") or "").strip()
        notes = data.get("notes")
        if translated:
            return Translation(translatedText=translated, notes=str(notes) if notes else None, model=model)
    except Exception:
        pass

    match = re.search(r'"translatedText"\s*:\s*"((?:\\.|[^"\\])*)', text, flags=re.DOTALL)
    if match:
        encoded = '"' + match.group(1) + '"'
        try:
            translated = json.loads(encoded).strip()
        except Exception:
            translated = match.group(1).strip()
        if translated:
            return Translation(translatedText=translated, notes=None, model=model)

    if not text.startswith("{") and len(text) >= 20:
        return Translation(translatedText=text, notes=None, model=model)

    raise ValueError("Gemini translation response was not usable JSON.")


def _translation_prompt(pali_text: str, json_mode: bool, language: str = DEFAULT_LANGUAGE) -> str:
    target = TRANSLATION_TARGETS.get(normalize_language(language), TRANSLATION_TARGETS[DEFAULT_LANGUAGE])
    lines = [
        f"Bạn là trợ lý dịch thuật Pali sang {target} cho văn bản kinh điển Phật giáo Theravāda.",
        f"Nhiệm vụ: dịch văn bản Pali sang {target} tự nhiên, rõ nghĩa, trang nghiêm và chính xác.",
        f"BẮT BUỘC: toàn bộ bản dịch phải viết bằng {target}, không được dùng ngôn ngữ khác.",
        "Không dịch máy móc từng chữ. Hãy ưu tiên truyền đạt đúng ý nghĩa của câu Pali bằng tiếng Việt dễ hiểu.",
        "Giữ đầy đủ nội dung của văn bản gốc; không tóm tắt, không bỏ ý, không thêm ý giáo lý ngoài văn bản.",
        f"Nếu câu Pali rất dài, được phép tách thành vài câu {target} ngắn hơn để dễ đọc, miễn không đổi nghĩa.",
        "Nếu văn bản thuộc dạng vấn đáp, tranh luận, phân tích pháp số hoặc định nghĩa Abhidhamma, hãy dịch theo đúng văn thể đó.",
        f"Không dịch kiểu chú giải từng cụm trong ngoặc. Không chèn từ Pali sau mỗi cụm {target}.",
        "Chỉ giữ thuật ngữ Pali trong ngoặc khi thuật ngữ đó quan trọng, khó dịch hết nghĩa, hoặc cần đối chiếu học thuật.",
        f"Dùng thuật ngữ Phật học {target} nhất quán, quen thuộc với truyền thống Theravāda.",
        "Với các thuật ngữ có nhiều cách dịch, hãy chọn cách dịch phù hợp nhất theo văn cảnh.",
        "Không áp dụng máy móc một bảng thuật ngữ cố định; luôn xét nghĩa theo văn cảnh Pali cụ thể.",
        "Với các đoạn lặp công thức hoặc ký hiệu lược như ...pe..., hãy dịch gọn theo đúng ý lược, không tự thêm nội dung không có trong văn bản.",
        "Văn phong nên trong sáng, mạch lạc, tự nhiên với người đọc bản ngữ.",
        "Nếu đoạn dài, vẫn dịch đủ toàn bộ, không tóm tắt.",
    ]
    if json_mode:
        lines.append('Trả JSON thuần, đúng một object: {"translatedText":"...","notes":"..."}')
    else:
        lines.append(f"Chỉ trả bản dịch {target} thuần, không bọc JSON, không markdown, không giải thích thêm.")
    lines.extend(["", "Pali:", pali_text])
    return "\n".join(lines)


def _split_text_for_translation(text: str, max_chars: int = TRANSLATION_FALLBACK_CHUNK_CHARS) -> list[str]:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

    def split_long_paragraph(paragraph: str) -> list[str]:
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

    for paragraph in paragraphs:
        for piece in ([paragraph] if len(paragraph) <= max_chars else split_long_paragraph(paragraph)):
            next_len = current_len + len(piece) + (2 if current else 0)
            if current and next_len > max_chars:
                flush()
            current.append(piece)
            current_len = len(piece) if current_len == 0 else current_len + len(piece) + 2

    flush()
    return chunks


def _translate_text_resilient(pali_text: str, language: str = DEFAULT_LANGUAGE) -> Translation:
    try:
        return _translate_text(pali_text, language)
    except Exception as first_error:
        chunks = _split_text_for_translation(pali_text, max_chars=TRANSLATION_RESCUE_CHUNK_CHARS)
        if len(chunks) <= 1:
            raise first_error

        translated_parts: list[str] = []
        models: list[str] = []
        failed_chunks: list[int] = []
        for index, chunk in enumerate(chunks, start=1):
            try:
                translated = _translate_text(chunk, language)
                translated_parts.append(translated.translatedText.strip())
                if translated.model and translated.model not in models:
                    models.append(translated.model)
            except Exception:
                failed_chunks.append(index)

        if not translated_parts:
            raise first_error

        notes = f"Dịch fallback theo {len(chunks)} phần rồi ghép lại vì dịch nguyên đoạn bị lỗi."
        if failed_chunks:
            notes += f" Một số phần chưa dịch được: {', '.join(map(str, failed_chunks))}."

        return Translation(
            translatedText="\n\n".join(part for part in translated_parts if part),
            notes=notes,
            model=", ".join(models) if models else None,
        )


def _translate_text(pali_text: str, language: str = DEFAULT_LANGUAGE) -> Translation:
    client = _client()
    errors: list[str] = []
    prompt = _translation_prompt(pali_text, json_mode=True, language=language)
    plain_prompt = _translation_prompt(pali_text, json_mode=False, language=language)

    for model in _models_for_call():
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            return _parse_translation_response(response.text or "", model)
        except Exception as exc:
            errors.append(f"{model}/json: {type(exc).__name__}: {str(exc)[:180]}")
            message = str(exc).lower()
            if any(part in message for part in ["404", "not found", "unsupported", "quota", "429", "rate"]):
                continue

            try:
                response = client.models.generate_content(model=model, contents=plain_prompt)
                return _parse_translation_response(response.text or "", model)
            except Exception as plain_exc:
                errors.append(f"{model}/plain: {type(plain_exc).__name__}: {str(plain_exc)[:180]}")
                continue

    raise RuntimeError("All Gemini text models failed. " + " | ".join(errors))


def embed_query_vector(text: str) -> str | None:
    api_key = str(settings()["gemini_api_key"])
    if not api_key:
        return None
    try:
        client = genai.Client(
            api_key=api_key,
            http_options={"timeout": int(settings()["gemini_request_timeout_ms"])},
        )
        response = client.models.embed_content(
            model="gemini-embedding-2",
            contents=text,
            config={"output_dimensionality": 768},
        )
        values = response.embeddings[0].values if response.embeddings else None
        if not values:
            return None
        return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"
    except Exception:
        return None


def summarize_section_text(section_payload: dict, language: str = DEFAULT_LANGUAGE) -> dict:
    """Summarize an entire section into key points with mapped passage IDs."""
    blocks = section_payload.get("paragraphs", [])
    if not blocks:
        return {"points": []}
        
    text_chunks = []
    for block in blocks:
        passage_ids = block.get("passageIds", [])
        pali_text = block.get("text", "").strip()
        if not pali_text or not passage_ids:
            continue
        text_chunks.append(f"[IDs: {', '.join(passage_ids)}]\n{pali_text}")
    
    full_text = "\n\n".join(text_chunks)
    if not full_text.strip():
        return {"points": []}

    from .i18n import TRANSLATION_TARGETS, normalize_language
    target_language = TRANSLATION_TARGETS.get(normalize_language(language), TRANSLATION_TARGETS[DEFAULT_LANGUAGE])
    
    prompt = (
        f"You are a Buddhist scholar. Read the following Pali text and its passage IDs.\n"
        f"Provide a comprehensive summary of the main points. BẮT BUỘC viết tóm tắt bằng ngôn ngữ: {target_language}.\n"
        f"Group multiple IDs into one summary point if they discuss the same topic.\n"
        f"If they are distinct, separate them. Ensure every point has at least one associated ID from the text.\n"
        f"CRITICAL: Keep the summary extremely concise. Do not exceed 10-15 main points to avoid timeouts.\n\n"
        f"Text:\n{full_text}"
    )
    api_key = str(settings()["gemini_api_key"])
    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": 120.0},
    )
    active_models = _models()
    model_name = active_models[0] if active_models else "gemini-2.5-flash"
    
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SectionSummary,
                temperature=0.2,
            ),
        )
        if response.text:
            import json
            data = json.loads(response.text)
            return data
    except Exception as ex:
        print("Summary generation failed:", ex)
        
    return {"points": []}

