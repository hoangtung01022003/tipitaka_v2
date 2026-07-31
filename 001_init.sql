create extension if not exists vector;
create extension if not exists pg_trgm;
create extension if not exists pgcrypto;

create table if not exists documents (
  id uuid primary key default gen_random_uuid(),
  file_name text not null unique,
  xml_path text not null,
  corpus_type text not null check (corpus_type in ('mul', 'att', 'tik', 'nrf')),
  title text,
  tree_path text[] not null default '{}',
  source_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists passages (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references documents(id) on delete cascade,
  passage_key text not null unique,
  sort_order integer not null default 0,
  paragraph_no text,
  pali_text text not null,
  normalized_pali text not null,
  hierarchy jsonb not null default '{}',
  page_markers jsonb not null default '[]',
  embedding vector(768),
  text_hash text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists sections (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references documents(id) on delete cascade,
  section_key text not null unique,
  parent_section_key text,
  title text not null,
  normalized_title text not null,
  level integer not null default 1,
  rend text,
  start_sort_order integer not null default 0,
  end_sort_order integer,
  source_path text[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table passages add column if not exists section_id uuid references sections(id);
alter table passages add column if not exists xml_paragraph_no text;
alter table passages add column if not exists display_paragraph_no text;
alter table passages add column if not exists source_locator jsonb not null default '{}';

create table if not exists translations (
  id uuid primary key default gen_random_uuid(),
  passage_id uuid not null references passages(id) on delete cascade,
  language text not null default 'vi',
  model text not null,
  prompt_version text not null,
  translated_text text not null,
  notes text,
  created_at timestamptz not null default now(),
  unique (passage_id, language, model, prompt_version)
);

create table if not exists text_translations (
  id uuid primary key default gen_random_uuid(),
  text_hash text not null,
  language text not null default 'vi',
  model text not null,
  prompt_version text not null,
  source_text text not null,
  translated_text text not null,
  notes text,
  created_at timestamptz not null default now(),
  unique (text_hash, language, model, prompt_version)
);

create table if not exists search_logs (
  id uuid primary key default gen_random_uuid(),
  query text not null,
  filters jsonb not null default '{}',
  expanded_query jsonb not null default '{}',
  result_passage_ids uuid[] not null default '{}',
  created_at timestamptz not null default now()
);

create index if not exists documents_corpus_type_idx on documents (corpus_type);
create index if not exists sections_document_id_idx on sections (document_id);
create index if not exists sections_document_sort_idx on sections (document_id, start_sort_order, end_sort_order);
create index if not exists sections_title_idx on sections (normalized_title);
create index if not exists passages_document_id_idx on passages (document_id);
create index if not exists passages_document_sort_idx on passages (document_id, sort_order);
create index if not exists passages_section_id_idx on passages (section_id);
create index if not exists passages_section_sort_idx on passages (section_id, sort_order);
create index if not exists passages_text_hash_idx on passages (text_hash);
create index if not exists text_translations_text_hash_idx on text_translations (text_hash);
create index if not exists passages_normalized_pali_trgm_idx on passages using gin (normalized_pali gin_trgm_ops);
create index if not exists passages_normalized_pali_fts_idx on passages using gin (to_tsvector('simple', normalized_pali));
create index if not exists passages_embedding_idx on passages using hnsw (embedding vector_cosine_ops);
