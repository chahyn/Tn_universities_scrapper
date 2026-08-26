"""
Example scraper for a JS-rendered university site (SPA, lazy-loaded content)
using Playwright's sync API. Use this when requests+BeautifulSoup returns an
empty shell because the real content is injected by JavaScript.

Same idea as example_static_source.py: replace URL and selectors per site.
"""
from playwright.sync_api import sync_playwright

from scraper.base_scraper import BaseScraper
from scraper.models import RawUniversity, RawLocation


class ExampleJsSource(BaseScraper):
    source_name = "example_js_university"

    LISTING_URL = "https://example-js-university.tn"  # replace me

    def scrape(self) -> list[RawUniversity]:
        universities: list[RawUniversity] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(self.LISTING_URL, wait_until="networkidle")

            # --- Replace with real selectors once the page has rendered ---
            name = page.locator("h1.university-name").first
            description = page.locator("div.description").first
            address = page.locator("span.address").first
            city = page.locator("span.city").first
            # ----------------------------------------------------------------

            universities.append(
                RawUniversity(
                    name=name.text_content().strip() if name.count() else "Unknown",
                    type="PUBLIC",
                    description=description.text_content().strip() if description.count() else None,
                    website=self.LISTING_URL,
                    location=RawLocation(
                        address=address.text_content().strip() if address.count() else None,
                        city=city.text_content().strip() if city.count() else None,
                    ),
                    source_url=self.LISTING_URL,
                    source_name=self.source_name,
                )
            )

            browser.close()

        self.polite_wait()
        return universities
