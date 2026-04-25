#!/usr/bin/env python3
"""
Specter saved-search → Supabase SMOKE TEST.

╔════════════════════════════════════════════════════════════════╗
║  HARD LIMITS — DO NOT MODIFY                                   ║
║                                                                ║
║   • 5 records per search. No more. Ever.                       ║
║   • 4 searches total. ~20 credits per run.                     ║
║   • No pagination. No --full flag. No backfill.                ║
║   • Run once. Do not loop. Do not schedule.                    ║
║                                                                ║
║  This is a smoke test, not a sync. The cron / weekly sync      ║
║  is a future deliverable specified separately. If the script   ║
║  consumes more than ~20 credits, stop and investigate.         ║
╚════════════════════════════════════════════════════════════════╝

Run once to:
1. Pull 5 records per type from 4 saved searches (~20 credits total).
2. Upsert into sp_searches, sp_companies, sp_stratintel,
   sp_people_search_hits, sp_talent_signals (and specter_people).
3. Print a summary of what landed.

Throwaway test rows. Tom will delete via:
  DELETE FROM sp_companies          WHERE search_id IN (6340, 30480, 35539, 4729);
  DELETE FROM sp_stratintel         WHERE search_id IN (6340, 30480, 35539, 4729);
  DELETE FROM sp_talent_signals     WHERE search_id IN (6340, 30480, 35539, 4729);
  DELETE FROM sp_people_search_hits WHERE search_id IN (6340, 30480, 35539, 4729);
  DELETE FROM sp_searches           WHERE last_status = 'smoke_test';
once the proper sync is wired up.

Pre-req: migrations.sql must already be applied (run via Supabase SQL editor).

Requires:
  SPECTER_API_KEY            (from same secret store as Affinity sync)
  SUPABASE_URL               (https://ezdokbmhdnuyslqhtyrm.supabase.co)
  SUPABASE_SERVICE_ROLE_KEY  (same secret store as Affinity sync)
"""

import os
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

# ---------------------------------------------------------------- config

SPECTER_API_KEY = os.environ["SPECTER_API_KEY"]
SUPABASE_URL    = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY    = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

SPECTER_BASE = "https://app.tryspecter.com/api/v1"

SEARCHES_TO_TEST = [
    # (search_id, name, product_type, url_path)
    (6340,  "KB focus sectors",   "company",    "companies"),
    (30480, "Analysts ecosystem", "people",     "people"),
    (35539, "Claude talent",      "talent",     "talent"),
    (4729,  "Climate (Revised)",  "stratintel", "investor-interest"),
]

# HARD LIMIT — DO NOT INCREASE.
# This is a smoke test, not a sync. If you raise this above 5 you are
# building a different deliverable than the one specified.
LIMIT_PER_SEARCH = 5
assert LIMIT_PER_SEARCH == 5, "LIMIT_PER_SEARCH must remain 5 — this is a smoke test, not a sync"
assert len(SEARCHES_TO_TEST) == 4, "Smoke test covers exactly 4 searches"
MAX_TOTAL_CREDITS = LIMIT_PER_SEARCH * len(SEARCHES_TO_TEST)  # = 20

# ---------------------------------------------------------------- http

def specter_get(path):
    """GET against Specter with one retry on 5xx."""
    url = f"{SPECTER_BASE}{path}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"X-API-Key": SPECTER_API_KEY})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise

