"""
Turn address + city into latitude/longitude.
Uses OpenStreetMap's Nominatim by default (free, no API key, rate-limited to
~1 req/sec — respect that). Swap in Google Geocoding by setting GEOCODER=google
and providing GOOGLE_GEOCODING_API_KEY in .env if you need better TN coverage.
"""
import time
import requests

from config import GEOCODER, GOOGLE_GEOCODING_API_KEY

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GOOGLE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def _geocode_nominatim(query: str) -> tuple[float, float] | None:
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1, "countrycodes": "tn"},
        headers={"User-Agent": "tn-universities-app-scraper/1.0"},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    time.sleep(1)  # Nominatim's usage policy: max ~1 req/sec
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


def _geocode_google(query: str) -> tuple[float, float] | None:
    resp = requests.get(
        GOOGLE_URL,
        params={"address": query, "key": GOOGLE_GEOCODING_API_KEY, "region": "tn"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "OK" or not data.get("results"):
        return None
    loc = data["results"][0]["geometry"]["location"]
    return loc["lat"], loc["lng"]


def geocode_address(address: str | None, city: str | None) -> tuple[float, float] | None:
    if not address and not city:
        return None
    query = ", ".join(part for part in [address, city, "Tunisia"] if part)

    try:
        if GEOCODER == "google" and GOOGLE_GEOCODING_API_KEY:
            return _geocode_google(query)
        return _geocode_nominatim(query)
    except requests.RequestException as exc:
        print(f"  geocoding failed for '{query}': {exc}")
        return None


def geocode_university(uni: dict) -> dict:
    location = uni.get("location")
    if not location:
        return uni

    coords = geocode_address(location.get("address"), location.get("city"))
    if coords:
        location["latitude"], location["longitude"] = coords
    else:
        location["latitude"], location["longitude"] = None, None
    return uni
