"""
Raw models = exactly what the scraper is responsible for collecting.
No id, no createdAt/updatedAt, no latitude/longitude (that's ETL's job).
These map to your Postgres schema minus the fields the DB or ETL generate.
"""
from typing import Optional, Literal
from pydantic import BaseModel, Field


class RawLocation(BaseModel):
    address: Optional[str] = None
    city: Optional[str] = None
    # latitude / longitude are filled in later by etl/geocode.py, not here


class RawProgram(BaseModel):
    name: str
    degree_type: Optional[Literal["LICENCE", "MASTER", "ENGINEERING", "PHD"]] = None
    duration_years: Optional[int] = None
    description: Optional[str] = None


class RawClub(BaseModel):
    name: str
    description: Optional[str] = None
    logo_url: Optional[str] = None
    contact_info: Optional[str] = None


class RawEvent(BaseModel):
    title: str
    description: Optional[str] = None
    start_date: Optional[str] = None  # kept as raw string; parsed to datetime in ETL
    end_date: Optional[str] = None


class RawSubInstitution(BaseModel):
    """
    A constituent faculty / school / institute under a parent (umbrella)
    university -- e.g. Universite de Carthage's ENICarthage, INSAT, Sup'Com,
    etc. Only public universities in Tunisia are structured this way; most
    private establishments ARE the institution (no children to enumerate).
    """
    name: str
    website: Optional[str] = None
    type_label: Optional[str] = None  # e.g. "Faculte", "Ecole", "Institut" -- best-effort, may be None


class RawUniversity(BaseModel):
    # Core fields the scraper fills in directly
    name: str
    type: Optional[Literal["PUBLIC", "PRIVATE"]] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    # Nested raw data
    location: Optional[RawLocation] = None
    programs: list[RawProgram] = Field(default_factory=list)
    clubs: list[RawClub] = Field(default_factory=list)
    events: list[RawEvent] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sub_institutions: list[RawSubInstitution] = Field(default_factory=list)

    # Provenance — keep this, it's gold for the "scraping process" doc later
    source_url: str
    source_name: str