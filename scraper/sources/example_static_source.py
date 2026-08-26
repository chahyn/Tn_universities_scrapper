"""
Example scraper for a static (non-JS) university website using requests + BeautifulSoup.
Use this pattern when the page's HTML already contains the data (view-source has it).

This is a TEMPLATE — replace the URL and CSS selectors with the real ones for
each university site you target. Duplicate this file per source, or per
"family" of sites that share the same HTML structure.
"""
import requests
from bs4 import BeautifulSoup

from scraper.base_scraper import BaseScraper
from scraper.models import RawUniversity, RawLocation


class ExampleStaticSource(BaseScraper):
    source_name = "example_static_university"

    LISTING_URL = "https://example-university.tn/about"  # replace me

    def scrape(self) -> list[RawUniversity]:
        universities: list[RawUniversity] = []

        resp = requests.get(self.LISTING_URL, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # --- Replace these selectors with the real ones for the target site ---
        name = soup.select_one("h1.university-name")
        description = soup.select_one("div.description")
        address = soup.select_one("span.address")
        city = soup.select_one("span.city")
        email = soup.select_one("a[href^='mailto:']")
        phone = soup.select_one("a[href^='tel:']")
        logo = soup.select_one("img.logo")
        cover = soup.select_one("img.cover")
        tag_nodes = soup.select("ul.tags li")
        # ------------------------------------------------------------------

        universities.append(
            RawUniversity(
                name=name.get_text(strip=True) if name else "Unknown",
                type="PRIVATE",  # set from what you know about this source
                description=description.get_text(strip=True) if description else None,
                logo_url=logo["src"] if logo and logo.has_attr("src") else None,
                cover_image_url=cover["src"] if cover and cover.has_attr("src") else None,
                website=self.LISTING_URL,
                email=email.get_text(strip=True) if email else None,
                phone=phone.get_text(strip=True) if phone else None,
                location=RawLocation(
                    address=address.get_text(strip=True) if address else None,
                    city=city.get_text(strip=True) if city else None,
                ),
                tags=[t.get_text(strip=True) for t in tag_nodes],
                source_url=self.LISTING_URL,
                source_name=self.source_name,
            )
        )

        self.polite_wait()
        return universities
