"""
Entry point. Runs every registered scraper source, then hands the raw
output to the ETL pipeline.

Usage:
    python main.py                # run everything
    python main.py --etl-only     # skip scraping, just re-run ETL on existing raw/*.json
    python main.py --no-combine   # skip writing the merged all-in-one JSON file
    python main.py --load-db      # after ETL, also load into Postgres (needs DATABASE_URL in .env)
"""
import argparse
import json
from pathlib import Path

from config import RAW_OUTPUT_DIR
from scraper.sources.universite_centrale import UniversiteCentraleSource
from scraper.sources.generic_university_source import GenericUniversitySource
from etl.pipeline import run_pipeline
from etl.load_db import load_universities as load_processed_universities, load_into_postgres

SEED_PATH = Path(__file__).resolve().parent / "data" / "universities_seed.json"

# Where the single merged JSON (every university, one file, clubs/events/
# location/sub_institutions all included) gets written.
COMBINED_OUTPUT_PATH = RAW_OUTPUT_DIR.parent / "universities_combined.json"


def load_sources() -> list:
    """
    Universite Centrale gets the dedicated, hand-tuned scraper (better
    clubs/events coverage). Every other entry in the seed file gets the
    generic heuristic scraper. Add more universities by adding entries to
    data/universities_seed.json -- no code change needed unless a site
    turns out to need its own dedicated scraper (low completeness in the
    coverage report is the signal for that).
    """
    sources = [UniversiteCentraleSource()]

    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    for entry in seed:
        if not entry.get("website"):
            # A handful of seed entries have a null website -- either never
            # filled in, or (as happened for 4 entries) nulled out after
            # being caught pointing at the wrong institution's site. Skip
            # rather than crash; these need a manually-verified URL added
            # to data/universities_seed.json before they can be scraped.
            print(f"[main] skipping '{entry.get('name')}': no website in seed data")
            continue
        if entry["website"].rstrip("/") == UniversiteCentraleSource.HOME_URL.rstrip("/"):
            continue  # already covered by the dedicated scraper above
        sources.append(
            GenericUniversitySource(
                name_hint=entry["name"],
                type_hint=entry.get("type"),
                website=entry["website"],
            )
        )
    return sources


def run_scrapers() -> None:
    for scraper in load_sources():
        try:
            scraper.run()
        except Exception as exc:
            print(f"[{scraper.source_name}] failed: {exc}")


def combine_raw_outputs(out_path: Path) -> list[dict]:
    """
    Merge every per-source file in output/raw/*.json (each one written by
    BaseScraper._save_raw) into a single list, and write it to out_path.

    Per-source files stay put -- they're still useful for debugging one
    source in isolation -- this just adds one extra, complete file that has
    every university (public + private), each with whatever
    clubs/events/location/sub_institutions data its scraper could collect.
    """
    combined: list[dict] = []
    seen_names = set()

    for json_file in sorted(RAW_OUTPUT_DIR.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[combine] skipping {json_file.name}: invalid JSON ({exc})")
            continue

        records = data if isinstance(data, list) else [data]
        for record in records:
            name_key = (record.get("name") or "").strip().lower()
            if name_key and name_key in seen_names:
                print(f"[combine] skipping duplicate '{record.get('name')}' from {json_file.name}")
                continue
            seen_names.add(name_key)
            combined.append(record)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")

    n_with_clubs = sum(1 for u in combined if u.get("clubs"))
    n_with_events = sum(1 for u in combined if u.get("events"))
    n_with_location = sum(1 for u in combined if u.get("location"))
    n_with_subs = sum(1 for u in combined if u.get("sub_institutions"))
    total_subs = sum(len(u.get("sub_institutions") or []) for u in combined)

    print(f"\n[combine] wrote {len(combined)} universities to {out_path}")
    print(f"  {n_with_location} have a location, {n_with_clubs} have clubs, {n_with_events} have events")
    print(f"  {n_with_subs} universities have sub-institutions listed ({total_subs} sub-institutions total)")

    return combined


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--etl-only", action="store_true", help="skip scraping, just process existing raw output")
    parser.add_argument("--no-combine", action="store_true", help="skip writing the merged all-in-one JSON file")
    parser.add_argument("--load-db", action="store_true", help="after ETL, load output/processed/universities.json into Postgres (needs DATABASE_URL)")
    args = parser.parse_args()

    if not args.etl_only:
        run_scrapers()

    if not args.no_combine:
        combine_raw_outputs(COMBINED_OUTPUT_PATH)

    summary = run_pipeline()
    print(f"\nDone: {summary['validated_count']}/{summary['total_count']} universities passed validation")

    if args.load_db:
        universities = load_processed_universities()
        count = load_into_postgres(universities)
        print(f"[load-db] upserted {count} universities into Postgres")


if __name__ == "__main__":
    main()