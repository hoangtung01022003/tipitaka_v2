from google import genai
from pydantic import BaseModel

from .config import settings
from .db import execute, fetch_one


PROMPT_VERSION = "python-pali-vi-literal-v2"


class Translation(BaseModel):
    translatedText: str
    notes: str | None = None


def _client() -> genai.Client:
    api_key = str(settings()["gemini_api_key"])
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    return genai.Client(api_key=api_key)


def _models() -> list[str]:
    return list(settings()["gemini_text_models"])


def _should_try_next(error: Exception) -> bool:
    message = str(error).lower()
    return any(part in message for part in ["429", "quota", "rate", "resource_exhausted", "404", "not found", "unsupported"])


def translate_passage(passage_id: str) -> dict:
    model = _models()[0]
    cached = fetch_one(
        """
        select translated_text, notes, model
        from translations
        where passage_id = %s and language = 'vi' and model = %s and prompt_version = %s
        limit 1
        """,
        [passage_id, model, PROMPT_VERSION],
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

    translated = _translate_text(passage["pali_text"])
    execute(
        """
        insert into translations (passage_id, language, model, prompt_version, translated_text, notes)
        values (%s, 'vi', %s, %s, %s, %s)
        on conflict (passage_id, language, model, prompt_version)
        do update set translated_text = excluded.translated_text, notes = excluded.notes
        """,
        [passage_id, model, PROMPT_VERSION, translated.translatedText, translated.notes],
    )
    return {
        "vi": translated.translatedText,
        "notes": translated.notes,
        "model": model,
        "fromCache": False,
    }


def _translate_text(pali_text: str) -> Translation:
    client = _client()
    errors: list[str] = []
    prompt = "\n".join(
        [
            "Bạn là trợ lý dịch thuật Pali sang tiếng Việt cho văn bản kinh điển Phật giáo Theravāda.",
            "Dịch sát nguyên văn Pali sang tiếng Việt, ưu tiên đúng nghĩa trước văn chương.",
            "Viết thành bản dịch liền mạch, dễ đọc. Không dịch kiểu chú giải từng cụm trong ngoặc.",
            "Không chèn từ Pali trong ngoặc ngay sau mỗi cụm tiếng Việt, trừ khi thật sự cần để tránh mơ hồ.",
            "Giữ cấu trúc ý của câu gốc; không thêm ý giáo lý nếu Pali không nói.",
            "Văn phong trang nghiêm, trong sáng, không quá Hán-Việt nếu có thể nói tự nhiên.",
            "Thuật ngữ nên nhất quán: sīla=giới; dāna=bố thí; cāga=xả thí; dakkhiṇā=cúng dường; vipāka=quả báo/quả dị thục; phala=quả; puñña=phước/công đức; saraṇa=quy y/nơi nương tựa; aparappaccaya=không do người khác làm duyên/không lệ thuộc vào người khác.",
            "Nếu đoạn quá dài, vẫn dịch đủ toàn bộ, không tóm tắt.",
            'Trả JSON thuần: {"translatedText":"...","notes":"..."}',
            "",
            "Pali:",
            pali_text,
        ]
    )

    for model in _models():
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            return Translation.model_validate_json(response.text or "{}")
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            if not _should_try_next(exc):
                break

    raise RuntimeError("All Gemini text models failed. " + " | ".join(errors))


def embed_query_vector(text: str) -> str | None:
    api_key = str(settings()["gemini_api_key"])
    if not api_key:
        return None
    try:
        client = genai.Client(api_key=api_key)
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
