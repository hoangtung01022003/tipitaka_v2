import json
import re
import hashlib
from itertools import count
from threading import Lock

from google import genai
from pydantic import BaseModel

from .config import settings
from .db import execute, fetch_one


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


def translate_passage(passage_id: str) -> dict:
    active_models = _models()
    cached = fetch_one(
        """
        select translated_text, notes, model
        from translations
        where passage_id = %s
          and language = 'vi'
          and model = any(%s)
          and prompt_version = %s
        order by created_at desc
        limit 1
        """,
        [passage_id, active_models, PROMPT_VERSION],
    )
    if cached:
        return {
            "vi": cached["translated_text"],
            "notes": cached["notes"],
            "model": cached["model"],
            "fromCache": True,
        }

    passage = fetch_one("select pali_text from passages where id = %s", [passage_id])
    if not passage:
        raise RuntimeError("Passage not found.")

    translated = _translate_text_resilient(passage["pali_text"])
    model = translated.model or active_models[0]
    execute(
        """
        insert into translations (passage_id, language, model, prompt_version, translated_text, notes)
        values (%s, 'vi', %s, %s, %s, %s)
        on conflict (passage_id, language, model, prompt_version)
        do update set translated_text = excluded.translated_text, notes = excluded.notes, created_at = now()
        """,
        [passage_id, model, PROMPT_VERSION, translated.translatedText, translated.notes],
    )
    return {
        "vi": translated.translatedText,
        "notes": translated.notes,
        "model": model,
        "fromCache": False,
    }


def translate_text(pali_text: str) -> dict:
    translated = _translate_text_resilient(pali_text)
    return {
        "vi": translated.translatedText,
        "notes": translated.notes,
        "model": translated.model or (_models()[0] if _models() else None),
        "fromCache": False,
    }


def _text_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def translate_text_cached(pali_text: str) -> dict:
    active_models = _models()
    text_hash = _text_hash(pali_text)
    cached = fetch_one(
        """
        select translated_text, notes, model
        from text_translations
        where text_hash = %s
          and language = 'vi'
          and model = any(%s)
          and prompt_version = %s
        order by created_at desc
        limit 1
        """,
        [text_hash, active_models, PROMPT_VERSION],
    )
    if cached:
        return {
            "vi": cached["translated_text"],
            "notes": cached["notes"],
            "model": cached["model"],
            "fromCache": True,
            "textHash": text_hash,
        }

    translated = _translate_text_resilient(pali_text)
    model = translated.model or active_models[0]
    execute(
        """
        insert into text_translations (text_hash, language, model, prompt_version, source_text, translated_text, notes)
        values (%s, 'vi', %s, %s, %s, %s, %s)
        on conflict (text_hash, language, model, prompt_version)
        do update set translated_text = excluded.translated_text, notes = excluded.notes, source_text = excluded.source_text, created_at = now()
        """,
        [text_hash, model, PROMPT_VERSION, pali_text, translated.translatedText, translated.notes],
    )
    return {
        "vi": translated.translatedText,
        "notes": translated.notes,
        "model": model,
        "fromCache": False,
        "textHash": text_hash,
    }


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


def _translation_prompt(pali_text: str, json_mode: bool) -> str:
    lines = [
        "Bạn là trợ lý dịch thuật Pali sang tiếng Việt cho văn bản kinh điển Phật giáo Theravāda.",
        "Nhiệm vụ: dịch văn bản Pali sang tiếng Việt tự nhiên, rõ nghĩa, trang nghiêm và chính xác.",
        "Không dịch máy móc từng chữ. Hãy ưu tiên truyền đạt đúng ý nghĩa của câu Pali bằng tiếng Việt dễ hiểu.",
        "Giữ đầy đủ nội dung của văn bản gốc; không tóm tắt, không bỏ ý, không thêm ý giáo lý ngoài văn bản.",
        "Nếu câu Pali rất dài, được phép tách thành vài câu tiếng Việt ngắn hơn để dễ đọc, miễn không đổi nghĩa.",
        "Nếu văn bản thuộc dạng vấn đáp, tranh luận, phân tích pháp số hoặc định nghĩa Abhidhamma, hãy dịch theo đúng văn thể đó.",
        "Không dịch kiểu chú giải từng cụm trong ngoặc. Không chèn từ Pali sau mỗi cụm tiếng Việt.",
        "Chỉ giữ thuật ngữ Pali trong ngoặc khi thuật ngữ đó quan trọng, khó dịch hết nghĩa, hoặc cần đối chiếu học thuật.",
        "Dùng thuật ngữ Phật học tiếng Việt nhất quán, quen thuộc với truyền thống Theravāda.",
        "Với các thuật ngữ có nhiều cách dịch, hãy chọn cách dịch phù hợp nhất theo văn cảnh.",
        "Không áp dụng máy móc một bảng thuật ngữ cố định; luôn xét nghĩa theo văn cảnh Pali cụ thể.",
        "Với các đoạn lặp công thức hoặc ký hiệu lược như ...pe..., hãy dịch gọn theo đúng ý lược, không tự thêm nội dung không có trong văn bản.",
        "Văn phong nên trong sáng, mạch lạc, không quá Hán-Việt nếu có thể nói tự nhiên hơn.",
        "Nếu đoạn dài, vẫn dịch đủ toàn bộ, không tóm tắt.",
    ]
    if json_mode:
        lines.append('Trả JSON thuần, đúng một object: {"translatedText":"...","notes":"..."}')
    else:
        lines.append("Chỉ trả bản dịch tiếng Việt thuần, không bọc JSON, không markdown, không giải thích thêm.")
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


def _translate_text_resilient(pali_text: str) -> Translation:
    try:
        return _translate_text(pali_text)
    except Exception as first_error:
        chunks = _split_text_for_translation(pali_text, max_chars=TRANSLATION_RESCUE_CHUNK_CHARS)
        if len(chunks) <= 1:
            raise first_error

        translated_parts: list[str] = []
        models: list[str] = []
        failed_chunks: list[int] = []
        for index, chunk in enumerate(chunks, start=1):
            try:
                translated = _translate_text(chunk)
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


def _translate_text(pali_text: str) -> Translation:
    client = _client()
    errors: list[str] = []
    prompt = _translation_prompt(pali_text, json_mode=True)
    plain_prompt = _translation_prompt(pali_text, json_mode=False)

    for model in _models_for_call():
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
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
