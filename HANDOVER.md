# Specter saved-search → Supabase weekly sync

## ⚠ SCOPE OF THIS HANDOVER — READ FIRST

**This is a smoke test only. Pull exactly 5 records per search, no more. Ever.**

- **5 records per search, full stop.** Hardcoded as `LIMIT_PER_SEARCH = 5` in `smoke_test.py`. Do not increase it. Do not parameterise it. Do not add a `--full` flag.
- **Do not paginate.** A single `/results?new=true&limit=5` call per search. No `page=2`. No "just to be safe" follow-up calls.
- **No backfills.** Even if a search has 1,156 records flagged new, we pull 5.
- **No "full sync" of any kind.** This deliverable is the schema + a 20-credit smoke test. Nothing else.
- **Total credit budget for the entire run: 20 credits** (4 searches × 5 records). If your run consumes more than that, stop and investigate.
- **The cron / weekly sync is a future deliverable.** Do not build it as part of this task. Do not write `--full`, `--backfill`, or any "production mode" flag. The handover stops at: schema applied, 20 records inserted, row counts printed.

The records inserted by this smoke test are throwaway. Tom will delete them once the proper sync is wired up later, identified by `sp_searches.last_status = 'smoke_test'`.

---

## What you're building

A one-off smoke test that:

1. Applies the `sp_*` schema (5 tables) via `migrations.sql`.
2. Calls the Specter API for **5 records each** from 4 saved searches (one per syncable product type).
3. Upserts those 20 records into the new tables, and the underlying person profiles into the existing `specter_people` table.
4. Prints row counts as proof the data landed.

That's it. There is no cron, no weekly run, no backfill. Future sync work will be specified separately.

Four product types are in scope: **company**, **people**, **talent**, **stratintel**. A fifth type, `investors`, exists in the saved-search list but has no `/results` endpoint and is excluded.

Place the smoke-test code under `services/specter_sync/` in the existing Transition tooling repo (same repo as the Affinity sync). Use the same Python toolchain and lint config.

## Credentials

Both secrets are stored in the same secret store as the Affinity sync credentials.

```
SPECTER_API_KEY            — Specter API key, used as `X-API-Key` header
SUPABASE_URL               — https://ezdokbmhdnuyslqhtyrm.supabase.co
SUPABASE_SERVICE_ROLE_KEY  — service-role key for the Supabase project (same as Affinity sync)
```

Specter base URL: `https://app.tryspecter.com/api/v1`.

## How the Specter API works (the parts that matter)

There are two endpoints per saved search. Read both rows carefully before writing the client:

| Endpoint | Returns | Cost |
|---|---|---|
| `GET /searches/{path}/{searchId}` | Counts only — `full_count`, `new_count`, etc. No records. | Free |
| `GET /searches/{path}/{searchId}/results` | Full enriched records, paginated. | **1 credit per record** |

There is no separate "list of IDs" endpoint and no per-record `details` endpoint at the search layer. The cron only needs `/results` plus, optionally, the cheap `/searches` index call to discover what exists.

`?new=true` filters to records first seen since the last delivery. Confirmed empirically that calling `/results?new=true` does **not** deplete the set — calling it twice in a row returns the same records. So the reset is on Specter's schedule, not ours. Treat upserts as idempotent and don't worry about repeated returns.

### Path-segment mapping (the `product_type` from `/searches` does not equal the URL path)

| `product_type` from `GET /searches` | URL path segment for `/results` |
|---|---|
| `company` | `companies` |
| `people` | `people` |
| `talent` | `talent` |
| `stratintel` | **`investor-interest`** |
| `investors` | no endpoint — skip |

### Pagination

`/results` defaults to `limit=50`, supports `?limit=` (max 200) and `?page=` (zero-indexed). Use `limit=200` and paginate until a page returns fewer than 200 records.

### Headers

```
X-API-Key: {SPECTER_API_KEY}
Accept: application/json
```

### Retry / backoff

Specter occasionally returns 502 / 503 / 504. Retry up to 3 times with exponential backoff (5s, 10s, 20s). 4xx — fail fast and log.

## Run sequence

```
1. Apply migrations.sql via Supabase SQL editor (uses the service-role connection)
2. Set env vars: SPECTER_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
3. Run smoke_test.py
   - For each of the 4 test searches:
     - GET /searches/{path}/{search_id}/results?new=true&limit=5
     - Upsert 5 records into the appropriate sp_* table
     - For people / talent: also upsert 5 person profiles into specter_people
   - Print row counts per table
4. Stop. Do not run again. Do not loop.
```

