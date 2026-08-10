# CLAUDE.md

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
```

`dev_verify.py` is the quality gate, and each part answers a different question:
- **A** re-downloads the bilara source and checks every stored `segment_ids` entry really is a substring of the passage it was written to — independent of the importer's own logic, so an importer bug cannot mark itself correct.
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
- `translation_sources.py` — the "which translation" selector. All four sources now return real data: `ai` (Gemini), `sujato`, `indacanda` (Kinh Tập only so far) and `minh_chau`. `source_options()` marks availability from `sources_with_data()`, a plain (deliberately **un**cached) `select distinct source from human_translations`, so a source appears as soon as its importer finishes — no restart needed.

  **Two attachment shapes, and picking the wrong reader silently returns nothing.** `sujato`/`indacanda` are *passage-level*: one row per `passage_id`, the `document_id`/`start_sort_order`/`end_sort_order` columns left null — read with `_fetch_from_human_translations`. `minh_chau` is *sutta-level*: the translator divides the text differently, so one row covers a whole discourse plus the `sort_order` range it spans. Its `passage_id` is only an anchor, **not** the sole passage covered — reading it by `passage_id` makes every other passage in the discourse miss, which is why it needs `_fetch_whole_sutta_translation` (a range lookup). Register each source under the right reader in `HUMAN_TRANSLATION_FETCHERS`; a source that is imported but not registered there stays invisible behind "Hiện không có bản dịch chính thức nào" even though the rows exist.

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

### Human translations

`human_translations` (`../db/migrations/002_human_translations.sql`) stores translations by a **named translator**, keyed `(passage_id, source)` — as opposed to `translations`, which only models AI output keyed by `(passage_id, language, model, prompt_version)`. `human_translation_imports` logs each run so you can see coverage.

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
   - Sujato (English) — [bilara-data](https://github.com/suttacentral/bilara-data), CC0, aligned per sentence. **Imported** (24,723 rows), passage-level.
   - Thích Minh Châu (Vietnamese) — on SuttaCentral as legacy texts (`suttacentral.net/mn10/vi/minh_chau`), aligned per sutta, not per sentence. **Imported** (3,073 rows), sutta-level: one row per discourse plus its `sort_order` range. DN/MN/SN/AN come from SuttaCentral; Itivuttaka, Udāna and Suttanipāta come from budsas.org because SuttaCentral has no Vietnamese for them (see "Human translations"), and bring those three books to 97-98% Vietnamese coverage by passage.
   - Indacanda (Vietnamese) — [tamtangpaliviet.net](https://www.tamtangpaliviet.net/), published as a Pāli-Vietnamese bilingual edition; the client never supplied it. **Imported** by `import_indacanda.py`, passage-level, but so far **only Kinh Tập** (1,073 of 1,170 verse pairs matched, covering 1,073 of Suttanipāta's 3,892 passages). `VOLUMES` in that script already configures `kn1`, `thag`, `dn1`, `dn2`, `dn3` — those runs have not been done, so outside Kinh Tập Indacanda legitimately reports no translation.
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
   - *Routing through bilingual Pali-facing human translations, then tracing the facing Pali back into the Tipiṭaka* — **partially unblocked.** The round trip the client described (find in translation → read off the facing Pali → search the Pali again in the Tipiṭaka) is redundant: once `human_translations` rows are joined to `passages`, finding the translation *is* finding the passage. With Sujato imported, an English-language search route can be built as a direct query over `human_translations.translated_text` joined back to `passages` — no second lookup. Still to do: the importer for the two Vietnamese editions, and the search branch itself.

### New source material under `feat_new/` (not yet ingested)

Everything below is PDF (some zipped one-PDF-per-file); none of it is structured/machine-readable yet, so turning it into searchable/aligned DB rows needs its own extraction pipeline — out of scope until the client prioritizes it.

- `Bandich_ngaiMinhChau/` — Thích Minh Châu's Vietnamese translation: Trung Bộ, Tăng Chi Bộ, Tương Ưng Bộ Kinh.
- `bandichTiengAnh_Ngai_Sujato/` — Bhikkhu Sujato's English translation: Trường Bộ ("Long Discourses"), Trung Bộ ("Middle Discourses"), Tương Ưng ("Linked Discourses"), Tăng Chi ("Numbered Discourses"), plus several Tiểu Bộ (Khuddaka Nikāya) collections (Dhammapada, Udāna, Itivuttaka, Suttanipāta, Theragāthā, Therīgāthā) as zipped PDFs.
- `3. CHÚ GIẢI TẠNG VI DIỆU PHÁP.../` — Abhidhamma commentary (Aṭṭhakathā) for Dhammasaṅgaṇī, Vibhaṅga, Dhātukathā, Kathāvatthu, Paṭṭhāna, from several named translators (TK. Sán Nhiên, TK. Siêu Thành, TK. Thiện Minh, Tâm An, TK. Minh Huệ, HT. Tịnh Sự).
- `vidieuphap_bandich_NgaiTinhSu/` — Ven. Tịnh Sự's Vietnamese Abhidhamma Piṭaka translation, all 7 books.
