-- ============================================================
-- Specter saved-search → Supabase sync — schema
-- Apply once before first cron run.
-- ============================================================

-- ------------------------------------------------------------
-- sp_searches  — registry of all Specter saved searches
-- One row per saved search returned by GET /searches.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sp_searches (
  search_id          INTEGER PRIMARY KEY,
  name               TEXT NOT NULL,
  product_type       TEXT NOT NULL CHECK (product_type IN
                       ('company','people','talent','stratintel','investors')),
  url_path_segment   TEXT GENERATED ALWAYS AS (
                       CASE product_type
                         WHEN 'company'    THEN 'companies'
                         WHEN 'people'     THEN 'people'
                         WHEN 'talent'     THEN 'talent'
                         WHEN 'stratintel' THEN 'investor-interest'
                         ELSE NULL
                       END
                     ) STORED,
  is_global          BOOLEAN,
  query_id           INTEGER,
  full_count         INTEGER,
  new_count          INTEGER,
  sync_enabled       BOOLEAN NOT NULL DEFAULT TRUE,
  is_syncable        BOOLEAN GENERATED ALWAYS AS (
                       product_type IN ('company','people','talent','stratintel')
                     ) STORED,
  last_synced_at     TIMESTAMPTZ,
  last_record_count  INTEGER,
  last_status        TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  public.sp_searches IS
  'Registry of Specter saved searches. Refreshed each cron run from GET /searches.';
COMMENT ON COLUMN public.sp_searches.sync_enabled IS
  'Set false to skip /results fetch for this search. Counts and metadata are still refreshed.';
COMMENT ON COLUMN public.sp_searches.is_syncable IS
  'False for product_type=investors (no /results endpoint exists).';

-- ------------------------------------------------------------
-- sp_companies  — company saved-search results
-- Path: /searches/companies/{search_id}/results
-- One row per (search_id, specter_company_id).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sp_companies (
  search_id            INTEGER NOT NULL REFERENCES public.sp_searches(search_id) ON DELETE CASCADE,
  specter_id           TEXT    NOT NULL,
  first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- promoted scalars
  organization_name    TEXT,
  domain               TEXT,
  hq_country           TEXT,
  hq_region            TEXT,
  founded_year         INTEGER,
  growth_stage         TEXT,
  operating_status     TEXT,
  employee_count       INTEGER,
  total_funding_usd    BIGINT,
  last_funding_date    DATE,
  last_funding_type    TEXT,
  last_updated_specter DATE,
  -- full payload
  raw                  JSONB NOT NULL,
  PRIMARY KEY (search_id, specter_id)
);

CREATE INDEX IF NOT EXISTS sp_companies_specter_id_idx   ON public.sp_companies (specter_id);
CREATE INDEX IF NOT EXISTS sp_companies_domain_idx       ON public.sp_companies (domain);
CREATE INDEX IF NOT EXISTS sp_companies_first_seen_idx   ON public.sp_companies (first_seen_at DESC);
CREATE INDEX IF NOT EXISTS sp_companies_raw_gin          ON public.sp_companies USING GIN (raw);

-- ------------------------------------------------------------
-- sp_stratintel  — investor-interest (a.k.a. stratintel) signals
-- Path: /searches/investor-interest/{search_id}/results
-- One row per (search_id, signal_id).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sp_stratintel (
  search_id            INTEGER NOT NULL REFERENCES public.sp_searches(search_id) ON DELETE CASCADE,
  signal_id            TEXT    NOT NULL,
  first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- promoted scalars
  signal_date          TIMESTAMPTZ,
  signal_score         INTEGER,
  signal_type          TEXT,
  signal_summary       TEXT,
  source_types         TEXT[],
  signal_source        TEXT[],
  entity_id            TEXT,
  entity_kind          TEXT CHECK (entity_kind IN ('company','person')),
  company_name         TEXT,
  company_domain       TEXT,
  signal_total_funding_usd  BIGINT,
  signal_last_funding_usd   BIGINT,
  signal_last_funding_date  DATE,
  signal_investors     TEXT[],
  -- full payload
  raw                  JSONB NOT NULL,
  PRIMARY KEY (search_id, signal_id)
);

CREATE INDEX IF NOT EXISTS sp_stratintel_entity_id_idx   ON public.sp_stratintel (entity_id);
CREATE INDEX IF NOT EXISTS sp_stratintel_signal_date_idx ON public.sp_stratintel (signal_date DESC);
CREATE INDEX IF NOT EXISTS sp_stratintel_first_seen_idx  ON public.sp_stratintel (first_seen_at DESC);
CREATE INDEX IF NOT EXISTS sp_stratintel_raw_gin         ON public.sp_stratintel USING GIN (raw);

-- ------------------------------------------------------------
-- sp_talent_signals  — talent saved-search results, signal-grain.
-- Path: /searches/talent/{search_id}/results
-- Person profile is stored separately in the existing
-- public.specter_people table; this table holds only the
-- signal-specific fields, with FK to specter_people.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sp_talent_signals (
  search_id            INTEGER NOT NULL REFERENCES public.sp_searches(search_id) ON DELETE CASCADE,
  talent_signal_id     TEXT    NOT NULL,
  person_id            TEXT    NOT NULL REFERENCES public.specter_people(person_id) ON DELETE CASCADE,
  first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- signal scalars
  signal_date                  DATE,
  signal_score                 INTEGER,
  signal_type                  TEXT,
  signal_status                TEXT,
  signal_summary               TEXT,
  -- new position
  new_position_title           TEXT,
  new_position_company_id      TEXT,
  new_position_company_name    TEXT,
  new_position_company_website TEXT,
  -- past position
  past_position_title          TEXT,
  past_position_company_id     TEXT,
  past_position_company_name   TEXT,
  past_position_company_website TEXT,
  -- stealth-related
  out_of_stealth_advantage     TEXT,
  announcement_delay_months    INTEGER,
  -- full payload
  raw                          JSONB NOT NULL,
  PRIMARY KEY (search_id, talent_signal_id)
);

CREATE INDEX IF NOT EXISTS sp_talent_signals_person_id_idx
  ON public.sp_talent_signals (person_id);
CREATE INDEX IF NOT EXISTS sp_talent_signals_signal_date_idx
  ON public.sp_talent_signals (signal_date DESC);
CREATE INDEX IF NOT EXISTS sp_talent_signals_new_company_idx
  ON public.sp_talent_signals (new_position_company_website);
CREATE INDEX IF NOT EXISTS sp_talent_signals_first_seen_idx
  ON public.sp_talent_signals (first_seen_at DESC);
CREATE INDEX IF NOT EXISTS sp_talent_signals_raw_gin
  ON public.sp_talent_signals USING GIN (raw);

-- ------------------------------------------------------------
-- sp_people_search_hits  — people saved-search appearances.
-- Path: /searches/people/{search_id}/results
-- Person profile lives in specter_people. This table records
-- which saved searches a person matches and when.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sp_people_search_hits (
  search_id            INTEGER NOT NULL REFERENCES public.sp_searches(search_id) ON DELETE CASCADE,
  person_id            TEXT    NOT NULL REFERENCES public.specter_people(person_id) ON DELETE CASCADE,
  first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw                  JSONB NOT NULL,
  PRIMARY KEY (search_id, person_id)
);

CREATE INDEX IF NOT EXISTS sp_people_search_hits_person_id_idx
  ON public.sp_people_search_hits (person_id);
CREATE INDEX IF NOT EXISTS sp_people_search_hits_first_seen_idx
  ON public.sp_people_search_hits (first_seen_at DESC);

-- ------------------------------------------------------------
-- updated_at trigger for sp_searches
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.sp_searches_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS sp_searches_updated_at ON public.sp_searches;
CREATE TRIGGER sp_searches_updated_at
  BEFORE UPDATE ON public.sp_searches
  FOR EACH ROW EXECUTE FUNCTION public.sp_searches_set_updated_at();