The 4 test searches (one per syncable product type):

| product_type | URL path | search_id | name |
|---|---|---|---|
| company | `companies` | 6340 | KB focus sectors |
| people | `people` | 30480 | Analysts ecosystem |
| talent | `talent` | 35539 | Claude talent |
| stratintel | `investor-interest` | 4729 | Climate (Revised) |

These four IDs are hardcoded in `smoke_test.py`. Do not change them.

## Schema

Run the SQL in `migrations.sql` (separate file) once before first cron run. It creates:

- `sp_searches` — registry, one row per Specter saved search
- `sp_companies` — company-search results, signal-grain by `(search_id, specter_id)`
- `sp_stratintel` — investor-interest signals, by `(search_id, signal_id)`
- `sp_talent_signals` — talent signals, by `(search_id, talent_signal_id)`, with FK to `specter_people.person_id`
- `sp_people_search_hits` — people-search appearances, by `(search_id, person_id)`, with FK to `specter_people.person_id`

People-type and talent-type results both write the underlying person profile to the existing `specter_people` table (one row per person, dedup across searches), and only the search-specific fields go into `sp_people_search_hits` / `sp_talent_signals`. This matches the existing Supabase pattern where `specter_people` is the canonical person store.

## Mapping records → rows

For each of the four record shapes, write a pure mapping function. Sample records are in the `samples/` folder of this handover (`sample_company.json`, `sample_people.json`, `sample_talent.json`, `sample_stratintel.json`) — use them as fixtures for unit tests.

### Company

Top-level scalars to promote: `id` → `specter_id`, `organization_name`, `web.domain` → `domain`, `hq.country`, `hq.region`, `founded_year`, `growth_stage`, `operating_status`, `employee_count`, `funding.total_funding_usd`, `funding.last_funding.date`, `funding.last_funding.type`, `last_updated`. The whole record goes in `raw` jsonb.

### People

Person profile → upsert into `specter_people` (use existing column list, populate from the API record's matching fields). Then upsert into `sp_people_search_hits` with `(search_id, person_id, first_seen_at, last_synced_at, raw)`.

### Talent

Person profile → upsert into `specter_people`. Then upsert into `sp_talent_signals` with the signal-specific fields: `talent_signal_id`, `person_id`, `signal_date`, `signal_score`, `signal_type`, `signal_status`, `signal_summary`, `new_position_*`, `past_position_*`, `out_of_stealth_advantage`, `announcement_delay_months`, `raw`.

Important: per `lib-specter`, `signal_score` is unreliable. Store it for completeness but never use it for ranking.

### Stratintel

The record has a nested `company` block (when entity is a company) or `person` block (when entity is a person). Promote `signal_id`, `signal_date`, `signal_score`, `signal_type`, `signal_summary`, `entity_id`, derive `entity_kind` from which nested block is present, plus `signal_total_funding_usd`, `signal_last_funding_usd`, `signal_last_funding_date`, `signal_investors` (flatten to text array of names), `signal_source`, `source_types`. Whole record in `raw`.

## Upsert semantics

Every upsert uses `ON CONFLICT (...) DO UPDATE SET` with one critical rule: **never overwrite `first_seen_at` on conflict.** That's the value Specter doesn't preserve and the only reason this sync provides any new analytical value. `last_synced_at` and `raw` always update; promoted scalars also update so we get the freshest version of any changing field.

## Suggested file layout

```
services/specter_sync/
  README.md
  smoke_test.py        # the one-off test script (provided)
  migrations.sql       # the schema (provided)
  samples/             # canonical fixture records (provided)
    sample_company.json
    sample_people.json
    sample_talent.json
    sample_stratintel.json
```

No `src/` package, no test harness, no CLI library. This is a single script.

## Out of scope (deliberately)

- **Anything beyond 5 records per search.** No pagination, no backfills, no `--full` flag.
- **Any cron, scheduler, or recurring job.** This is a one-off script.
- The `investors` product type — no `/results` endpoint exists.
- Specter Lists API (the company / people *list* endpoints, distinct from saved searches).
- Enrichment endpoints (`POST /companies`, `POST /people`).
- Backfill of old data from prior CSV uploads.
- Frontend or any consumer logic. Data lands in Supabase only.
