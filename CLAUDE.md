# CLAUDE.md

> **Continuation note (2026-08-15):** Before extending Indacanda whole-PDF
> coverage, read `INDACANDA_FULL_HANDOFF.md`. It records the completed 3-volume
> import, current DB counts, safety invariants, the interrupted 27-volume profile,
> and the exact implementation plan for all remaining PDFs.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`python_app` is a FastAPI + Jinja2 + PostgreSQL full-stack rewrite of the Tipiṭaka (Pali Buddhist canon) search product. It serves a Vietnamese-language UI for searching Pali scripture text, with optional Gemini-powered query expansion, result reranking, and Pali→Vietnamese translation.

**This directory is its own git repository** (remote `tipitaka_v2.git`), nested inside the parent Next.js monorepo (`tipitaka/`, remote-tracked separately as `main`). It is deployed independently to a Windows VPS by `git pull`-ing this repo directly on the server — it is not built/deployed via the parent repo.

**The Postgres database is shared with the sibling Next.js app at the repo root.** Schema migrations and XML corpus import are owned by the Next.js side (`../db/migrations/`, `npm run db:migrate`, `npm run import:xml`, `npm run embed:passages` — see root `package.json`). `python_app/001_init.sql` is a snapshot copy of `../db/migrations/001_init.sql`, not a live migration — don't edit it expecting it to run automatically. Never invent new migrations here; schema changes belong in the root Next.js project.

Search is text/AI-driven by default — `PY_SEARCH_ENABLE_VECTOR=false` in `.env.example`, meaning pgvector similarity search is off unless explicitly enabled and passages have embeddings (populated by the root `embed:passages` script).

## Commands