def supabase_request(method, path, payload=None, prefer=None):
    """REST call against Supabase. Returns parsed JSON or None."""
    url = f"{SUPABASE_URL}/rest/v1{path}"
    headers = {
        "apikey":         SUPABASE_KEY,
        "Authorization":  f"Bearer {SUPABASE_KEY}",
        "Content-Type":   "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        return json.loads(body) if body else None

def upsert(table, rows, on_conflict):
    """Bulk upsert via PostgREST."""
    if not rows:
        return
    path = f"/{table}?on_conflict={on_conflict}"
    supabase_request(
        "POST", path, payload=rows,
        prefer="resolution=merge-duplicates,return=minimal",
    )

# ---------------------------------------------------------------- helpers

def safe_get(d, *keys, default=None):
    cur = d
    for k in keys:
        if cur is None or not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default

def parse_date(v):
    """Pass through dates / ISO strings; return None on falsy."""
    if not v:
        return None
    if isinstance(v, str):
        return v[:10] if len(v) >= 10 else None
    return v

# ---------------------------------------------------------------- mappers

def map_company(search_id, rec):
    web_domain = safe_get(rec, "web", "domain") or safe_get(rec, "website")
    return {
        "search_id":            search_id,
        "specter_id":           rec.get("id"),
        "organization_name":    rec.get("organization_name"),
        "domain":               web_domain,
        "hq_country":           safe_get(rec, "hq", "country"),
        "hq_region":            safe_get(rec, "hq", "region"),
        "founded_year":         rec.get("founded_year"),
        "growth_stage":         rec.get("growth_stage"),
        "operating_status":     rec.get("operating_status"),
        "employee_count":       rec.get("employee_count"),
        "total_funding_usd":    safe_get(rec, "funding", "total_funding_usd"),
        "last_funding_date":    parse_date(safe_get(rec, "funding", "last_funding", "date")),
        "last_funding_type":    safe_get(rec, "funding", "last_funding", "type"),
        "last_updated_specter": parse_date(rec.get("last_updated")),
        "raw":                  rec,
    }

def map_stratintel(search_id, rec):
    company = rec.get("company") or {}
    investors = rec.get("signal_investors") or []
    return {
        "search_id":              search_id,
        "signal_id":              rec.get("signal_id"),
        "signal_date":            rec.get("signal_date"),
        "signal_score":           rec.get("signal_score"),
        "signal_type":            rec.get("signal_type"),
        "signal_summary":         rec.get("signal_summary"),
        "source_types":           rec.get("source_types"),
        "signal_source":          rec.get("signal_source"),
        "entity_id":              rec.get("entity_id"),
        "entity_kind":            "company" if company else ("person" if rec.get("person") else None),
        "company_name":           company.get("organization_name") or company.get("name"),
        "company_domain":         safe_get(company, "web", "domain") or company.get("domain"),
        "signal_total_funding_usd": rec.get("signal_total_funding_usd"),
        "signal_last_funding_usd":  rec.get("signal_last_funding_usd"),
        "signal_last_funding_date": parse_date(rec.get("signal_last_funding_date")),
        "signal_investors":       [i.get("name") if isinstance(i, dict) else str(i) for i in investors],
        "raw":                    rec,
    }

def map_specter_person(rec):
    """Map a people/talent record into the existing specter_people row shape."""
    return {
        "person_id":                       rec.get("person_id"),
        "talent_signal_ids":               rec.get("talent_signal_ids"),
        "strategic_signal_ids":            rec.get("strategic_signal_ids")
                                          or rec.get("investor_signal_ids"),
        "profile_picture":                 rec.get("profile_picture")
                                          or rec.get("profile_picture_url"),
        "first_name":                      rec.get("first_name"),
        "last_name":                       rec.get("last_name"),
        "full_name":                       rec.get("full_name"),
        "linkedin_url":                    rec.get("linkedin_url"),
        "twitter_url":                     rec.get("twitter_url"),
        "github_url":                      rec.get("github_url"),
        "about":                           rec.get("about"),
        "tagline":                         rec.get("tagline"),
        "location":                        rec.get("location"),
        "region":                          rec.get("region"),
        "people_highlights":               rec.get("highlights") or rec.get("people_highlights"),
        "level_of_seniority":              rec.get("level_of_seniority"),
        "years_of_experience":             rec.get("years_of_experience"),
        "education_level":                 rec.get("education_level"),
        "experience":                      rec.get("experience"),
        "current_position_title":          rec.get("current_position_title"),
        "current_position_company_name":   rec.get("current_position_company_name"),
        "current_position_company_website":rec.get("current_position_company_website"),
        "past_position_title":             rec.get("past_position_title"),
        "past_position_company_name":      rec.get("past_position_company_name"),
        "past_position_company_website":   rec.get("past_position_company_website"),
        "current_tenure":                  rec.get("current_tenure"),
        "average_tenure":                  rec.get("average_tenure"),
        "education":                       rec.get("education"),
        "languages":                       rec.get("languages"),
        "skills":                          rec.get("skills"),
        "linkedin_followers":              rec.get("linkedin_followers"),
        "linkedin_connections":            rec.get("linkedin_connections"),
    }

def map_people_hit(search_id, rec):
    return {
        "search_id": search_id,
        "person_id": rec.get("person_id"),
        "raw":       rec,
    }

def map_talent_signal(search_id, rec):
    return {
        "search_id":                    search_id,
        "talent_signal_id":             rec.get("talent_signal_id"),
        "person_id":                    rec.get("person_id"),
        "signal_date":                  parse_date(rec.get("signal_date")),
        "signal_score":                 rec.get("signal_score"),
        "signal_type":                  rec.get("signal_type"),
        "signal_status":                rec.get("signal_status"),
        "signal_summary":               rec.get("signal_summary"),
        "new_position_title":           rec.get("new_position_title"),
        "new_position_company_id":      rec.get("new_position_company_id"),
        "new_position_company_name":    rec.get("new_position_company_name"),
        "new_position_company_website": rec.get("new_position_company_website"),
        "past_position_title":          rec.get("past_position_title"),
        "past_position_company_id":     rec.get("past_position_company_id"),
        "past_position_company_name":   rec.get("past_position_company_name"),
        "past_position_company_website":rec.get("past_position_company_website"),
        "out_of_stealth_advantage":     rec.get("out_of_stealth_advantage"),
        "announcement_delay_months":    rec.get("announcement_delay_months"),
        "raw":                          rec,
    }

# ---------------------------------------------------------------- run

def main():
    print("=" * 64)
    print(f"  SMOKE TEST — {LIMIT_PER_SEARCH} records × {len(SEARCHES_TO_TEST)} searches = "
          f"~{MAX_TOTAL_CREDITS} credits maximum")
    print("  Not a sync. Run once. Do not loop.")
    print("=" * 64)

    # 0. Schema
    here = Path(__file__).parent
    sql_path = here / "migrations.sql"
    print(f"\n=== Step 0: applying schema from {sql_path} ===")
    sql = sql_path.read_text()
    # Apply via direct PG connection — PostgREST can't run DDL.
    # Use psycopg2 if available; else print and exit so user can run via Supabase SQL editor.
    try:
        import psycopg2
        # Pull host/db/user/password from SUPABASE_URL and SUPABASE_DB_PASSWORD if you've set it.
        # Easiest: have the operator run migrations.sql via the Supabase SQL editor before this script.
        # For now, assume schema is already applied — fall through.
        print("  (skipping DDL apply — please run migrations.sql via Supabase SQL editor first)")
    except ImportError:
        print("  psycopg2 not installed — please run migrations.sql via Supabase SQL editor first.")

    # 1. Pull saved-search index, register the 4 test searches
    print("\n=== Step 1: registering test searches ===")
    sp_searches_rows = []
    all_searches = specter_get("/searches")
    by_id = {s["id"]: s for s in all_searches}
    for sid, name, ptype, _path in SEARCHES_TO_TEST:
        s = by_id.get(sid, {})
        sp_searches_rows.append({
            "search_id":    sid,
            "name":         s.get("name") or name,
            "product_type": s.get("product_type") or ptype,
            "is_global":    s.get("is_global"),
            "query_id":     s.get("query_id"),
            "full_count":   s.get("full_count"),
            "new_count":    s.get("new_count"),
            "sync_enabled": True,
            "last_status":  "smoke_test",
        })
        print(f"  {sid:<6} {ptype:<10} full={s.get('full_count'):<6} new={s.get('new_count'):<5} | {s.get('name')}")
    upsert("sp_searches", sp_searches_rows, on_conflict="search_id")

    # 2. Pull 5 records from each, write them in
    for sid, name, ptype, path in SEARCHES_TO_TEST:
        print(f"\n=== Step 2.{sid}: pulling {LIMIT_PER_SEARCH} from '{name}' ({ptype}) ===")
        endpoint = f"/searches/{path}/{sid}/results?new=true&limit={LIMIT_PER_SEARCH}"
        records = specter_get(endpoint)
        print(f"  Returned: {len(records)} records")

        # Hard guard — never process more than the limit, even if API returns extra.
        if len(records) > LIMIT_PER_SEARCH:
            raise RuntimeError(
                f"Specter returned {len(records)} records for search {sid} but LIMIT_PER_SEARCH={LIMIT_PER_SEARCH}. "
                "Aborting to prevent over-fetch. Smoke test only."
            )

        if ptype == "company":
            rows = [map_company(sid, r) for r in records]
            upsert("sp_companies", rows, on_conflict="search_id,specter_id")

        elif ptype == "stratintel":
            rows = [map_stratintel(sid, r) for r in records]
            upsert("sp_stratintel", rows, on_conflict="search_id,signal_id")

        elif ptype == "people":
            people_rows = [map_specter_person(r) for r in records]
            upsert("specter_people", people_rows, on_conflict="person_id")
            hit_rows = [map_people_hit(sid, r) for r in records]
            upsert("sp_people_search_hits", hit_rows, on_conflict="search_id,person_id")

        elif ptype == "talent":
            people_rows = [map_specter_person(r) for r in records]
            upsert("specter_people", people_rows, on_conflict="person_id")
            signal_rows = [map_talent_signal(sid, r) for r in records]
            upsert("sp_talent_signals", signal_rows, on_conflict="search_id,talent_signal_id")

        # Update sp_searches with run metadata
        upsert("sp_searches", [{
            "search_id":         sid,
            "name":              name,
            "product_type":      ptype,
            "last_synced_at":    "now()",
            "last_record_count": len(records),
            "last_status":       "ok",
        }], on_conflict="search_id")

    # 3. Summary
    print("\n=== Step 3: summary ===")
    for table in ["sp_searches", "sp_companies", "sp_stratintel",
                  "sp_people_search_hits", "sp_talent_signals"]:
        result = supabase_request(
            "GET", f"/{table}?select=count",
            prefer="count=exact",
        )
        # PostgREST returns count in Content-Range header normally, but with select=count
        # it returns [{"count": N}].
        count = result[0]["count"] if result else "?"
        print(f"  {table}: {count} rows")

    print("\nDone.")

if __name__ == "__main__":
    main()
