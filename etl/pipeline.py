"""Orchestrates clean -> dedupe -> geocode -> validate over every raw source file."""
import json
from pathlib import Path

from config import RAW_OUTPUT_DIR, PROCESSED_OUTPUT_DIR
from etl.clean import clean_university
from etl.dedupe import dedupe_universities
from etl.geocode import geocode_university
from etl.validate import validate_university
from etl.export import to_app_schema


def load_raw_universities() -> list[dict]:
    universities = []
    for path in Path(RAW_OUTPUT_DIR).glob("*.json"):
        with open(path, encoding="utf-8") as f:
            universities.extend(json.load(f))
    return universities


def run_pipeline() -> dict:
    raw = load_raw_universities()
    print(f"loaded {len(raw)} raw university records from {RAW_OUTPUT_DIR}")

    cleaned = [clean_university(u) for u in raw]
    deduped = dedupe_universities(cleaned)
    print(f"deduped down to {len(deduped)} unique universities")

    geocoded = [geocode_university(u) for u in deduped]

    validated, coverage_report = [], []
    for uni in geocoded:
        model, missing = validate_university(uni)
        if model:
            validated.append(model.model_dump())
        coverage_report.append({"name": uni.get("name"), "missing_fields": missing})

    app_schema = [to_app_schema(u) for u in validated]

    out_path = PROCESSED_OUTPUT_DIR / "universities.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(app_schema, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(app_schema)} universities (app schema) to {out_path}")

    report_path = PROCESSED_OUTPUT_DIR / "coverage_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(coverage_report, f, ensure_ascii=False, indent=2)
    print(f"wrote coverage report to {report_path}")

    return {"validated_count": len(validated), "total_count": len(raw)}