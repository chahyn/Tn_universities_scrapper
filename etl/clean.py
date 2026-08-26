"""Normalize raw scraped fields before anything else touches them."""
import re
import unicodedata


def normalize_whitespace(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\s+", " ", value).strip() or None


def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"[^\d+]", "", value)
    return digits or None


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower()
    return value if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value) else None


def normalize_key(value: str) -> str:
    """Lowercase, accent-stripped, punctuation-free form used for dedupe matching."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-z0-9\s]", "", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def clean_university(raw: dict) -> dict:
    """Run field-level cleaning over one raw university dict (already model_dump()'d)."""
    raw["name"] = normalize_whitespace(raw.get("name"))
    raw["description"] = normalize_whitespace(raw.get("description"))
    raw["email"] = normalize_email(raw.get("email"))
    raw["phone"] = normalize_phone(raw.get("phone"))

    if raw.get("location"):
        raw["location"]["address"] = normalize_whitespace(raw["location"].get("address"))
        raw["location"]["city"] = normalize_whitespace(raw["location"].get("city"))

    # normalize_whitespace() returns None for a whitespace-only tag, and
    # sorted() on a set containing None raises TypeError -- so filter AFTER
    # normalizing, not before.
    raw["tags"] = sorted({t for t in (normalize_whitespace(t) for t in raw.get("tags") or []) if t})
    return raw
