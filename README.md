# TN Universities Scraper

Collects raw data on Tunisian universities and turns it into a clean,
validated dataset ready to import into the PostgreSQL `University` schema.

## What this does — and doesn't — do

The scraper (`scraper/`) only fills in fields it can read off a page:
name, description, contact info, address/city, tags, programs, clubs, events.

It never sets `id`, `createdAt`, `updatedAt`, or `latitude`/`longitude`.
IDs and timestamps are the database's job at insert time (`gen_random_uuid()`,
`DEFAULT now()`). Coordinates are filled in later by the ETL layer's
geocoding step, from the scraped address — not scraped directly.

## Project layout

```
tn-universities-scraper/
├── config.py                    # env-driven settings
├── main.py                      # entry point: run scrapers, then ETL
├── scraper/
│   ├── models.py                 # RawUniversity etc. — the scraper's contract
│   ├── base_scraper.py           # shared run()/save logic every source uses
│   └── sources/
│       ├── example_static_source.py   # template: requests + BeautifulSoup
│       └── example_js_source.py       # template: Playwright, for JS-rendered sites
├── etl/
│   ├── clean.py                  # whitespace/phone/email normalization
│   ├── dedupe.py                 # merges the same university seen across sources
│   ├── geocode.py                # address -> lat/lng via Nominatim or Google
│   ├── validate.py               # final schema check + missing-field report
│   └── pipeline.py               # ties clean -> dedupe -> geocode -> validate together
└── output/
    ├── raw/                       # one JSON file per source, untouched scrape output
    └── processed/                 # universities.json + coverage_report.json
```

## Adding a new university source

1. Copy `scraper/sources/example_static_source.py` (or the `_js_` variant if
   the site needs JS rendering) and rename it.
2. Set `LISTING_URL` and swap in the real CSS selectors for that site.
3. Register the new class in `SOURCES` in `main.py`.

Each source scraper only needs to worry about its own site — cleaning,
deduping against other sources, and geocoding all happen centrally in `etl/`.

## Running it

```bash
pip install -r requirements.txt
playwright install chromium      # only needed once, for JS-rendered sources
cp .env.example .env

python main.py                   # scrape every registered source, then run ETL
python main.py --etl-only        # re-run ETL on existing output/raw/*.json without re-scraping
```

Output:
- `output/raw/<source_name>.json` — one file per source, raw scrape output
- `output/processed/universities.json` — cleaned, deduped, geocoded, validated
- `output/processed/coverage_report.json` — missing-field list per university,
  the basis for the "% completeness" section of the deliverable doc

## Next step

`output/processed/universities.json` is what a separate import script (or a
one-off SQL loader) reads from to `INSERT` into Postgres — that's where
`id` and `createdAt`/`updatedAt` get generated, and where `location`,
`programs`, `clubs`, `events`, and `tags` get written into their own
tables per the foreign-key relationships.
