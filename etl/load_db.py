"""
Loads output/processed/universities.json (the app-schema JSON that
etl/pipeline.py -> etl/export.to_app_schema already produces) into
Postgres, per schema.sql.

Run standalone:
    python -m etl.load_db

Or via main.py:
    python main.py --load-db          # scrape + ETL + load
    python main.py --etl-only --load-db   # re-load existing processed JSON without re-scraping

Idempotent by design: re-running against the same DB updates existing
universities (matched by name) in place and replaces their
programs/clubs/events/tags, rather than accumulating duplicates. This
matters because the scraper is expected to be re-run periodically as
source sites change.
"""
import json
from pathlib import Path

import psycopg2
import psycopg2.extras

from config import PROCESSED_OUTPUT_DIR, DATABASE_URL

PROCESSED_PATH = Path(PROCESSED_OUTPUT_DIR) / "universities.json"


def load_universities(path: Path = PROCESSED_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _upsert_university(cur, uni: dict) -> str:
    """Insert or update the university row by its unique name; return its id."""
    cur.execute(
        """
        INSERT INTO universities (name, type, description, logo_url, cover_image_url, website, email, phone, aliases)
        VALUES (%(name)s, %(type)s, %(description)s, %(logoUrl)s, %(coverImageUrl)s, %(website)s, %(email)s, %(phone)s, %(aliases)s)
        ON CONFLICT (name) DO UPDATE SET
            type            = EXCLUDED.type,
            description     = EXCLUDED.description,
            logo_url        = EXCLUDED.logo_url,
            cover_image_url = EXCLUDED.cover_image_url,
            website         = EXCLUDED.website,
            email           = EXCLUDED.email,
            phone           = EXCLUDED.phone,
            aliases         = EXCLUDED.aliases
            -- updated_at is bumped automatically by trg_universities_updated_at
        RETURNING id
        """,
        {**uni, "aliases": uni.get("aliases") or []},
    )
    return cur.fetchone()[0]


def _upsert_location(cur, university_id: str, location: dict | None) -> None:
    if not location:
        # No location data for this run -- leave any existing row alone
        # rather than deleting real prior data because of a transient
        # scrape gap on this particular run.
        return
    cur.execute(
        """
        INSERT INTO locations (university_id, address, city, latitude, longitude, geocode_precision)
        VALUES (%(university_id)s, %(address)s, %(city)s, %(latitude)s, %(longitude)s, %(geocodePrecision)s)
        ON CONFLICT (university_id) DO UPDATE SET
            address           = EXCLUDED.address,
            city              = EXCLUDED.city,
            latitude          = EXCLUDED.latitude,
            longitude         = EXCLUDED.longitude,
            geocode_precision = EXCLUDED.geocode_precision
        """,
        {**location, "university_id": university_id},
    )


def _replace_children(cur, table: str, university_id: str, rows: list[dict], columns: list[str]) -> None:
    """
    Delete existing child rows for this university and bulk-insert the
    current set. Simpler and safer than trying to diff/upsert individual
    programs/clubs/events, which have no natural unique key of their own
    (two programs can legitimately share a name across different
    universities, or even be re-titled between scrapes) -- and cheap,
    since these tables are small per-university.
    """
    cur.execute(f"DELETE FROM {table} WHERE university_id = %s", (university_id,))
    if not rows:
        return
    col_list = ", ".join(columns)
    values = [tuple(row.get(col) for col in columns) + (university_id,) for row in rows]
    psycopg2.extras.execute_values(
        cur,
        f"INSERT INTO {table} ({col_list}, university_id) VALUES %s",
        values,
    )


def _upsert_tags(cur, university_id: str, tag_names: list[str]) -> None:
    cur.execute("DELETE FROM university_tags WHERE university_id = %s", (university_id,))
    if not tag_names:
        return
    tag_ids = []
    for name in tag_names:
        cur.execute(
            """
            INSERT INTO tags (name) VALUES (%s)
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (name,),
        )
        tag_ids.append(cur.fetchone()[0])

    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO university_tags (university_id, tag_id) VALUES %s",
        [(university_id, tag_id) for tag_id in tag_ids],
    )


def load_into_postgres(universities: list[dict], database_url: str = DATABASE_URL) -> int:
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to .env, e.g. "
            "DATABASE_URL=postgresql://user:password@localhost:5432/tn_universities"
        )

    conn = psycopg2.connect(database_url)
    loaded = 0
    try:
        with conn:
            with conn.cursor() as cur:
                for uni in universities:
                    # camelCase keys from export.to_app_schema map straight
                    # onto the %(name)s-style placeholders used above.
                    university_id = _upsert_university(cur, uni)
                    _upsert_location(cur, university_id, uni.get("location"))
                    _replace_children(
                        cur, "programs", university_id, uni.get("programs", []),
                        columns=["name", "degree_type", "duration_years", "description"],
                    )
                    _replace_children(
                        cur, "clubs", university_id, uni.get("clubs", []),
                        columns=["name", "description", "logo_url", "contact_info"],
                    )
                    _replace_children(
                        cur, "events", university_id, uni.get("events", []),
                        columns=["title", "description", "start_date", "end_date"],
                    )
                    _upsert_tags(cur, university_id, uni.get("tags", []))
                    loaded += 1
        # `with conn:` above commits on clean exit / rolls back on exception
    finally:
        conn.close()

    return loaded


def main() -> None:
    universities = load_universities()
    print(f"loaded {len(universities)} universities from {PROCESSED_PATH}")
    count = load_into_postgres(universities)
    print(f"upserted {count} universities into Postgres")


if __name__ == "__main__":
    main()