"""
Final gate before a record is considered ready for DB import.
Doesn't invent data — flags what's missing so it goes in NULL, and so it
can be counted for the coverage report the deliverable asks for.
"""
from typing import Optional, Literal
from pydantic import BaseModel, EmailStr, field_validator


class CleanLocation(BaseModel):
    address: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Which geocoding tier produced the coordinates above:
    #   "address"     -- geocoded the full scraped street address
    #   "institution" -- geocoded "<name>, <city>, Tunisia"
    #   "city"        -- city centroid only; good enough to place on a map,
    #                    NOT good enough for "navigate me there"
    #   None          -- no coordinates
    geocode_precision: Optional[Literal["address", "institution", "city"]] = None


class CleanUniversity(BaseModel):
    name: str
    aliases: list[str] = []
    type: Optional[Literal["PUBLIC", "PRIVATE"]] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[CleanLocation] = None
    tags: list[str] = []
    programs: list[dict] = []
    clubs: list[dict] = []
    events: list[dict] = []
    source_urls: list[str] = []

    @field_validator("name")
    @classmethod
    def name_must_exist(cls, v: str) -> str:
        if not v or v == "Unknown":
            raise ValueError("university name is missing or a placeholder")
        return v


# Top-level scalar fields whose absence is worth reporting.
REQUIRED_FOR_COVERAGE = [
    "name", "type", "description", "website", "email", "phone",
]

# Fields that are lists -- an empty list counts as missing. These were left
# out of the coverage report entirely, which is why it reported healthy
# coverage while tags/clubs/events were full of nav-menu noise.
LIST_FIELDS_FOR_COVERAGE = ["tags", "clubs", "events"]

# Nested location fields, reported as "location.<field>".
LOCATION_FIELDS_FOR_COVERAGE = ["city", "latitude"]


def validate_university(raw: dict) -> tuple[CleanUniversity | None, list[str]]:
    """Returns (validated model or None, list of missing required fields)."""
    missing = [f for f in REQUIRED_FOR_COVERAGE if not raw.get(f)]
    missing += [f for f in LIST_FIELDS_FOR_COVERAGE if not raw.get(f)]

    # Report the nested location fields whether or not a location dict
    # exists -- previously a wholly missing location was silently fine,
    # which hid the fact that 0/107 records had coordinates.
    location = raw.get("location") or {}
    missing += [f"location.{f}" for f in LOCATION_FIELDS_FOR_COVERAGE if not location.get(f)]

    try:
        model = CleanUniversity(**raw)
        return model, missing
    except Exception as exc:
        print(f"  rejected '{raw.get('name')}': {exc}")
        return None, missing
