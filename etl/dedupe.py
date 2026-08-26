"""
Merge duplicate universities that showed up across multiple sources
(e.g. the ministry directory AND the university's own site).
Match key = normalized name + city, since names can be scraped with
slightly different spelling/spacing per source.
"""
from etl.clean import normalize_key


def _merge_record(base: dict, extra: dict) -> dict:
    """Fill in any field that's missing in `base` using `extra`. Base wins on conflicts."""
    for key, value in extra.items():
        if key in ("tags", "programs", "clubs", "events"):
            continue  # merged separately below
        if not base.get(key) and value:
            base[key] = value

    base["tags"] = sorted(set(base.get("tags", [])) | set(extra.get("tags", [])))
    for list_field in ("programs", "clubs", "events"):
        base.setdefault(list_field, [])
        base[list_field].extend(extra.get(list_field, []))

    base.setdefault("source_urls", [base.get("source_url")])
    if extra.get("source_url") and extra["source_url"] not in base["source_urls"]:
        base["source_urls"].append(extra["source_url"])

    return base


def dedupe_universities(universities: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}

    for uni in universities:
        city = (uni.get("location") or {}).get("city") or ""
        key = f"{normalize_key(uni['name'])}|{normalize_key(city)}"

        if key in merged:
            merged[key] = _merge_record(merged[key], uni)
        else:
            merged[key] = uni

    return list(merged.values())