Local dev (from `python_app/`, after `docker compose up -d` at the repo root for local Postgres):

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8001
```

Production run on the VPS (native Windows, no Docker/systemd):

```bat
run.bat          :: activates .venv, runs uvicorn on 0.0.0.0:8000
run_hidden.vbs    :: launches run.bat with no visible window (used by deploy_vps.bat)
stop.bat          :: taskkill on python.exe/uvicorn.exe to free the port
deploy_vps.bat    :: git pull origin main, pip install -r requirements.txt, relaunch via run_hidden.vbs
```

Nginx + SSL are set up separately on the VPS via `install_nginx.bat`, `start_nginx.bat`, `stop_nginx.bat`, `setup_ssl.bat`, and `download_wacs.py` (win-acme wrapper). `nginx_config/nginx.conf` / `nginx_ssl.conf` reverse-proxy `suttasearch.net` to `127.0.0.1:8000`.

There is no pytest suite and no lint/format config (no pyproject.toml, ruff, black) in this project — don't assume one exists. There are three hand-rolled check scripts, run directly with the venv Python:

```bat
python dev_check.py            :: chạy pipeline tìm kiếm in-process trên các truy vấn khách báo lỗi
python dev_fallback_check.py   :: kiểm tra bậc rút gọn từ khóa (không cần server)
python dev_http_check.py       :: kiểm tra qua HTTP; cần uvicorn đang chạy ở :8010
python dev_verify.py           :: kiểm định chất lượng, xem bên dưới
python dev_coverage.py         :: báo cáo độ phủ bản dịch theo bộ kinh
python dev_make_vi_words.py    :: sinh lại app/data/vi_words.txt (từ vựng nối chữ PDF)
python dev_reader_unit_check.py:: đo tỉ lệ "Xem toàn bộ bài kinh" mở ra trích đoạn
python dev_check_export_sql.py :: cổng kiểm export_data.sql trước khi nạp VPS
```

`dev_check_export_sql.py` chỉ đọc file SQL, thoát khác 0 khi có mục KHÔNG ĐẠT. Nó tồn
tại vì một bản dump cũ nhìn từ ngoài không khác gì bản đúng: bản 2026-08-14 thiếu
migration 006, thiếu 101 dòng `pdf_heading_boundary` và thiếu `set client_encoding`, mà
không câu lệnh nào báo lỗi khi nạp.

Phép đếm `match_method` trong file phải neo theo ĐUÔI câu insert (`E'<method>', <score>,
<batch>`), không phải `E'<tên>', ` trơn - và cả hai lỗi của cách cũ đều đã bắn báo động giả
thật: danh sách tên hardcode không thấy phương pháp mới (`label_boundary_fix` có đủ 3 dòng
trong file mà đếm ra 0), còn mẫu trơn thì khớp lây sang bảng `human_translation_imports`
(`strict_unique` đếm 61.735 trong khi DB có 61.733). Neo đuôi cũng bắt vài từ Pāli trong thân
bản dịch tình cờ trùng dạng, nên chỉ được đếm những TÊN ĐÃ BIẾT cộng tên DB đang có.

**Chạy kèm `--against-db` trước khi nạp VPS, không thì nó vẫn cho qua bản xuất cũ.** Phép
kiểm dòng `pdf_heading_boundary` vốn chỉ là `> 0`, đủ để bắt ca 2026-08-14 (thiếu HẲN) nhưng
không thấy ca thiếu MỘT PHẦN - đo thật ngày 2026-08-17: `export_data.sql` mang 125 dòng khi
DB đã có 174 (thiếu trọn 49 dòng `pr`), `indacanda_full` 264 so với 305, mà mọi mục vẫn ĐẠT
và script in "sẵn sàng nạp VPS". Một file cũ tự nó vẫn nhất quán nên không có dấu hiệu nội
tại nào để phát hiện; chỉ so với DB mới thấy. Cờ này để riêng nên mặc định vẫn giữ đúng cam
kết "chỉ đọc file, không chạm DB".

`dev_reader_unit_check.py` trả lời một câu mà phần còn lại không trả lời được:
`_canonical_reader_section` **âm thầm rơi về mục con** khi không tiêu đề nào qua được
`_is_reader_unit_title`, nên nút "toàn bộ bài kinh" có thể mở ra một trích đoạn mà không
báo gì. Rủi ro dồn vào Tạng Luật, Vi Diệu Pháp và các sách kệ - nơi tiêu đề không kết
thúc bằng `sutta(ṃ)`. Script chỉ đọc, và ghi kết quả ra JSON.

`dev_verify.py` is the quality gate, and each part answers a different question:
- **A** re-downloads the bilara source and checks every stored `segment_ids` entry really corresponds to the passage it was written to — independent of the importer's own logic, so an importer bug cannot mark itself correct. Runs for **both** `sujato` and `brahmali`; `_bilara_tree()` indexes `vinaya/` as well as `sutta/`, without which every Brahmali ref silently resolves to nothing and the check reports a blank pass. It accepts the **three** match shapes `import_sujato.align` is allowed to produce (segment inside passage / elided-prefix / passage inside segment) — a verifier that only models the first raises false alarms on the other two.
- **D** is a known-answer search suite: each query has an expected sutta that must appear within top-N. This is what catches ranking regressions; both the provenance-dedup and verbatim-retrieval bugs above surfaced here.
- **B** checks AI output is in the requested language, not truncated, and free of JSON/markdown leakage.
- **C** asks Gemini to score AI Vietnamese against Sujato's English for the same passage (`--skip-judge` to skip; it costs many API calls).

`dev_check.py` and `dev_http_check.py` hit the real DB and the real Gemini API, so results vary run to run — Gemini's query expansion and reranking are not deterministic. They are smoke checks, not assertions.

## Configuration

`app/config.py` loads `.env` from three locations in order, later ones overriding earlier: repo-root-of-repo-root `.env`, its parent `.env`, then `python_app/.env` (with `override=True`). `settings()` is an `lru_cache`d singleton — restart the process to pick up `.env` changes.

Key env vars (see `.env.example`):
- `DATABASE_URL` — Postgres connection string (same DB as the Next.js app; local default is the docker-compose Postgres, port 5432/5434 depending on setup).
- `GEMINI_API_KEY`, `GEMINI_TEXT_MODELS` (comma-separated fallback list), `GEMINI_REQUEST_TIMEOUT_MS`.
- `PY_SEARCH_AI_MODE`: `off` | `query` | `full`. `query` enables Gemini query expansion only; `full` also enables AI reranking of candidates.
- `PY_SEARCH_ENABLE_VECTOR`: toggles the pgvector candidate-retrieval branch.
- `PY_SEARCH_MIN_SCORE`, `PY_SEARCH_RERANK_LIMIT`: tune the scoring/reranking pipeline in `search_engine.py`.
- `ADMIN_USERNAME`, `ADMIN_PASSWORD`: plaintext credentials for `/admin/*` (compared with `secrets.compare_digest`, no hashing).
- `SECRET_KEY`: session-cookie signing key for `SessionMiddleware`; defaults to a random value per process if unset (admin sessions won't survive a restart in that case).

## Architecture

### Module map (`app/`)

- `config.py` — env loading, `settings()`.
- `i18n.py` — UI string catalog for **vi / en / my**, plus the localized corpus/pitaka option lists (including the `all` = "search everything" entries) and `TRANSLATION_TARGETS` (the target-language name injected into the Gemini translation prompt). `t(language, key, **kwargs)` is the accessor; `ui_strings(language)` dumps the whole catalog into the page for the client-side JS.
- `translation_sources.py` — the "which translation" selector. Six sources, all with real data: `ai` (Gemini), `minh_chau`, `indacanda`, `indacanda_full`, `sujato`, `brahmali`. `source_options()` marks availability from `sources_with_data()`, a plain (deliberately **un**cached) `select distinct source from human_translations`, so a source appears as soon as its importer finishes — no restart needed.

  | source | rows | shape | scope |
  |---|---|---|---|
  | `sujato` | 31,229 | passage | four nikāyas + verse books, English |
  | `indacanda` | 30,331 | passage | Vinaya + Khuddaka + Dīgha, Vietnamese |
  | `brahmali` | 9,639 | passage | **Vinaya only**, English — the sole English Vinaya |
  | `minh_chau` | 3,073 | whole-sutta | DN/MN/SN/AN + Iti/Ud/Snp, Vietnamese |
  | `indacanda_full` | 251 | whole-sutta | same text as `indacanda`, per discourse — **two methods, very different quality: see below** |

  `indacanda_full`'s 251 rows split 150 `whole_unit` (assembled between matched anchors) and 101 `pdf_heading_boundary` (cut at the printed headings). Only the second is trustworthy. Hand-checked against the PDF: `7. Mahāsamayasuttaṃ` (`pdf_heading_boundary`) matches ~100%, while `6. Pāsādikasuttaṃ` (`whole_unit`) carries only **71%** of the discourse — the whole of paragraph 165 has no translation at all, though its Pāli still displays beside it. And `whole_unit` fails in the other direction too: see the `1. Pāthikasuttaṃ` case below, where over half the row is two other suttas. Run `dev_whole_unit_audit.py` for the current spread.

  **Two attachment shapes, and picking the wrong reader silently returns nothing.** `sujato`/`indacanda`/`brahmali` are *passage-level*: one row per `passage_id`, the `document_id`/`start_sort_order`/`end_sort_order` columns left null — read with `_fetch_from_human_translations`. `minh_chau`/`indacanda_full` are *sutta-level*: the translator divides the text differently, so one row covers a whole discourse plus the `sort_order` range it spans. Its `passage_id` is only an anchor, **not** the sole passage covered — reading it by `passage_id` makes every other passage in the discourse miss, which is why it needs `_fetch_whole_sutta_translation` (a range lookup). Register each source under the right reader in `HUMAN_TRANSLATION_FETCHERS`; a source that is imported but not registered there stays invisible behind "Hiện không có bản dịch chính thức nào" even though the rows exist. This has already bitten once: 9,639 Brahmali rows sat in the DB invisible until the source was registered.

  `indacanda` and `indacanda_full` are the **same translation in two shapes**, deliberately kept as two sources because `human_translations` is keyed `(passage_id, source)` and one source cannot hold both shapes. Passage-level gives the correct excerpt under a search result; whole-sutta gives a continuous read. Do not "clean up" by deleting one.

  **Both stay as separate UI options with fixed labels, and that is a client decision — do not "simplify" it.** An attempt to merge them into one translator entry with a shape-suffixed label was built and rejected: the client wants the five rows exactly as listed, `indacanda` "(trích đoạn ngắn)" then `indacanda_full` "(toàn bộ bài kinh)", with those strings unchanged.

  What was actually broken is narrower. `indacanda_full` covers 251 suttas against 30,590 passage-level rows, so reading that source alone left roughly 90% of suttas showing a greyed `(chưa có dữ liệu)` option right beside a working excerpt option — the contradiction the client pointed at: *if there is an excerpt, the whole discourse must be readable too.* `WHOLE_FALLBACK_SOURCE` fixes exactly that: when no whole-discourse row exists, the "toàn bộ bài kinh" option is assembled from that same translator's passage-level rows. It applies in three places that must stay in step — `source_options` (dropdown), `sources_for_sections` (button enabled/disabled), and `official_translations_merged` (the text itself).

  **Assembly is not gated on completeness.** 40% or 90% both count as "toàn bộ bài kinh"; text lifted out of a PDF is essentially never whole, so demanding 100% is the same as offering nothing. Nothing is concealed by this: `_join_with_gap_markers` marks every hole in place and `section.passageOfficialHint` prints "N/M đoạn (Z%)" under the label. Keep both whenever you touch this — they are the only things stopping the option from overclaiming, and the tail is real (sampled discourses run to a median of 100% but as low as 4%).

  The fallback fires **only when `covers_whole_sutta=True`**, i.e. on the reader page, which passes every passage of the discourse. The results card passes only the snippet's few passages, so assembling there would reproduce the excerpt option verbatim and print the same translator twice.

  Passage-level text assembled across a sutta is joined by `_join_with_gap_markers`, **not** a bare `"\n\n".join`. Sujato matches 70-95% of segments, so a plain join glues non-adjacent paragraphs together and the reader sees an unbroken narrative that is quietly missing text — the client's "bấm xem toàn bộ thì bản dịch bị thiếu so với bản pali gốc". The percentage in the heading says *how much* is missing; the markers say *where*. Consecutive missing passages collapse into one marker so a thinly covered text doesn't turn into a forest of them.

  Sutta-level rows carry `wholeSutta: True` out of `official_translations_merged`. The Minh Châu texts average 3.4k characters and reach ~147k, so the **search results page** passes `whole_sutta_excerpt_chars=WHOLE_SUTTA_EXCERPT_CHARS` (500) and only the opening excerpt is rendered, flagged `truncated: True`; readers get the full text from the "Xem toàn bộ bài kinh · <translator>" button already under every result card, which opens `section.html`. The trim happens in `official_translations_merged`, not the template, so the HTML never carries text nobody reads — and the section page calls the same function **without** that argument, so it still receives the whole discourse.
- `notice.py` — the startup notice banner, stored in `app/data/notice.json` (not the DB, since the schema is owned by the parent Next.js repo). Editing it bumps `version`, and the browser only re-shows the banner when the version changes.
- `fallback_search.py` — progressive query-shortening used only when the main pipeline returns nothing (see below).

`import_sujato.py` (repo root, not `app/`) imports Bhikkhu Sujato's English translation — see "Human translations" below.
- `db.py` — a single `psycopg_pool.ConnectionPool` (autocommit, `dict_row` factory) plus `fetch_all`/`fetch_one`/`execute` helpers used everywhere instead of an ORM. `normalize_database_url` strips unsupported query params from `DATABASE_URL` (needed for some Supabase pooler URLs).
- `normalize.py` — Pali diacritic stripping (`normalize_pali`) and Vietnamese diacritic stripping (`strip_vietnamese`, `tokenize`). Nearly every search/translation module depends on this.
- `glossary.py` — a hand-curated `CONCEPTS` table mapping Vietnamese trigger phrases to Pali term sets (`must`/`should`/`avoid`/`phrases`) for ~17 core Buddhist concepts (sīla items, jewels, kamma, etc.). `analyze_query()` is the **local, non-AI** query analyzer — it always runs first and is the fallback when Gemini is disabled or fails.
- `query_expander.py` — Gemini-based query expansion (`expand_query_with_ai`) and AI reranking (`rerank_candidates_with_ai`), gated by `PY_SEARCH_AI_MODE`. `merge_expansion()` combines the AI result with the local `glossary.py` analysis (AI is additive, never fully replaces the local signal). Has an in-process expansion cache (`_EXPANSION_CACHE`, unbounded, process-lifetime).
- `translator.py` — Gemini Pali→Vietnamese translation with a rotating model-fallback list (`_models_for_call` round-robins via a shared cursor), tolerant JSON parsing of the model response (several regex/fallback strategies since Gemini doesn't always return clean JSON), and **two-tier DB caching**: `translations` keyed by `passage_id` (for indexed passages) and `text_translations` keyed by a SHA-256 hash of normalized text (for arbitrary/snippet text, e.g. expanded search snippets or section chunks that don't map to one passage). Long text is chunked and translated piecewise with paragraph/sentence-boundary-aware splitting when a single call fails or exceeds size limits. `embed_query_vector()` calls Gemini's embedding model (`gemini-embedding-2`, 768 dims) for the vector search branch.
- `search_engine.py` — the core search pipeline, see below.
- `main.py` — FastAPI routes: page routes returning full Jinja pages (`/`, `/section-page/{id}`), an HTML-fragment route (`/search-page`, returns a partial rendered by `results.html`/appended client-side), JSON API routes (`/search`, `/api/translate-result`, `/api/sections/{id}`, `/api/sections/{id}/translate`, `/api/sections/{id}/translate-chunk` for incremental streamed section translation, `/api/passages/{id}`), and a cookie-session-based `/admin` area (login, search history viewer, history detail, clear-history) protected by `get_current_admin` (redirects to `/admin/login` via a raised `HTTPException` with a 302 status — not a typical error response).
- `templates/` + `static/style.css` — server-rendered Jinja2, no client-side framework. The client JS (inline in `index.html`) drives everything with plain `fetch()` calls: `/search-page` and `/section-page/{id}` return HTML fragments that get injected into the DOM; `/api/translate-result` and `/api/sections/{id}/translate-chunk` return JSON consumed to progressively render translations.

### Search pipeline (`search_engine.py`)

`search_passages(query, corpus_types, pitaka_type, page, page_size, include_translations)` is the entry point (called by both `/search` and `/search-page`). Flow:

1. **Query analysis**: `glossary.analyze_query()` (local) merged with `query_expander.expand_query_with_ai()` (optional, Gemini) via `merge_expansion()` — produces `mustHavePali`/`shouldHavePali`/`avoidPali`/`expandedQueries`.
2. **Candidate retrieval** (`_retrieve_candidates`): up to four independent Postgres queries against `passages`, scoped to `document_id`s resolved from `corpus_types` (`mul`/`att`/`tik`/`nrf`) and an optional `pitaka_type` filter (matched via `file_name like` prefixes in `PITAKA_PREFIXES`, since there's no dedicated pitaka column). Pass `corpus_types=["all"]` / `pitaka_type="all"` (or `None`) to search everything — `resolve_corpus_types` / `resolve_pitaka_type` normalize this:
   - **verbatim match**: a direct `normalized_pali like '%segment%'` per sentence, served by the existing `passages_normalized_pali_trgm_idx`, **ordered by passage length ascending**. Shortest-first matters: a gāthā line *is* the quote, whereas a long commentary paragraph merely contains it. Without this branch the candidate cap (`_candidate_limit`) fills up with long commentary passages and the canonical short verse never reaches scoring at all.
   - **quote match** (`_tsquery_for_quote`): ANDs the distinctive tokens of each *sentence* of the user's input, OR-ing the sentences together. Catches a pasted excerpt even when word order or spacing drifted.
   - phrase match via `to_tsquery('simple', ...)` built from multi-word expanded phrases (`<->` proximity operators) **and from each sentence of the raw query**. Chains longer than `PHRASE_CHAIN_MAX_TOKENS` are cut into overlapping windows — a single long `<->` chain can only match if every token sits inside one `passages` row, which fails for gāthā verses (each line is its own row).
   - single-term match via `to_tsquery` gated on `mustHavePali` **plus compound stems** (`_compound_stems`: Pali compounds share a head, so `alagaddūpama` is truncated to `alagadd` to also reach `alagaddatthiko`). If the must-gate returns nothing, it retries with the broader keyword set — an over-narrow `mustHavePali` from Gemini used to zero out the whole branch.
   - optional pgvector cosine-similarity search (only if `PY_SEARCH_ENABLE_VECTOR=true`, an embedding can be generated, **and `_has_embeddings()` finds at least one row with an embedding** — otherwise every search wasted a Gemini embedding call for nothing).
   Each branch scores rows via `_score()`, a weighted blend of keyword hit ratio, "concept" (must/should term coverage), term proximity, DB-reported hit count, semantic similarity, and `_quote_score` (how much of what the user actually typed the row covers).

   **Critical invariant:** `glossary.analyze_query` puts the query's own distinctive tokens in `queryTerms` / `querySegmentTerms` (only when `pali_ratio(query) >= 0.5`, so Vietnamese text isn't mistaken for Pali). Retrieval falls back to these when the glossary matched no concept and Gemini returned nothing — without it, a query like `Sona` produced zero candidate terms and therefore zero results even though hundreds of passages contain the word.
3. **Dedup + threshold**: candidates are deduped by `text_hash`, filtered by `PY_SEARCH_MIN_SCORE` (falling back to the unfiltered set if everything gets filtered out), then sorted.

   **Duplicate resolution is by provenance, not score** (`_prefer_duplicate` / `CORPUS_PROVENANCE_RANK`). Commentaries quote the root canon verbatim, so one passage of text can exist byte-identical in `mul`, `att`, `tik` and `nrf` under a single `text_hash`. Keeping the highest-scoring copy meant the citation shown to the reader pointed at a commentary instead of the sutta the text actually comes from — `Sabbe saṅkhārā aniccāti…` exists in 7 documents and the Theragāthā original was being dropped in favour of an Aṭṭhakathā copy. Since the text is identical, the only thing to choose between them is provenance: prefer `mul` > `att` > `tik` > `nrf`, and only fall back to score within the same corpus. In a single-corpus search this is a no-op.
4. **AI rerank** (optional, `PY_SEARCH_AI_MODE=full`): top N (`_rerank_limit`, capped by `PY_SEARCH_RERANK_LIMIT`) go to `rerank_candidates_with_ai`, whose relevance score is blended 70/30 with the original score. Items Gemini didn't return are kept as a tail, still sorted by original score.
4b. **Exact-quote priority** (`_apply_exact_quote_bonus`): a row containing a whole sentence of the user's input verbatim gets `EXACT_QUOTE_BONUS` added per matched sentence (plus a bonus for the *leading* sentence). Pasting an excerpt is a lookup, not a conceptual question, so the literal match has to outrank whatever the AI reranker preferred. `EXACT_QUOTE_CORPUS_BONUS` adds a further root-canon preference **on exact matches only**, so a famous verse quoted across dozens of commentaries still resolves to the sutta it comes from; conceptual queries are untouched.

   Those two are **constants**, which is enough only while the competitors sit in different corpora. `Sabbe saṅkhārā aniccā` sits verbatim in 140 passages, **60 of them `mul`** (Niddesa, Paṭisambhidāmagga, Nettippakaraṇa are all canonical), so both bonuses lifted every candidate by the identical amount and decided nothing; `_quote_score` saturates at 1.0 the moment a row contains the words, so it could not separate a 44-character verse from a 1,200-character paragraph quoting it either. With no discriminating signal the base score decided, and it favours **long** rows (more term hits, wider concept coverage) — backwards for a verbatim lookup, where the shortest row containing the quote *is* the quote. `EXACT_QUOTE_DENSITY_BONUS` supplies the missing signal as a ratio (quote length ÷ passage length): ~0.5 for the verse itself, ~0.02 for a paragraph citing it. The verbatim retrieval branch already fetched shortest-first, so the right rows were always in the candidate pool — only the ranking discarded them.

4c. **The sutta named after the concept** (`_names_the_concept`). Asking "bốn niệm xứ" used to return Vibhaṅga, Peṭakopadesa and Niddesa passages while Mahāsatipaṭṭhānasutta sat at **rank 186** — those texts *define* the term, so they mention it densely, whereas each passage of the discourse itself says it once and then expounds at length. A density-based score ranks that exactly backwards. The fix adds a bonus when a Pali term from the query appears in the result's `sourcePath`, split two ways because one level is not enough: `CONCEPT_TITLE_BONUS` (0.70) when any ancestor names the concept, `CONCEPT_TITLE_BONUS_SUTTA` (1.50) when the **sutta's own title** does. Without the split, all hundred-odd suttas of the Satipaṭṭhānasaṃyutta got the same lift and the actual discourse still never surfaced. 1.50 rather than 1.10 because at 1.10 the margin was 0.09 and the AI reranker's jitter flipped the result run to run. Terms shorter than `CONCEPT_TITLE_MIN_TERM` (6) are ignored — `sati` and `jhāna` sit inside too many titles to mean anything.

   This also fixed queries nobody had reported: `vô thường khổ vô ngã` moved from Niddesa commentary to `Uppādāsuttaṃ` / `Bāhirāniccasuttaṃ`, and `mindfulness of breathing` now returns `Ānāpānassatisuttaṃ` in all three top slots.

   The bug had been sitting behind a **weak test**: `dev_verify` part D checked the pattern `satipatthana`, which any passage merely *mentioning* the term satisfies, so the case reported OK while the canonical sutta was nowhere in the top 15. An outside tester caught it. The pattern is now `satipatthanasutt|mahasatipatthana` — when writing a known-answer case, make the pattern name the answer, not the topic.

   `EXACT_QUOTE_MIN_TOKENS` is 3, not 4: at 4 the best-known three-word gāthās were not treated as quotes at all. Going below 3 is not safe by the same argument — but 3 is, because `querySegmentTexts` is only populated for Pali-looking queries (`analyze_query` gates on `pali_ratio`), so Vietnamese three-word questions never reach this path, and a row earns the bonus only if it contains the literal string.
5. **Collapse + diversification** (`_collapse_adjacent`, then `_diversify_results`): consecutive rows of the same section collapse to the highest-scoring one (a multi-line paste matches several adjacent gāthā rows, and snippet expansion re-joins them anyway); near-duplicate passages (by Jaccard similarity of "content words" and/or shared source path) are pushed later in the list rather than dropped.
6. **Pagination + snippet expansion**: short passages get neighboring passages from the same section stitched in (`_expand_short_snippets`) up to `SNIPPET_MAX_CHARS`, so the UI shows enough context.

   **Page 2 must never re-rank.** Steps 1-5 live in `_rank_candidates`, which returns the whole ranked list; `search_passages` only slices it. "Hiển thị thêm" used to re-run the entire pipeline with `page=2`, and two things made that list differ from page 1's: the AI reranker is not deterministic, and `_candidate_limit` multiplied its pool by `page` (600 candidates for page 1, 800 for page 2) so dedup/collapse/diversify ran over a different input. Measured on one query: page 1 + page 2 returned 10 rows of which only **9 were distinct**, and one row that belongs in the top ten was never shown to the reader at all — the duplicate is visible and annoying, the dropped row is silent and worse. Fixed by making `_candidate_limit` page-independent (the ranked list already holds 234-388 rows, i.e. 46-77 pages) and caching the ranked list per `(query, corpus, pitaka, language)` in `_RANKED_CACHE` for 10 minutes, so every page cuts from one list. `_page_results` deep-copies its slice because everything downstream — snippet expansion, translation attachment, `_strip_internal_fields` — mutates the items in place, and the cached list has to survive for the next page.
7. **Translation attachment** (optional per-call): `translate_passage`/`translate_text` per result.
8. **Zero-result fallback** (`fallback_search.run_fallback`): if page 1 came back empty, the query is progressively shortened (drop filler words → keep the 3 / 2 / 1 most-repeated content words) and re-searched, stopping at the first rung that returns anything. The response then carries `fallback: {used, usedQuery, triedQueries, ladder}` so `results.html` can tell the reader the results came from a shortened keyword. Inner calls pass `allow_fallback=False, log_search=False` to avoid recursion and duplicate log rows.
9. Every search call inserts a row into `search_logs` (`_insert_log`) — this powers `/admin/history`. There is no way to search without logging; keep that in mind if adding new search-triggering code paths.

Source-path display (`_display_source` and the `_looks_like_source_noise` / `_looks_like_heading_title` heuristics) is a set of regex/heuristic rules for cleaning up noisy XML-derived section titles (e.g. stripping "niṭṭhitā"/"samattā" colophons, detecting real heading-shaped text vs. sentence fragments). If source-path display looks wrong for a corpus, this is where to look — it's heuristic, not authoritative metadata.

### Language handling

`main.request_language(request, override)` resolves the UI language from, in order: an explicit request parameter (`?lang=` / the `lang` form field / a `language` JSON key), the `lang` cookie, `Accept-Language`, then `vi`. `GET /` writes the cookie back.

The selected language is threaded all the way through: `search_passages(..., language=)` localizes match reasons and the AI-translation warning, and `translate_passage` / `translate_text` / `translate_text_cached` take a `language` argument that becomes both the target language in the Gemini prompt **and** the `language` column value used for cache lookup and insert. No schema change was needed — `translations` and `text_translations` already key on `(…, language, model, prompt_version)`; the old code just hardcoded `'vi'` everywhere.

Translation payloads carry both `text` (language-neutral) and `vi` (the original key name, kept so existing templates/JS keep working).

### The whole-discourse reader unit (`_canonical_reader_section`)

**Superseded for the production reader on 2026-08-21.** The reader now uses the exact
deepest section referenced by `passages.section_id`; it does not promote by title suffix
or ancestor range. `_canonical_reader_section` and the tier analysis below remain only
for historical audit/import scripts. Do not wire that helper back into `_section_payload`
or `_reader_section_by_id`: doing so changes `Gabbhinīupamā` (3 passages) back into all of
`Pāyāsisuttaṃ` (56), and `Sudinnabhāṇavāro` (23) back into all of
`Paṭhamapārājikaṃ` (140), contrary to the current reader contract.

`passages.section_id` points at the deepest subsection, so "Xem toàn bộ bài kinh" has to climb before it can show a discourse. The climb is **two-tier**, and the second tier is what makes the button honest:

1. The nearest ancestor whose title passes `_is_reader_unit_title` — a real discourse.
2. Failing that, **and only if the landing section is smaller than `READER_FALLBACK_MIN_PASSAGES` (20)**, the nearest ancestor regardless of title, capped at `READER_FALLBACK_MAX_PASSAGES` (1500).

Tier 2 exists because the client asked for "không có full thì gần full cũng được". Without it the function fell back to the deepest subsection — median **10** passages — so the reader pressed "whole discourse" and got a scrap.

**Both bounds on tier 2 are load-bearing, and each was put there by a failure, not by taste.**

The upper cap: the only ancestor of a Jātaka in `s0514m` is the whole Mahānipāta at 13,618 passages — neither a discourse nor a page anyone can load.

The lower bound is the subtler one, and tier 2 shipped without it and was wrong. A first version climbed unconditionally. QA then found that searching `Mahāpadāna` opened a page titled **`Dīghanikāyo`, 1,361 passages, showing Mahāparinibbānasutta**. The report diagnosed it as "tier 2 overriding tier 1"; that was the wrong diagnosis and worth understanding, because the real cause is easy to reintroduce. The matched passage is the *uddāna* at `sort_order` 1 of `s0102m` — `Mahāpadāna nidānaṃ, nibbānañca sudassanaṃ` — the verse that lists the volume's contents. It belongs to no sutta, so tier 1 finding nothing was **correct**. Its section is `Mahāvaggapāḷi` (1,361 passages), and `Dīghanikāyo` is **coextensive with it** — same 1,361. Climbing therefore added not one passage of content and only replaced a correct title with a broader one. Unconditional climbing made the page strictly worse than the pre-tier-2 behaviour.

Threshold 20 was measured, not guessed. Fragments (a non-reader-unit result under 20 passages, i.e. the thing the client complained about) fall from **5.2% to 0.7%** of translated passages — identical to climbing unconditionally — while pages over 500 passages drop from 106 to **76** and pages over 1,000 from 28 to 19. Raising it to 50/100/200 removes no further fragments and only manufactures heavier pages.

The returned row carries `_readerUnitExact` saying which tier fired. **The UI deliberately ignores it**: the client chose one fixed label, "Bản gốc Pali trọn bài kinh", for every case. Keep the flag anyway — it is a real behavioural boundary, the tests pin both tiers through it, and the pending Jātaka importer pass needs to read it.

`dev_reader_unit_check.py` holds a hand-copied replica of this function so it can measure 30k sections without one query each. **If you change the tiers here, change the replica too** — a replica that has drifted reports numbers about code that no longer exists.

It also measures the wrong axis for the bug below: it reports *which tier fired*, never *how big the returned page is*. A tier-1 "confident" hit can still be a 616-passage chapter, so a clean report from it is not evidence the reader got a discourse.

#### A numbered landing section is a unit, not a fragment (tier 2 must not climb past it)

Tier 2 only runs when *no* ancestor is a recognised unit — i.e. when there is no evidence at all about where the reading unit is. A leading number in the landing section's own title is that evidence: the editor placed it in an **enumerated series**, so it is one member of that series.

The client's case: paragraph 659 sits at `13. Appamaññāvibhaṅgo -> 1. Suttantabhājanīyaṃ -> 2. Karuṇā`. `1. Suttantabhājanīyaṃ` (53 passages) holds exactly four children — `1. Mettā` / `2. Karuṇā` / `3. Muditā` / `4. Upekkhā`, 13 passages each, the four immeasurables. `2. Karuṇā` is complete in itself, but 13 < `READER_FALLBACK_MIN_PASSAGES` so tier 2 climbed and the reader got all four.

**No size floor here, and that is the lesson this session already paid for once.** A floor recreates exactly the sibling inconsistency `_READER_SIZE_SENSITIVE_SUFFIXES` produced: measured, `2. Vedanākkhandhaniddeso` (16) would stay while `4. Saṅkhārakkhandhaniddeso` (1) would climb, though both are members of the same five-aggregate series and differ only because the commentary is terse in one place. A label that matches its content is correct even at one passage.

Measured: 5,910 sections stop climbing (21,287 passages), 2,436 of them one passage long; they previously opened a page of **median 73 passages**. Samples confirm the direction — the five aggregates now open individually instead of as one 53-passage block, `151. Rājovādajātakaṃ` opens the jātaka instead of `1. Daḷhavaggo` (72 passages, ~10 jātakas), and Paṭṭhāna opens one pair-combination instead of a 273-passage table. Dīgha is untouched: `Pubbenivāsapaṭisaṃyuttakathā` → `1. Mahāpadānasuttaṃ`, `Paribbājakakathā` → `1. Brahmajālasuttaṃ`, both still tier 1.

**`dev_reader_unit_check.py` reports this change as a regression, and that is a flaw in the metric, not in the change.** It counts "fell back to a subsection" as a failure, so a section that deliberately stays put is scored as broken: `mul` reads 20.2% → 26.7% "hỏng". Judge this behaviour by sampling real sections, not by that percentage.

While sampling the Jātaka cases, a second instance of an already-documented bug surfaced: `_READER_TRAILING_ORDINAL` stripped `(6)` but not the **multi-level** printed form `(2-1-1)` (nipāta-vagga-position), so 264 Jātaka sections still had `jātakaṃ` masked and missed tier 1. Widening it to `\(\d+(?:[-–]\d+)*\)` gains those 264 and loses **0**, all in `mul` — `151. Rājovādajātakaṃ (2-1-1)` is now a tier-1 exact hit rather than relying on the enumerated-title fallback.

#### Commentary corpora take the deepest section; only `mul` climbs

Four consecutive client reports were the same problem wearing four different naming conventions (`kathā` numbered, `kathā` unnumbered, an over-wide recorded range, `vaṇṇanā` over a core in the needs-a-number group). Enumerating naming conventions never finishes on this corpus, so the rule is now by **corpus**: `att`/`tik`/`nrf` return the landing section as-is (`_READER_DEEPEST_CORPUS`), `mul` keeps the two-tier climb.

The justification is how the texts are read, not convenience: a commentary is read section-by-section against the root text, so its reading unit is *finer* than the canon's. `Vaggumudātīriyabhikkhuvatthuvaṇṇanā` (14 passages) is a unit, not a scrap of `4. Catutthapārājikaṃ`; whereas `Pubbenivāsapaṭisaṃyuttakathā` really is a scrap inside `1. Mahāpadānasuttaṃ` and the client insisted on the sutta.

Measured: passages opening an excerpt instead of a unit fall **223,863 → 39,280**; `att`/`tik`/`nrf` go to **0%** while `mul` stays exactly 24.7%. Pages under 50 passages rise 162,681 → 178,373. The 1500+ bucket does not move (35,704) — those are `nrf` books with **no subsections in the data at all** (`Jinavaṃsadīpaṃ`: one section for 9,896 passages), which only a re-import can split.

A missing `corpus_type` must fall through to the `mul` path. Test fixtures omit it, and guessing the other way would silently change the canon.

#### A citation must point at one place: duplicate `source_path` needs an occurrence marker

`abh02t.tik.xml` holds **two complete commentaries back to back** (passages 0–863 and 864–1649), each covering all 18 vibhaṅgas. So it has two `Viññāṇapadaniddesavaṇṇanā` sections (308–351 and 1188–1225) with **different text and character-identical `source_path`** — the reader saw two different works under one address with no way to tell them apart. Corpus-wide: **1,760 groups** share document + title + full path; the worst is `1. Paccayānulomaṃ` **72 times** in `abh03m10.mul.xml`.

`_duplicate_path_ranks` ranks them by `start_sort_order` (printed order, not arbitrary) and `_with_path_occurrence` appends `(chỗ N/M)`.

**Scope, measured — this is a 6% problem, not a dominant one:** 1,760 groups covering 7,570 sections, but only **27,537 of 463,175 passages (5.95%)** of what readers actually touch. `mul` 18,599 · `tik` 7,925 · `nrf` 936 · `att` 77.

**The wording must stay positional, and the reason is in that breakdown.** The two kinds of duplicate are not the same thing:
- *Two separate works in one file* — `abh02t.tik.xml` really holds `Vibhaṅga-mūlaṭīkā` (0–863) and `Vibhaṅga-anuṭīkā` (864–1649). Only ~1,173 passages.
- *One title recurring inside one work* — Paṭṭhāna's combinatorial structure: `1. Paccayānulomaṃ` ×72, `7. Pañhāvāro` ×59, `Paccayacatukkaṃ` ×59. This is the **majority** (~16,300 passages in `abh03m*`).

So calling it "bộ" (a volume/work) would be actively wrong for most of it. `chỗ N/M` / `occurrence N/M` claims only position, which is all the data supports.

Two things worth keeping:

- **Batch it.** One query for the whole results page, not one per row; the results card resolves up to 10 paths.
- **Append nothing when the path is unique.** Verified over 4,000 random sections: 763 got a marker (exactly those in duplicate groups) and **3,237 were byte-identical**. The test pins both directions.

#### `source_path` is repaired at display time (`_fix_source_path` / `_prune_path`)

The import leaves citation paths that **cannot be followed**. The client hit it exactly: walking `Vajirabuddhi-Ṭīkā -> Ganthārambhakathā -> Vinayapiṭake -> Vajirabuddhi-ṭīkā` finds no `4. Catutthapārājikaṃ` anywhere. Three repairs, each with a concrete cause:

| rule | evidence | elements changed |
|---|---|---:|
| element **repeats** an earlier one (case-insensitively) | `Vajirabuddhi-ṭīkā` repeats `Vajirabuddhi-Ṭīkā` | 788 |
| **work name assigned per file** | `abh02t.tik.xml` holds `Vibhaṅga-mūlaṭīkā` (0–863) *and* `Vibhaṅga-anuṭīkā` (864–1649), yet both halves were labelled `Vibhaṅga-Mūlaṭīkā` | 208 |

The work-name repair only fires when the real work title and the path element **share a book stem** (`Vibhaṅga`) and the element itself ends in a work suffix — without that guard a valid book name could be replaced by an unrelated containing work. The real name comes from a section in the data, never invented.

Measured before wiring it in: **34,133 sections unchanged, 3,040 repaired** (6.57% of reader-touched passages). Re-verified through the live function on 3,000 random sections: 2,761 byte-identical.

**Two more rules were written, measured, and removed. Do not re-add either.**

*"Drop elements that span the whole document"* — rejected before shipping. It removes `Vinayapiṭake`, but it also deleted **book names**: `Abhidhammapiṭaka -> Dhammasaṅgaṇīpāḷi -> Mātikā -> 1. Tikamātikā` became `Abhidhammapiṭaka -> Mātikā -> 1. Tikamātikā`. A book-level section legitimately spans its whole document.

*"Drop elements that do not contain this section's range"* — **shipped, then reverted.** It cleaned 2,515 junk elements (`Ganthārambhakathā` 0–34 against a section at 529–530), and my verification only sampled the *changed* rows for the two reported cases — so it missed that the same shape deletes **legitimate book names whose range is truncated**. The client found both: `Milindapañhapāḷi` recorded 16–117 while its child `1. Vessantarapañho` sits at 1074–1109, and `Abhidhammatthasaṅgaho` recorded 0–743 with a child at 1188–1225. Rescuing them with a corrected extent (run to just before the next same-or-shallower-level section) does **not** work — both stay at 16–117 and 0–743 because a same-level section follows immediately. With the available data there is **no signal separating "book with a truncated range" from "bogus preface"**; they are the same shape. Leaving one redundant element beats deleting a book name.

**Lesson for measuring this kind of change:** counting how many rows changed is not verification. Sample the *dropped* items and ask whether each deserved to go — the 2,515 number looked like a win precisely because nobody looked at what was inside it.

**Known residual:** a preface whose recorded range genuinely *does* contain the target survives — `vin09t.nrf.xml` has two `Ganthārambhakathā` sections (0–413 and 414–1626) and the second really contains `1. Musāvādavaggo` (1074–1098), so the range test keeps it. Separating that from real nesting needs a signal none of these three rules provide. `Vinayapiṭake` also stays for the same reason: it is a true ancestor by range, merely redundant with the corpus label.

Both display points go through the batched `_fixed_paths_for_sections` (two queries per page, and it looks up **only the titles present in the paths** — a Paṭṭhāna document has thousands of sections while a path has under ten elements).

#### Commentary titles must be read *through* their `vaṇṇanā` suffix

Aṭṭhakathā and Ṭīkā name a section "the explanation of X": `Mettāsahagatasutta` becomes `Mettāsahagatasuttavaṇṇanā`. That suffix hides the real one, so before this was fixed **no commentary section counted as a reader unit at all** — 374 of them in `att`, 538 in `tik`, across 189,533 passages. The client hit it directly: paragraph 235 sits in `4. Mettāsahagatasuttavaṇṇanā` (8 passages), tier 1 missed, tier 2 climbed to `6. Sākacchavaggo` (66 passages), and the outline offered all six sutta commentaries of the vagga.

`_reader_core` strips a trailing `vaṇṇanā` and the existing rules are re-applied to the core — **the suffix lists are not widened**, which is what keeps chapters out:

| title | core | verdict |
|---|---|---|
| `4. Mettāsahagatasuttavaṇṇanā` | `…sutta` | unit, decisive class |
| `1. Suddhabrahmacariyakathāvaṇṇanā` | `…kathā` | unit, context class |
| `1. Bhūmivaggavaṇṇanā` | `…vagga` | refused — a chapter is still a chapter |

Measured: units go att 374 → **2,953**, tik 538 → **2,169**, nrf 688 → 707, and **`mul` does not change at all** (the canon does not use this naming, so the canon carries no risk). Zero chapter titles admitted, checked explicitly against `vagga`/`nipāta`/`saṃyutta`/`kaṇḍa`/`paṇṇāsaka` + `vaṇṇanā`. Passages opening an excerpt instead of a unit: 265,734 → **231,975**, with `att` falling 75.4% → 48.7% and `tik` 61.5% → 55.7%.

`_is_context_dependent_reader_title` has to use the same core, or `…kathāvaṇṇanā` would outrank the `…suttavaṇṇanā` that contains it.

#### Why the tier-1 suffix list has two classes (`kathā`/`khandhaka` are context-dependent)

`kathā` and `khandhaka` mean different things depending on where they sit, so a single flat suffix list cannot place them:

- In the Dīgha Nikāya a `kathā` is a **subsection of a sutta** (`Pubbenivāsapaṭisaṃyuttakathā`, 32 passages, inside `1. Mahāpadānasuttaṃ`, 204). Treat it as a unit and the reader presses "toàn bộ bài kinh" and gets a fragment.
- In the Mahāvagga a `kathā` **is** the unit. All 69 level-4 children of `1. Mahākhandhako` are `kathā` or `vatthu`, spanning 1 passage (`42. Sikkhāpadakathā`) to 69 (`5. Brahmayācanakathā`). There is nothing finer to climb to.

So tier 1 runs **two passes over the same ancestor list** (still sorted shortest-first): the first accepts only decisive suffixes (`sutta`/`sikkhāpadaṃ`/`jātaka`/`vatthu`/…), the second accepts `_READER_CONTEXT_SUFFIXES`. A `kathā` inside a sutta therefore loses to the sutta even though the sutta is far larger — **size must not decide this**, which is the trap the previous attempt fell into.

**The previous attempt was a size floor, and it produced the exact bug it cited.** `_READER_SIZE_SENSITIVE_SUFFIXES` gave `kathā` a floor of `READER_FALLBACK_MIN_PASSAGES`; the 1-passage `42. Sikkhāpadakathā` was therefore skipped in tier 1 and the loop climbed to `1. Mahākhandhako` — 616 passages, 234,250 characters of Pāli, **76 AI translation chunks**, and an assembled Indacanda text covering only 255/616 (41%) so the rest is gap markers. The client reported it twice, the second time after the floor was in place. Its own code comment named this case as its motivation.

The tell was never size, it was **sibling inconsistency** — three consecutive printed sections, all visible in one client screenshot:

| section | span | before | after |
|---|---:|---|---|
| `41. Rāhulavatthu` | 3 | itself | itself |
| `42. Sikkhāpadakathā` | 1 | `1. Mahākhandhako` (616) | itself |
| `43. Daṇḍakammavatthu` | 4 | itself | itself |

31 of those 69 children climbed to the 616-passage page and 38 did not, decided by `vatthu` (no floor) vs `kathā` (floor). 1-passage `vatthu` units had shipped all along and were never complained about.

Measured before changing, and this is the check that matters: **501 sections change reader unit (2,989 passages), and none of them are in the Dīgha Nikāya.** `s0101m`/`s0102m`/`s0103m` do not appear at all, because every `kathā` there has a sutta ancestor in the decisive class. The changes land exactly where `kathā` really is the unit — `abh03m3` (Kathāvatthu) 197, `vin12t` 166, `vin02m2` 91, `vin02m3` 14, `s0517m` (Paṭisambhidāmagga) 13. Corpus-wide the size distribution barely moves (600+ pages 16.6% → 16.5%) because those books are a small share of 463,175 passages; for the affected books it is decisive.

`dev_reader_unit_check.py` moves in the right direction on every axis: sections that are themselves the unit 6,503 → **7,039**, sections falling back to a subsection 3,302 → **3,267**, passages opening an excerpt 266,126 → **265,734**. Also checked against applied data: all 20 `indacanda_full` rows in `s0517m` still line up with a reader unit exactly, and none of the narrowed units sits inside a wider applied row — so no row became unreachable or over-wide.

`khandhako`/`khandhakam` moved into the context class rather than out of the suffix list, because `_is_reader_unit_title("2. Uposathakkhandhako")` is pinned by a test and is right: when a passage lands directly on a khandhaka with nothing named inside, the khandhaka *is* the only unit.

**Do not reach for the printed `niṭṭhita` colophon as a completeness signal — it is not in the DB.** The idea is tempting (CST prints `Sikkhāpadakathā niṭṭhitā.` right after paragraph 106, the same marker `indacanda_full_extract.find_last_boundary` keys on) but passage 492 ends at `…sāmaṇerehi sikkhitunti.` and only 22 passages in all of `vin02m2` contain `niṭṭhit`. The importer kept headings and dropped colophons, so any such rule means re-reading the source XML. What *is* in the DB is the section row: a named 1-passage section is the editor's own statement that it is a unit.

**Two unit definitions still disagree.** On those 69 sections `main._is_reader_unit_title` accepts 53 while `import_indacanda._is_whole_unit_title` accepts 31 — the 22 `kathā` titles are reader units but not importer units. The rejection list further down this file is the *importer's*. Reconcile them before `mv1`/`mv2` are imported, or PDF-cut rows will be keyed to ranges the reader never displays.

**Still open from the same client report:** the results card shows "có bản dịch ở phần khác của bài kinh" via `elsewhereOnly`, computed over the reader range. With the range down from 616 passages to 1, that notice becomes "chưa có bản dịch" instead. The client asked for the opposite: use AI to pick the passage-level translation that best matches the displayed Pāli snippet and render it inline. `align_minhchau.py` is the existing groundwork.

### Reader navigation: jump to the match, and an outline built from the section tree

The client asked for "AI tóm tắt từng phân đoạn rồi tạo nút bấm" over the large page. Measured before building it, that shape is the expensive way to get the small part of it that readers need: **888 reader pages of ≥50 passages in `mul` hold 32.2M characters of Pāli, so summarising the bodies is ~8,957 Gemini calls** — to restate what the printed headings already say, with scripture as the thing being paraphrased. Three cheaper layers cover the same need, and they ship independently:

1. **Jump to the matched passage** — free, works on every page. This is the client's own Ctrl+F analogy, and it was the missing piece: the results card already carried `data-passage-id`, but only `data-section-id` was passed to `openSection`, and `section.html` had no anchors at all. `_section_paragraphs` now returns per-block `{anchor, passageIds, sortOrder}` and the template emits one `<p class="pali" id="doan-N">` per block.
2. **Outline from the section tree** (`_section_outline`) — free, no AI, no hallucination, because the entries *are* the editor's printed headings. Covers 295 of those 888 pages; `1. Ñāṇakathā` (499 passages) yields 48 entries, `1. Mahākhandhako` (616) yields 68.
3. **Vietnamese labels** (`_outline_labels`, `GET /api/sections/{id}/outline-labels`) — the part the client actually needed, since `1. Bodhikathā` is unreadable for the intended audience. **One batched call per page, not one per entry**, cached by `translate_text_cached`: 68 labels in a single request, 29s cold → **93ms warm**. Fetched lazily on first outline expand, so opening a long discourse costs nothing unless the reader opens the outline. Output matches what the client asked for almost verbatim — they wrote "đoạn 1 nói về sự giác ngộ", it returns "1. Câu chuyện về sự giác ngộ (Bodhikathā)".

Four things here are load-bearing and easy to undo by accident:

- **`paliText` must stay a flat string.** It is the input to `_chunk_section_text` for AI translation, so changing it changes how chunks are cut. `_join_section_passages` is now derived from `_section_paragraphs` precisely so the gāthā-merge rule lives in one place — verified byte-identical against the old implementation on 60 random sections, 52 of which contain gāthā rows.
- **A merged gāthā block must keep every `passage_id` it swallowed.** The matched passage can be the third verse line inside one block; a block carrying only its first id silently fails to resolve and the button does nothing.
- **`revealReaderBlock` has to un-collapse `.readerBody` first.** It ships `collapsed` (max-height 62vh), so scrolling to a target inside it looks like a dead button.
- **`data-passage-id` belongs on `<article class="result">`, not only on `.translationMount`.** The mount is a *sibling* of the "Xem toàn bộ bài kinh" button, so `closest()` never sees it. Card selection for translation goes through `[data-translation-card]`, so the extra attribute is inert there.

`_outline_labels` returns `{}` when the model's line count disagrees with the title count, and the UI then keeps the Pāli headings. A mislabeled outline is worse than an untranslated one: the reader taps "the part about X" and lands somewhere else.

**Pre-existing crash found while testing this, now fixed.** `resolve_pitaka_type` passed any unrecognised string straight through, and `_pitaka_sql` then emitted `like any(%s)` while dropping the parameter, so the *whole search* 500'd with `the query has 2 placeholders but 1 parameters were passed`. `pitaka_type` is a plain form field, so a stale open page, a renamed key, or `Vinaya` with a capital V was enough. Valid keys are `vinaya`/`sutta`/`abhidhamma`; unknown values now resolve to `None`.

Two things about that fix are deliberate. **`None` rather than an error, and no case-folding:** both alternatives guess at what the sender meant, and `None` is the only direction that cannot show *less* than before — those inputs previously returned an exception, i.e. nothing. **The invariant to test is `sql.count("%s") == len(params)`**, not the resolved string; that is what actually broke, and the test pins it in both directions.

Proven, not assumed: document ids were captured for 30 combinations (3 corpus sets × 10 pitaka values) before and after — **21 identical id-for-id, 9 crashes fixed, 0 changed results.** Verify that way again if this function is ever touched.

`_params_with_pitaka` is the one function that could have mis-filtered *silently* instead of crashing, by shifting parameter positions. It is **dead code** — defined, never called. Leave it alone or delete it, but do not start calling it without re-checking the placeholder/parameter count.

**Not built, and deliberately so:** AI summaries of passage bodies.

#### The giant reader pages are uddāna, not readers losing their place

Worth reading before "fixing" them, because the first description of this (written one turn earlier in this file) was wrong twice. It said tier 2 "refuses to climb because they exceed `READER_FALLBACK_MAX_PASSAGES`" — tier 2 never runs at all, since it requires the landing section to be *under* `READER_FALLBACK_MIN_PASSAGES` and these are thousands of passages. And it implied readers were landing on 4,000-passage pages in numbers.

Measured instead: **637 of 159,007 `mul` passages (0.4%) resolve to a reader page of ≥1,000 passages**, and the count per page is nothing like the page size — `Pācittiyapāḷi` displays 3,048 passages and exactly **one** passage leads there; `Therīapadānapāḷi` 4,042 displayed, 25 leading; `Suttanipātapāḷi` 3,892 displayed, 90 leading.

What those passages are is not a guess — they read `Tassuddānaṃ –` followed by short verse lines listing unit names, i.e. **uddāna**, the mnemonic index. The `Pācittiyapāḷi` one is the Pātimokkha recitation preamble (`Uddiṭṭhaṃ kho, āyasmanto, nidānaṃ…`). Most are untranslated, consistent with `import_brahmali`'s note that SuttaCentral does not translate uddāna because it is an index, not prose. This file already reached the same verdict for the Dīgha uddāna case: tier 1 finding no discourse is **correct**, because the verse belongs to no discourse.

So the present failure mode is **over-showing** — a 41-character index verse rendered with the whole book as its context — not the truncation it resembles. Narrowing the range here would mean inventing a unit for text the editor never put in one.

Root cause of the huge span, for whoever does take this on: the XML import produced **noise wrapper sections** that span an entire document while their `source_path` sits *below* a real subsection and their title repeats an ancestor. `s0505m` has a 0–3891 section titled `Khuddakanikāye` whose path is `[…, 'Suttanipātapāḷi', '1. Uragavaggo', 'Khuddakanikāye']` — a whole-book range claiming to live inside the first vagga. `vin02m1` has the same shape. The uddāna passages' `section_id` points at these wrappers, and `_source_path_is_prefix` correctly refuses to treat them as ancestors of real content — which is also why no outline can be built for them.

**Built, additively:** those pages now get an outline from the document's real top-level sections instead of from the wrapper's (non-existent) descendants — `_section_outline` falls back to `_flat_outline_layer(smaller, require_disjoint=True)`. The passages shown are untouched; only navigation is added. `Suttanipātapāḷi` gets 5 vaggas, `Pācittiyapāḷi` 11 kaṇḍas, `Parivārapāḷi` 18, `Therīapadānapāḷi` 4.

**`require_disjoint` is on for that fallback only, and the asymmetry is measured, not stylistic.** In the fallback the parent/child relation has already broken down, so same-depth entries can overlap or run past the page: `s0510m2` at its shallowest depth yields ranges totalling **10,015 passages inside a 4,042-passage page**. On the prefix-verified descendant path the tree is real, and turning the guard on there cost **19 pages some outline entries and one page its whole outline** (`e1007n/Sūrassatīnīti`, 2 entries → 0) — two sections that overlap there are still two real sections, each with a correct jump target.

Proven additive across all 37,173 sections: **36,702 unchanged, 459 pages gained an outline, 0 pages lost an entry.** 12 pages changed only the *order* of the same entries (identical sets), because ties on `start_sort_order` are now broken by `end_sort_order` instead of by whatever order the DB returned. Compare sets, not lists, if you re-measure this — an unsorted comparison reported 166 false differences.

### Human translations

`human_translations` (`../db/migrations/002_human_translations.sql`) stores translations by a **named translator**, keyed `(passage_id, source)` — as opposed to `translations`, which only models AI output keyed by `(passage_id, language, model, prompt_version)`. `human_translation_imports` logs each run so you can see coverage.

#### Multi-pass importing and match provenance (`004_match_provenance.sql`)

**Never write with a bare `on conflict do update`.** Every importer used to, which meant the *last* run won regardless of how well it matched — running the strict pass and then the global-alignment pass wiped exactly the most reliable rows. `004_match_provenance.sql` adds `match_method` / `match_score` / `import_batch` plus `human_translation_method_rank()`:

```
manual 50 > pdf_heading_boundary 45 > strict_unique 40 > global_align 30 > whole_unit 20 > heuristic 10 > null 0
```

`import_indacanda.write_translation()` is the reference implementation: its upsert ends with `where rank(excluded.match_method) >= rank(human_translations.match_method)`, so a weaker pass can fill a gap but can never overwrite a stronger one. Verified in both directions — strict blocks global, manual upgrades strict, strict cannot then overwrite manual.

That makes importing **cumulative across passes** instead of "whatever the last run produced". The evidence this was already happening informally: the table held 30,331 Indacanda rows while a full re-run reproduced only 18,238 — the rest were survivors of earlier runs under different rules.

Two consequences to respect:
- **`--force` must never mean "delete then rebuild".** In `import_indacanda` it only bypasses the already-imported skip. Deleting first would have cost 12,093 rows that the current rules cannot reproduce.
- **Rows written before this column existed must be backfilled**, otherwise `match_method is null` ranks 0 and any pass overwrites them. Done once for `indacanda` (→ `strict_unique`) and `indacanda_full` (→ `whole_unit`).
- **An *invented* `match_method` string ranks 0 too, and that is the same trap by another door.** A one-off repair script must write `manual` (rank 50) or its rows are unprotected. Live example found 2026-08-17: `fix_indacanda_label_swap.py` wrote 3 `indacanda` rows with `match_method='label_boundary_fix'`, and `human_translation_method_rank('label_boundary_fix')` returns **0** — so even a `heuristic` pass (rank 10) would overwrite three hand-corrected rows silently. Current DB state to be aware of: 12,809 rows still carry `match_method = null` (also rank 0) alongside those 3.

`export_data.py` carries migrations 004 and 006 *and* the provenance columns. Omitting them makes the VPS copy rank-less, so the next import there would flatten it. **Check the generated file, not just the script** — `export_data.sql` dated 2026-08-14 listed 006 in the script but carried only 002/003/004/005, because it had been generated before 006 existed. A stale dump looks identical from the outside.

Two more things the exporter has to do that `pg_dump` would have done for free:
- **`set client_encoding = 'UTF8';` as the first statement.** Without it psql takes the client encoding from the OS locale; on a Windows VPS that is WIN1252/WIN1258 and 124 MB of Vietnamese and Pali diacritics get re-interpreted on the way in — corrupted silently, not rejected.
- **`on conflict do nothing` only dedupes where a unique constraint exists to collide with.** `human_translation_imports` has none beyond `id uuid default gen_random_uuid()`, so every re-run appended another full copy. It now uses `insert … select … where not exists (… is not distinct from …)`, and exports `created_at` as part of the comparison — the table logs one row *per importer run*, so 246 real rows hold only 91 distinct value tuples and dropping `created_at` would collapse them and erase the run history.

Only `import_indacanda` uses this so far. `import_sujato`, `import_brahmali`, `import_minhchau` still upsert unconditionally — harmless while each owns its own `source`, but they should be converted before any of them gains a second pass.

Note: the parent repo's `.gitignore` has a blanket `*.sql` rule for data dumps, which silently swallowed the migration. There is now a `!db/migrations/*.sql` negation — keep it, or new migrations will never reach the VPS.

`import_sujato.py` pulls from SuttaCentral's [bilara-data](https://github.com/suttacentral/bilara-data) (CC0), where the Pali root and Sujato's English share segment keys (`mn10:1.2`) and are therefore already aligned sentence-by-sentence. The hard part is mapping those segments onto **this** DB's `passages` rows.

File locations come from `bilara_tree()`, one call to the repo's git tree API giving an exact `uid -> path` map. Do not go back to guessing paths from a pattern: each nikāya nests differently (`sutta/mn/`, `sutta/sn/sn47/`, `sutta/kn/ud/vagga1/`), AN Books 1-2 are published as ranges (`an1.1-10`), and Itivuttaka's folder is `vagga{n}` where `n` is *not* derivable from the uid — pattern-guessing silently imported 1 of 111 Itivuttaka suttas.

**Do not match segment text against the whole DB.** The Pali canon repeats stock formulas verbatim — MN 10 and DN 22 are nearly identical — so global text matching silently assigns passages to the wrong sutta. An early attempt anchored MN 10 onto a Dīgha Nikāya document with a confident-looking 70% "match" rate. The importer instead does:

1. **Structural anchoring** (`build_targets`). Sutta sections are those whose title starts with a number *and* ends in `sutta(ṃ)`; sub-sections inside a sutta (`Uddeso`, `Kāyānupassanā ānāpānapabbaṃ`) carry no leading number, which is what separates the two. CST restarts numbering per vagga, so **position in document order** is the sutta number, never the printed one. Three shapes are configured in `NIKAYA_CONFIG`:
   - `flat` — dn, mn, iti: sequential across the nikāya's files → `mn10`
   - `grouped` — sn, ud, snp: a group header (`…saṃyuttaṃ` / `…vaggo`) resets the counter → `sn47.1`
   - `per_file` — an: each file is one nipāta, number given in the config → `an3.65`

   Two Khuddaka books break that title rule, so `_is_sutta_title` takes per-nikāya escapes (`allow_unnumbered`, `extra_suffixes`) set in `NIKAYA_CONFIG` — **only** for a nikāya whose full section list has been checked to make sure the loosened rule admits nothing else. `iti` needs `allow_unnumbered` because CST prints `Kalyāṇasīlasuttaṃ` with no leading number; since iti uids run flat, dropping it shifted every later sutta down one slot (DB `iti97` was really SuttaCentral's `iti98`) and the title guard then refused all 16. `snp` needs both, because the whole Pārāyanavagga is titled `…pucchā` / `…gāthā` rather than `…sutta` and was missing outright. Targets are now 112 / 80 / 73 for iti / ud / snp, matching the canon — `expect` in `NIKAYA_CONFIG` pins those counts so a future regression stops the import instead of silently importing less.
2. **Title verification** (`titles_agree`). bilara puts header lines in `<uid>:0.*`; the sutta name sits at `:0.2` in MN but `:0.3` in SN (where `:0.2` is the vagga), so *all* header lines are compared and any match counts. **A sutta whose title disagrees is skipped, not written.** This is the safety net against an off-by-one in the numbering, which would otherwise misassign translations across a whole nikāya without any visible symptom — it is what caught the SN header-position difference, and it correctly refuses the places where CST and SuttaCentral genuinely number suttas differently (AN 3.48 onward, SN 17.21 onward). It also refuses `iti35`/`iti36`, where the two editions simply name the same sutta differently (`Paṭhamajananakuhana` vs CST's `Paṭhamanakuhana`); that is 2 suttas lost out of 112 and is not worth loosening `_stems_agree` for.

   Comparison runs on *stems*: leading numbers and the trailing `sutta(ṃ)` are removed, parenthesised alternate names (`Dhamma (nāvā) sutta`) become separate candidates, and a shared prefix of ≥70% counts as agreement so that `Puttamaṁsa` still matches `Puttamaṁsūpama`. Loosening this is delicate — verify against both directions (`dev_verify` style: cases that must match *and* cases that must be refused) before touching it.
3. **Range-scoped alignment**. Only then are segments scanned inside the sutta's `sort_order` range, with a forward-only cursor so repeated formulas can't drag a match back to the start.

`resolve_pattern` probes bilara's directory layout per nikāya (`sutta/mn/…`, `sutta/sn/sn47/…`, `sutta/kn/ud/vagga1/…`) using uids sampled *across* the whole list — the first few uids of AN don't exist as individual files (Books of Ones and Twos are published as ranges like `an1.1-10`), so sampling only the head concludes the whole nikāya is missing.

```bat
python import_sujato.py mn dn sn an ud iti snp   :: nạp
python import_sujato.py --all                     :: mọi bộ đã cấu hình
python import_sujato.py sn --limit 20 --dry-run --verbose
```

Match rates run ~70-95% of segments. The shortfall is bilara's heading segments plus places where CST elides with `…pe…` while bilara spells the passage out; spot-checking the worst cases (dn16, dn26, mn135) confirmed those are *incomplete*, not misaligned.

The verse books need a **different anchor entirely**, which is what `mode: "by_section"` (`_targets_by_section`) is for. bilara publishes them by verse range (`dhp1-20`) or sutta range (`an1.1-10`), so there is no sutta uid to look up; instead one DB *section* maps to one bilara file, **by reading order**. Order is only trustworthy when both sides have exactly the same number of sections, so a count mismatch abandons the whole collection rather than importing the part that lines up — and `titles_agree` still checks every pair afterwards, so a slip is caught twice. Covered this way: `dhp` (26), `thag` (264), `thig` (73), `cp` (35), `an1` (31); match rates 73-92%.

Still not covered, and each for its own reason: **an2** — CST's Dukanipāta mixes three section shapes (`1. Vajjasuttaṃ`, `(6) 1. Puggalavaggo`, `1. Kodhapeyyālaṃ`) and no counting rule reaches bilara's 19 files; **ja** — Sujato has translated only 82 of 547 Jātakas, so reading order cannot align and it needs name-based matching; **vv, pv, bv, tha-ap, thi-ap** — bilara carries only a placeholder file each, not a translation.

`import_minhchau.py` reuses that same anchoring and title verification, but writes **sutta-level** rows (see `translation_sources.py` above). Its text comes from two places, and picking the wrong one looks like an importer bug when it isn't:

- **SuttaCentral `html_text/vi/pli/sutta/`** for dn, mn, sn, an.
- **budsas.org** (`budsas.py`) for iti, ud, snp — `BUDSAS_NIKAYAS`. SuttaCentral has **no** Vietnamese Itivuttaka or Udāna at all and only 3 Suttanipāta suttas (`kn/snp/chau`); confirmed against the whole sc-data tree *and* the suttaplex API, so `import_minhchau.py iti ud snp` writing 3 rows was the source being empty, not the uid mapping being wrong. budsas.org carries the complete Minh Châu Tiểu Bộ as plain HTML. Anything budsas skips falls back to SuttaCentral — it prints `(Xem kinh Sela, Trung Bộ Kinh)` instead of reprinting snp3.7 and snp3.9.

budsas.org has **no sutta ids**, only a Roman numeral per sutta (`(I) (Ud 1)`, `(I) Kinh Rắn (Sn 1)`), so the only anchor is reading order — and the printed numeral is itself wrong in three places (Ud 2 prints `(VIII)` twice, Ud 4 and Snp 4 both misprint the fourth), so it is used **only** to detect a chapter restart (`(I)`), never as the position. Because order is the anchor, `budsas_by_uid` refuses the whole collection unless every chapter has the same sutta count on both sides — one extra or missing sutta would shift all the rest silently, the same failure mode `titles_agree` exists to catch. The one legitimate shortfall is declared in `BUDSAS_SHORTFALL`: budsas merges the two closing verse sections of the Pārāyanavagga into a single "Kết luận", so snp5 is one short and `snp5.19` stays uncovered.

Lookup is by `passage_id` only. A search result whose snippet was expanded across several passages resolves to the anchor passage's translation; arbitrary text has no passage id and correctly returns "no official translation" rather than guessing.

`translation_sources.sources_with_data()` is deliberately **not** cached, so a newly imported source shows up without restarting the app. What still has to be done by hand after an import is registering the source in `HUMAN_TRANSLATION_FETCHERS` with the reader matching its attachment shape (see the `translation_sources.py` entry in the module map).

### `import_brahmali.py` — the only English Vinaya

Bhikkhu Sujato did not translate the Vinaya and neither did Minh Châu, so before this the Vinaya had **no** English at all. Source is bilara-data, same repo as Sujato, so Pali and English already share segment keys.

The Nikāyas anchor on suttas; the Vinaya has none — bilara splits by training rule (`pli-tv-bu-vb-pj1`) and CST splits by volume (`vin01m` … `vin02m4`). So each bilara file's target volume is **detected**, then `align_globally` places its segments.

**Score the whole file, never a head sample.** An earlier version probed the first 12 segments and got 5 files wrong, two of them silently:
- `pli-tv-bu-vb-pc8` scored **12/12** on `vin01m` — perfectly confident and wrong. Pācittiya 8 shares its origin story with Pārājika 4, which lives in `vin01m`. Whole-file: `vin02m1` 114/160 vs `vin01m` 64/160.
- `pli-tv-bu-pm` landed in `vin02m2` because the Pātimokkha opens with the uposatha recitation formula, which really is in the Mahāvagga. Whole file: `vin02m1` 180, `vin01m` 162, `vin02m2` **9**.

Openings are the most misleading part of the file: origin stories and ceremonial formulas are reused verbatim across volumes. Confidence at the head means nothing.

Don't set an absolute hit floor either — `pli-tv-bi-vb-as1-7` has exactly 2 usable segments. One hit is trusted when no other volume contains it.

One file to one volume, even though `pli-tv-bu-pm` genuinely spans two. Measured the second/best hit ratio across all 421 files: it spreads smoothly 0.1–0.5 with no valley separating "really spans" from "stock-formula bleed", so any threshold would be invented. And there is nothing to gain — the `vin01m` passages the Pātimokkha matches are already covered by each rule's own Vibhaṅga file, with a better `source_ref`.

`pli-tv-bi-vb-pj1-4` is skipped forever and correctly: bilara ships 4 header lines and `~`. Brahmali's own English says the rules "are not found in any manuscript".

Coverage is **74% of the 12,957 Vinaya root passages, and that is near the ceiling.** Of the 3,318 uncovered, 3,097 (94%) do not exist anywhere in bilara's 422 Vinaya files — mostly **uddāna**, the rhymed mnemonic verses listing rule names, which SuttaCentral does not translate because they are an index, not prose. Only 185 are recoverable, so perfect matching would reach 76%. Treat "74%" as *CST-paragraph coverage*, not translation completeness.

### `import_indacanda.py` — PDF source, three things that bite

**Word-splitting from PDF extraction.** `pypdf` inserts a space inside Vietnamese words with stacked diacritics: `thu ộc`, `lo ại`, `xu ống`. This was originally patched with regexes describing the *shape* of the break, which fails by construction — every unanticipated shape needs another rule, and 3,223/30,331 rows were still broken. `mend_spacing()` now asks the only question that generalizes: **does joining the two fragments produce a real word?** A PDF fragment never is one (`ộc`, `ại`); a legitimate pair never joins into one (`do ý` → `doý`). Vocabulary lives in `app/data/vi_words.txt` (5,390 forms, frequency ≥ 5), regenerated by `dev_make_vi_words.py`.

**`mend_spacing()` belongs to Indacanda's PDF pipeline and nowhere else.** `repair_indacanda_spacing.py` therefore keys its repair off a per-source `REPAIRS` map instead of applying one function to the table. Measured on the real data, applying it everywhere would corrupt: 1,508 `sujato` + 315 `brahmali` rows (`So I have heard` → `SoI have heard`, `the suffix -cakka` → `suffix-cakka`, and the punctuation rule eats Pali elision — `hīne ’dhimuttaṁ` → `hīne’dhimuttaṁ`), and 18 of 21 `minh_chau` word-joins including meaning-changing ones (`Nếu Ta y trước` → `Nếu Tay trước`, `Kalandaka Nivapa` → `KalandakaNivapa`). `minh_chau` came from HTML, not a PDF text layer, so it has no split-word defect to fix at all — and it spells Pali names apart in the older convention (`A tu la`, `Sà la`), exactly what the dictionary joiner destroys. It gets `punctuation_spacing_only` instead, verified against all 563 occurrences with no false positive. Adding a source to `REPAIRS` means sampling its real diffs first, not defaulting it to `mend_spacing`.

Keep it a **file**, not a live DB count: a re-import must reproduce the previous result, and counting from the DB changes the answer depending on how much happened to be loaded. It is importer data — review `git diff` on it, a new word there changes behaviour. Threshold 5 was measured (N∈{3,5,10} × K∈{0,1,3}); adding "must be commoner than the right fragment" drops `khuất` and scores 9/10, so it is not used. Result: 20/20 must-join, 14/14 must-keep, and 7,672 defects cleared to zero.

Note the repair had to be applied **in place** to 2,225 stale rows — a re-import only refreshes rows it re-matches, so leftovers from earlier passes keep their old text.

**Whole-unit assembly (`indacanda_full`).** Passage-level covers only 1–58% per volume, so "xem toàn bộ bài kinh" showed fragments. The whole-unit pass reassembles the continuous Vietnamese from the PDF span bracketed by the pairs that *did* match, which recovers text that never aligned.

Three attempts failed before the current one, each in a way worth remembering:
1. Anchoring on `passages.section_id` landed on sub-sections *inside* Mahāpadānasutta — still excerpts. Anchor at **sutta level**, like `minh_chau` does (`1. Mahāpadānasuttaṃ`, 4–207).
2. Extending the span left to the previous unit made Mahānidāna open with Mahāpadāna's tail.
3. Splitting the gap down the middle made Mahāsamaya open with Mahāgovinda's text — because Mahāgovinda had been dropped by a guard, so its pages became ownerless. **Any span extension breaks when a neighbour can be absent.**

So the span is cut strictly inside the anchors: a few lines are lost at each end, in exchange for the guarantee that text under a discourse's name belongs to it. `WHOLE_EDGE_SLACK` (0.15) bounds the loss, and overlapping spans are dropped rather than trimmed.

**Independent PDF-boundary preview.** The anchor assembler above cannot honestly produce “whole” text because it may lose 15% at the edges. `indacanda_full_extract.py` instead maps each DB reader unit to the printed Pāli heading, finds the corresponding Vietnamese heading on the facing page, and slices from that exact line to the next unit's Vietnamese heading. This also handles two short Suttanipāta units sharing one page and retains footnotes/small print. Volume-specific aliases are deliberate: SN has Kapila/Dhammacariya, Dhamma-(Nāvā)/Nāvā and `...sutta`/`...māṇavapucchā`; Pts II has Suññatā/Suñña and Paññā/Mahāpaññā plus a printed VI/VIII typo. Do not move those exceptions into a global fuzzy rule.

The four wrappers are `extract_indacanda_full_dn2.py`, `extract_indacanda_full_dn3.py`, `extract_indacanda_full_pts2.py`, and `extract_indacanda_full_sn.py`. They write only `indacanda_full_preview/<volume>/manifest.json` plus TXT previews and contain no INSERT/UPDATE/DELETE. Measured locally: DN2 10/10 PASS, DN3 11/11 PASS, Pts II 20/20 PASS, SN 71/73 PASS; Piṅgiya and Pārāyanatthutigāthā remain REVIEW because the latter has no independently detected printed heading.

`dn3` needed **no new parsing logic** — it reuses dn2's rules verbatim (`level == 4` + `sutta(ṃ)` suffix; the same `^\d+\.\s+.+\(\d+\)$` running-head strip for `6. Kinh Khơi Dậy Niềm Tin (29)`). Counted before assuming: `s0103m` has exactly 11 level-4 sections, all ending `suttaṃ`, and 137 level-5 sub-sections. That is the cheap case, not the general one — `sn` and `pts2` each still need their own selector and alias table, and the remaining 26 volumes should each be counted the same way rather than assumed to be another dn2.

**What dn3 exposed is bigger than truncation: `whole_unit` rows can carry other discourses' text.** The old anchor-built row for `1. Pāthikasuttaṃ` is 105,764 characters against the PDF-cut 69,956. Probing it at twelve evenly spaced points: 0–33% is really Pāthika, 58% is **Udumbarikasutta**, and 66–91% is **Cakkavattisutta**. Over half of what readers saw under "Pāthikasuttaṃ — whole discourse" was two other suttas. The shrink is the repair, not a loss — every distinctive phrase of Pāthika's closing `Aggaññapaññattikathā` is still present in the new text, checked directly.

That also pins a blind spot in `dev_whole_unit_audit.py`: it scores by length, so an over-inclusive row scores *high*. This row was never in its worst-50 list. The audit finds units that lost text; it cannot find units that gained someone else's. Do not read "50/150 below 80%" as the total count of bad rows.

**The last unit of a volume is the fragile one, and `niṭṭhita` alone does not mark its end.** Every other unit is bounded by the next unit's printed heading; the last has only `find_last_boundary`, which used to stop at the first Pāli page containing `niṭṭhita`. `11. Dasuttarasuttaṃ` prints `Paṭhamabhāṇavāro niṭṭhito.` on page 529 — end of the *first recitation section* — and the real `Dasuttarasuttaṃ Niṭṭhitaṃ Ekādasamaṃ.` only on page 569. It cut at 529 and lost half the discourse, **while passing every gate**, because what it did capture was clean. The tell was arithmetic, not any check: Việt/Pāli came to 0.78 where the other ten suttas of the volume sit between 1.45 and 1.71.

The boundary now prefers a `niṭṭhita` line that also names the unit, falling back to the old first-match rule (`printed_end_marker_unnamed`) so the already-applied volumes are untouched — verified: dn2, sn and pts2 re-extract byte-identical. The name has to match **on the same line**, not merely on the page: a first attempt compared per page and changed nothing, because every Pāli page carries the running head `Dīghanikāye Pāthikavaggo 11. Dasuttarasuttaṃ (34)`, so the internal marker's page "names the unit" too.

**The check that generalises is page tiling, not the Việt/Pāli ratio.** The ratio is what exposed Dasuttara, but it only works where the Pāli edition elides uniformly across the volume. In `dn1` it does not: CST spells the sīla sections out in full in Brahmajāla and elides them with `…pe…` in the later suttas while the translation writes them out every time, so the per-unit ratio spreads 1.31–5.03 and flagging outliers would be almost all false alarms. `dn3` happens to sit at 1.45–1.71, which is why it worked there.

Two structural checks hold in both volumes and are the ones to run on any new volume:

- **Consecutive units must tile the volume** — no gap over ~2 pages between one unit's last Vietnamese page and the next unit's first. Measured: 0 holes in both dn1 and dn3.
- **The last unit must end where the body ends**, just before the back matter. This is the one that catches the Dasuttara failure, which no between-units check can see because the last unit has no successor. Density does not catch it either: truncated Dasuttara ran 2,236 characters per Vietnamese page, right inside the normal 1,600–2,600 band.

`apply_indacanda_full_preview.py` is the separate reviewed-manifest importer. Default invocation is read-only dry-run; `--apply` is mandatory for a transaction. It rechecks PDF/TXT SHA-256, section/range/anchor and spacing/Unicode, imports PASS only, never overwrites `manual`, archives stale anchors for the exact same range, and refuses the whole transaction if a differently ranged `indacanda_full` row overlaps. Method `pdf_heading_boundary` ranks 45 via migration 006.

#### `pr`: printed positional prefixes, four rows of spillover, fixed 2026-08-17

`pr` was first applied as batch `pdf_heading_boundary-20260817-172714-635169` at 44 PASS / 5 REVIEW, and **four of those 44 rows carried a neighbour's text.** Re-extracting reproduced the same bytes, so it was a live extractor defect, not a stale preview. The repair is applied as batch `pdf_heading_boundary-20260817-233403-083470` (11 rows: 5 insert + 6 update, 1 stale anchor archived then removed) — see the verification at the end of this section.

Root cause, one shape hit from several directions: **the printed edition prefixes `PAṬHAMA `/`DUTIYA ` onto a base name shared by two consecutive rules, and twice names a rule differently from CST.** DB says `Saṅghabhedasikkhāpadaṃ` / `Bhedānuvattakasikkhāpadaṃ`; the book prints `PAṬHAMASAṄGHABHEDASIKKHĀPADAṂ` (p494) / `DUTIYA SAṄGHABHEDASIKKHĀPADAṂ` (p502). Fuzzy matching then failed in the worst possible way — it **kept the wrong page and discarded the right one**:

```
unit 10 `Saṅghabhedasikkhāpadaṃ` vs p494 (its own heading) : 0.8627, margin 0.0479 -> REFUSED (ambiguity guard)
unit 10 `Saṅghabhedasikkhāpadaṃ` vs p502 (unit 11's)       : 0.8800, margin 0.0723 -> ACCEPTED
```

The margin decided it, and the margin only differs because `dutiya` is one character shorter than `pathama`. Unit 10 stayed REVIEW so it was never written on its own — but unit **9** took that hit as its `next_hit` boundary and swallowed the whole of unit 10. A row that passed every gate, with clean text, under the wrong name.

The fix is **volume-scoped aliases in `heading_stem`** (six for `pr`, each hand-checked against the printed page) plus one guard: **a fuzzy hit must carry the DB unit's own printed number** (`_numbers_agree`). The number is right there on the page — p502 reads `3. 11.` while the DB unit is `10.` — and it is the evidence a similarity score cannot see. A score threshold was tried first and rejected: dn1/dn3/sn/pts2 hold five *legitimate* fuzzy hits (spelling variants like `Cūḷabyūha`/`Cūḷaviyūha`), so thresholding breaks four applied volumes, whereas all five agree on number and pass. Measured across the six volumes: 153/153 correct hits agree on number; the single disagreement is pts2's already-documented printed `VI`/`VIII` typo, which matches by name exactly and so never reaches the guard.

Result: **49/49 PASS, 0 REVIEW** (five units gained, including two that had no reachable heading at all), and the boundaries now **tile the volume exactly** — 74→140→198→238→302→338→…→688, every unit ending where the next begins. That tiling is the check worth running on any new volume; it is what makes spillover visible.

The four repaired rows:

| unit | before | after |
|---|---|---|
| `9. Dutiyaduṭṭhadosasikkhāpadaṃ` | →502, 28,627 chars (all of unit 10 appended) | →494, 18,351 |
| `8. Upakkhaṭasikkhāpadaṃ` | →588, 7,926 (partial unit 9, cut mid-sentence) | →586, 5,922 |
| `8. Duṭṭhadosasikkhāpadaṃ` | →482, 29,106 | →480, 28,001 |
| `1. Paṭhamakathinasikkhāpadaṃ` | →550, 8,101 | →548, 6,669 |

`10. Pariṇatasikkhāpadaṃ`, the last unit, still carries the chapter's uddāna verses and `DỨT BỘ PĀRĀJIKAPĀḶI`; no successor heading exists to stop it. Cosmetic by comparison — decide separately. It is still the one `vin01m` row the audit flags (1.28x), and that is now expected rather than unexplained.

**Verified after the apply** (all read-only): `pdf_heading_boundary` 169 → **174**, `whole_unit` 132 → **131** (the Catuttha row upgraded), total 305; **0 duplicate ranges, 0 null/inverted ranges**; all 49 `pr` rows byte-match their preview TXT; the two rows that had swallowed a neighbour no longer contain that neighbour's text at all. The audit's suspect list lost `9. Dutiyaduṭṭhadosasikkhāpadaṃ` (1.72x) and `8. Upakkhaṭasikkhāpadaṃ` (1.30x), and `4. Catutthapārājikaṃ` left the worst-20 list (it was 83% as `whole_unit`). The control group's median tightened 115% → 113% — removing spillover makes the yardstick itself more honest.

**Two lessons that generalise.** First, `dev_whole_unit_audit.py` flagged only two of the four (`1. Paṭhamakathina` and `8. Duṭṭhadosa` sat inside their volume's normal length band), so its "THỪA" list is a floor, not a census. Second, the `Paṭhama-`/bare vs `Dutiya-` naming pair repeats throughout Nissaggiya/Pācittiya — assume `thag`, `ap1`, `kn1` and the rest carry it, and check number agreement and boundary tiling before trusting PASS.

**State as of 2026-08-17 (verified against the DB, not carried over from an earlier note):** six volumes are applied — the whole Dīgha Nikāya `dn1` (13) + `dn2` (10) + `dn3` (11) = **34 suttas**, plus `sn` (71 of 73), `pts2` (20) and `pr` (**49 of 49**) — for **174 `pdf_heading_boundary` rows**, and a dry-run over all six reports `insert 0 | update 0 | unchanged 174 | blocked 0`. 24 of the 30 configured volumes remain. An older version of this file claimed "89 inserts, 12 upgrades" were still pending, and a later one said `dn3` was "not yet applied"; both had been true once and were stale, and it cost a session's work to rediscover. **Re-run the dry-run before believing any number written here.**

Two traps this area has already sprung, both worth keeping in mind:

- **A stale preview TXT fails the gate even when the DB is fine.** `repair_indacanda_spacing.py` rewrote `translated_text` in place but left `source_ref` holding the pre-repair SHA-256, so three rows (`pts2/5. Virāgakathā`, `pts2/2. Iddhikathā`, `sn/7. Nandamāṇavapucchā`) had a provenance stamp that disagreed with their own content. Re-running the extractors regenerated the TXT with the current `mend_spacing`, which both cleared the gate and surfaced the mismatch; applying it changed the text by zero bytes and only corrected `source_ref`. If a repair pass ever touches `translated_text` again, update `source_ref` in the same statement.
- The gate refusing an item is usually the **vocabulary file having moved on**, not the data being wrong. `mend_spacing` reads `app/data/vi_words.txt`; regenerate the previews before suspecting the DB.

Text completeness is a separate gate from boundary correctness. Every Vietnamese body page is cross-checked with pdfplumber words and font sizes. Words at ≤85% of the page's median font size are audited separately, so footnotes/small canonical text cannot disappear while the main prose still makes the unit look plausible. A pypdf miss may use `pdfplumber_fallback` only when pdfplumber also covers every pypdf token; disagreement, image-heavy pages without text, or unmapped Unicode makes the containing unit REVIEW. Current full scan: DN2 282 pages/116 with small text, Pts II 134/2, SN 176/48; minimum extracted font 6.48pt, total and small-text word coverage 100%, 0 fallback, 0 text-page REVIEW, 0 image pages, 0 Unicode hazards.

`_is_whole_unit_title` is **local to this file** — do not reuse `import_sujato._is_sutta_title`, which demands a `sutta(ṃ)` ending and returned 0 units for 24 of 30 volumes, precisely the verse books whose passage-level alignment is best. Suffixes were counted from real DB titles, not guessed: `jatakam` (418), `apadanam` (423+181), `gatha` (170+73), `vatthu` (86+51), `sikkhapadam` (45+213), plus `cariya`, `vamso`, `parajikam`. Rejected: `vaggo`, `nipato`, `kandam`, `khandhakam`, `bhanavaro`, `katha`, `vibhango` — all *chapters*; admitting one turns a "whole discourse" into a whole chapter, which is worse than missing.

**`--global-align` exists but has never been run.** It is the "previous verse → 1250, next → 1252, so this one is 1251" reasoning; the strict default instead demands a unique match that beats the runner-up by `SIMILARITY_MARGIN`. Narrowing the search to the detected section was tried and **rejected**: removing rivals from view manufactures false confidence — hand-checking 8 new rows gave 6 right, 1 off by a paragraph, 1 outright wrong.

### Remaining work, with the pass/fail branch for each

Ordered by expected value. Each is independent thanks to the provenance ranking — a failed pass writes nothing that outranks what is already there.

1. **Do not run `--all --force --global-align` as a production batch.** It remains experimental and unmeasured; earlier context-based/fill-between attempts produced plausible but wrong neighbouring paragraphs. Test it only on one named volume and one reversible batch after adding a representative audit set. `pts2` explicitly rejects this flag because its dedicated parser already enforces page and book order.
2. **Jātaka whole-unit is 0 — root cause now confirmed, and it is not what this file used to say.** The old entry claimed `s0514m` has *no* jātaka-level sections at all and guessed at `WHOLE_MIN_ANCHORS` / `WHOLE_EDGE_SLACK`; it was marked unverified, and it was **wrong**. The sections exist. CST prints the unit's position inside its nipāta as a trailing bracketed ordinal — `543. Bhūridattajātakaṃ (6)` — and both title tests compare the **end** of the normalised string, so `(6)` masks the real `jatakam` suffix and every Jātaka is rejected. Measured: `_is_whole_unit_title` accepts 0 of 56 sections in `s0514m` and 150 of 581 in `s0513m`; stripping a trailing `(\d+)` takes those to **27 and 254**.

   The same flaw sat in `main._is_reader_unit_title`, where it made the reader page open a fragment instead of the discourse: 4,885 of the passages that have a translation (2,717 in `s0514m`, 2,168 in `s0513m`). **Fixed there** via `_READER_TRAILING_ORDINAL`, moving whole-discourse coverage from 79.2% to 86.0% of translated passages. Stripping the ordinal only admits *more* titles and cannot smuggle chapters in — `22. Mahānipāto (3)` still ends in `nipato` and is still refused, which the regression test pins in both directions.

   `import_indacanda._is_whole_unit_title` is **not** fixed yet, because changing it means re-running `ja1 ja2 ja3` and writing DB rows. Do that as its own pass: apply the same strip, re-run those three volumes, and only then judge the edge guard — the previous "relax `WHOLE_EDGE_SLACK`" plan was aimed at a cause that turned out not to be the real one.
3. **`pts2` parser fixed, pending the operator's real import.** Root cause was confirmed: body pages use bare page numbers, not numbered paragraphs, so `_VERSE` correctly found 0 and must not be loosened. The dedicated `paired_indented` parser limits itself to PDF pages 36–303, pairs Pāli/Việt only when both pages have the same number of indented paragraphs, requires ≥85% query coverage plus a unique trigram winner, then keeps only a non-decreasing `sort_order` chain. Measured dry-run: 122/134 balanced page pairs, 659 paired paragraphs, 277 quality candidates, 268 ordered print paragraphs grouped into **258 DB passages**; 391 ambiguous pairs and 12 unbalanced page pairs are retained, not guessed. It deliberately writes no `indacanda_full`: incomplete coverage must never be labeled “whole discourse”.
4. **`mv1` detects sections on 0/597 pages** while comparable volumes reach 400–780. Anomalous enough to have its own cause.
5. **Low-rate volumes** (`pc2` 8%, `cv1` 8%, `bvcp` 10%). **Do not loosen similarity here.** Find out first whether CST and the printed edition divide the text differently; if they do, loosening produces a flattering number and wrong data — the same trap `pc8` sprang on the Sujato importer.
6. **Unresolved store implemented** in migration 005. Numbered pairs that do not pass matching are stored in `human_translation_unresolved`; `pts2` additionally stores whole unbalanced page pairs with reason `unbalanced_indented_pdf_pages`, so future passes can target only the retained cases.

A useful target shape for this corpus: ~60% certain + ~20% inferred from context + ~20% deliberately left empty. Do not chase 100%.

### Data model (see `001_init.sql`, owned upstream by `../db/migrations/`)

`documents` (one per source XML file, `corpus_type` ∈ mul/att/tik/nrf) → `sections` (hierarchical, `source_path text[]`) → `passages` (the actual searchable Pali text, `normalized_pali` for FTS/trgm, `hierarchy jsonb`, `embedding` for optional vector search). `translations` caches AI translations per `passage_id`; `text_translations` caches by content hash for text that isn't a single passage row. `search_logs` records every search query/filters/result set for the admin analytics view.

### Admin area

Session-cookie auth only (`SessionMiddleware` + a boolean `admin_logged_in` flag), credentials from `ADMIN_USERNAME`/`ADMIN_PASSWORD` env vars compared with `secrets.compare_digest`. No rate limiting or lockout.

- `/admin/history` — `search_logs` with **infinite scroll**, not numbered pages: the page renders the first `ADMIN_HISTORY_BATCH = 20` rows, and the browser asks `/api/admin/history/rows` for the next batch (`ADMIN_HISTORY_MAX_BATCH = 100` per call) using a keyset cursor (`before_time` + `before_id`), so there is no upper bound on how far back you can scroll — verified pulling all 387 logged rows. Plus a keyword filter (`?q=`), an `?only_empty=true` filter for searches that returned nothing, a total count, and a top-10 most-frequent-query list.
- `/api/admin/history/{id}` resolves the logged `result_passage_ids` back to full passage rows; `/api/admin/history/clear` truncates `search_logs`.
- `/admin/notice` — GET renders the notice editor (title + body per language, plus an on/off toggle), POST saves it via `notice.save_notice` and bumps the version when the content actually changed.

## Client requests (`feat_new/`) — status

The requests live in `feat_new/Yêu cầu.docx` (feature list) and `feat_new/toiuu_timkiem.docx` (search-quality bug report + a proposed fallback strategy), both Vietnamese. Screenshots embedded in the docx pin down the exact failing queries.

### Feature requests (`Yêu cầu.docx`)

1. **Startup notice banner — DONE.** `notice.py` + `app/data/notice.json` + the `#noticeModal` block in `index.html`, editable at `/admin/notice`. Shown once per notice version, tracked in `localStorage` under `tipitaka.noticeSeen`.
2. **English + Myanmar UI with a language switcher — DONE; all three named translators now return real data.** `i18n.py` carries the full vi/en/my catalog, the switcher is in the hero, and the chosen language becomes the Gemini translation target. All four translation options are always listed (as the client asked), and the search results card prints every source that covers the passage, one after another.

**The PDFs under `feat_new/` turned out not to be the right source.** Machine-readable, already-aligned versions of all three translations exist publicly, which avoids OCR and manual alignment entirely:
   - Sujato (English) — [bilara-data](https://github.com/suttacentral/bilara-data), CC0, aligned per sentence. **Imported** (31,229 rows), passage-level.
   - Thích Minh Châu (Vietnamese) — on SuttaCentral as legacy texts (`suttacentral.net/mn10/vi/minh_chau`), aligned per sutta, not per sentence. **Imported** (3,073 rows), sutta-level: one row per discourse plus its `sort_order` range. DN/MN/SN/AN come from SuttaCentral; Itivuttaka, Udāna and Suttanipāta come from budsas.org because SuttaCentral has no Vietnamese for them (see "Human translations"), and bring those three books to 97-98% Vietnamese coverage by passage.
   - Indacanda (Vietnamese) — [tamtangpaliviet.net](https://www.tamtangpaliviet.net/), published as a Pāli-Vietnamese bilingual edition; the client never supplied it. **Imported** by `import_indacanda.py`, all 30 configured volumes: **30,590 passage-level rows** (Vinaya, Khuddaka, Dīgha) plus 251 whole-discourse rows as `indacanda_full`. Per-volume match rates run 8–93%. Note the two are on very different footings: the passage-level import covers 29 documents and is done, whereas the *high-quality* whole-discourse extractor (`indacanda_full_extract`) supports only 4 of the 30 volumes. "3 of 30" statements refer to that extractor, never to the corpus.
   - Brahmali (English, Vinaya) — bilara-data, **not in the client's list** but the only English Vinaya in existence for this corpus. **Imported** by `import_brahmali.py` (9,639 rows, 74% of the Vinaya root canon).
3. **"Search all" option — DONE.** Both selectors gained an `all` entry and default to it, so a visitor can type and search without choosing anything. `resolve_corpus_types` / `resolve_pitaka_type` normalize it. Note this also required fixing `_display_source`, which labelled every row with `corpus_types[0]` and so tagged Ṭīkā results as "Tipiṭaka Mūla" once more than one corpus was in scope.
4. **Raise the admin search-history cap — DONE.** 50/200 → default 200, max 5000, plus pagination, a page-size picker, a keyword filter and a zero-result filter.

### Search-quality bugs (`toiuu_timkiem.docx`)

**Vấn đề 1 — pasting several Pali lines returned 0 results — FIXED.** Root cause confirmed against the DB: gāthā verses are stored **one line per `passages` row** (e.g. `sort_order` 2310 `gatha1` and 2311 `gathalast` hold the two halves of Theragāthā 676), while `_tsquery_for_phrases` built a single distance-1 `<->` chain over every token of the paste. No single row could ever satisfy it. Fixed by segmenting the query into sentences, building one chain per sentence, windowing long chains, adding the AND-of-tokens quote branch, and giving verbatim matches an explicit ranking bonus.

**Vấn đề 2 — zero-result keywords — FIXED for the reported cases.** The dominant cause was not the missing bilingual corpora but a gap in the local analyzer: when the glossary matched no concept and Gemini returned nothing usable, retrieval had **no search terms at all**. `Sona` returned 0 results even though 434 passages contain `soṇa*`. Three defects fixed:
   - `analyze_query` now emits `queryTerms` / `querySegmentTerms` from the query itself as a last-resort term source;
   - an over-narrow `mustHavePali` from Gemini no longer zeroes out the term branch (it retries with the broader keyword set);
   - Pali compounds are matched by shared head (`_compound_stems`) — Gemini answers `alagaddūpama`, but the sutta text reads `alagaddatthiko` / `alagaddagavesī`, so whole-word prefix matching missed the very passage being asked for.

**The client's proposed fallback** was two ideas; only one was buildable now:
   - *Progressive query shortening* (`"có người đi tìm rắn…"` → `"tìm rắn"` → `"rắn"`) — **DONE** in `fallback_search.py`, triggered only when page 1 comes back empty, with the substituted keyword surfaced to the reader.
   - *Routing through bilingual Pali-facing human translations, then tracing the facing Pali back into the Tipiṭaka* — **DONE for aligned passage rows and verified Indacanda bilingual sections.** The round trip the client described (find in translation → read off the facing Pali → search the Pali again in the Tipiṭaka) is redundant: `search_engine._retrieve_translation_candidates_for_docs` queries `human_translations.translated_text` and joins the hit straight back to `passages`. A long non-Pāli paste is checked before Gemini and, when matched, skips both AI expansion and AI reranking; short conceptual queries keep the existing semantic route and mix in direct translation hits. Passage rows return the exact Pāli passage. `indacanda_full` range rows are also searchable because their heading boundaries were verified against facing Pāli/Việt pages; they return the first passage of the verified section and never pretend to infer an exact sentence alignment inside it. Other whole-discourse sources remain excluded. Unresolved PDF pairs remain unsearchable until they can be aligned safely.

### New source material under `feat_new/` (not yet ingested)

Everything below is PDF (some zipped one-PDF-per-file); none of it is structured/machine-readable yet, so turning it into searchable/aligned DB rows needs its own extraction pipeline — out of scope until the client prioritizes it.

- `Bandich_ngaiMinhChau/` — Thích Minh Châu's Vietnamese translation: Trung Bộ, Tăng Chi Bộ, Tương Ưng Bộ Kinh.
- `bandichTiengAnh_Ngai_Sujato/` — Bhikkhu Sujato's English translation: Trường Bộ ("Long Discourses"), Trung Bộ ("Middle Discourses"), Tương Ưng ("Linked Discourses"), Tăng Chi ("Numbered Discourses"), plus several Tiểu Bộ (Khuddaka Nikāya) collections (Dhammapada, Udāna, Itivuttaka, Suttanipāta, Theragāthā, Therīgāthā) as zipped PDFs.
- `3. CHÚ GIẢI TẠNG VI DIỆU PHÁP.../` — Abhidhamma commentary (Aṭṭhakathā) for Dhammasaṅgaṇī, Vibhaṅga, Dhātukathā, Kathāvatthu, Paṭṭhāna, from several named translators (TK. Sán Nhiên, TK. Siêu Thành, TK. Thiện Minh, Tâm An, TK. Minh Huệ, HT. Tịnh Sự).
- `vidieuphap_bandich_NgaiTinhSu/` — Ven. Tịnh Sự's Vietnamese Abhidhamma Piṭaka translation, all 7 books.
