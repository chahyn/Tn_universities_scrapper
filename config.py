"""Central config, loaded once from environment variables."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
RAW_OUTPUT_DIR = BASE_DIR / os.getenv("RAW_OUTPUT_DIR", "output/raw")
PROCESSED_OUTPUT_DIR = BASE_DIR / os.getenv("PROCESSED_OUTPUT_DIR", "output/processed")

GEOCODER = os.getenv("GEOCODER", "nominatim")  # "nominatim" or "google"
GOOGLE_GEOCODING_API_KEY = os.getenv("GOOGLE_GEOCODING_API_KEY", "")

REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "1.5"))

# e.g. postgresql://user:password@localhost:5432/tn_universities
# Only required if you run main.py --load-db / python -m etl.load_db.
DATABASE_URL = os.getenv("DATABASE_URL", "")

RAW_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)