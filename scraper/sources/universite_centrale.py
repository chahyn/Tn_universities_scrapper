"""
Real scraper for Universite Centrale (universitecentrale.net).

The site is server-rendered (Next.js SSR), so requests + BeautifulSoup is
enough -- no Playwright needed here.

Note on selector strategy: this is a Next.js site, so most CSS classes are
auto-generated/hashed and will change on the next deploy. Instead of
guessing at those, this scraper anchors on things that are stable across
rebuilds: the og:site_name meta tag, mailto:/tel: link patterns, label
text ("Adresse :", "Tel.") in the contact block, and heading text markers
for the clubs/events sections.

Known limitations of this source (not bugs -- the site just doesn't expose
this data as scrapable text):
- Clubs: the site doesn't list individual named clubs anywhere in the
  markup. It only names 5 thematic "work axes" (Sport, Developpement
  Personnel et Carriere, etc.) on /student-life, plus a row of partner-
  style logos with no attached names. We scrape the 5 named axes as
  RawClub entries since that's the only real club-level data available.
- Events: there's no dated events calendar. The closest equivalent is the
  /blog news feed on the homepage, which has titles and links but no
  dates on the teaser cards. Getting real dates would mean fetching each
  individual blog post page -- left as a future enhancement, not done
  here to keep this scraper to one page fetch per section.
"""
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.base_scraper import BaseScraper
from scraper.models import RawUniversity, RawLocation, RawClub, RawEvent


class UniversiteCentraleSource(BaseScraper):
    source_name = "universite_centrale"

    HOME_URL = "https://www.universitecentrale.net/"
    STUDENT_LIFE_URL = "https://www.universitecentrale.net/student-life"

    def scrape(self) -> list[RawUniversity]:
        home_resp = requests.get(self.HOME_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        home_resp.raise_for_status()
        home_soup = BeautifulSoup(home_resp.text, "html.parser")
        page_text = home_soup.get_text(separator="\n")

        name = self._extract_name(home_soup)
        description = self._extract_description(home_soup)
        logo_url = self._extract_logo(home_soup)
        email = self._extract_email(home_soup, page_text)
        phone = self._extract_phone(page_text)
        address, city = self._extract_address(page_text)
        tags = self._extract_school_tags(home_soup)
        events = self._extract_events(home_soup)

        clubs = self._scrape_clubs()

        university = RawUniversity(
            name=name,
            type="PRIVATE" if description and "priv" in description.lower() else None,
            description=description,
            logo_url=logo_url,
            cover_image_url=None,  # no reliable cover image on this page
            website=self.HOME_URL,
            email=email,
            phone=phone,
            location=RawLocation(address=address, city=city),
            tags=tags,
            clubs=clubs,
            events=events,
            source_url=self.HOME_URL,
            source_name=self.source_name,
        )

        self.polite_wait()
        return [university]

    # --- field extractors, each isolated so a future breakage is easy to find ---

    def _extract_name(self, soup: BeautifulSoup) -> str:
        meta = soup.select_one('meta[property="og:site_name"]')
        if meta and meta.get("content"):
            return meta["content"].strip()
        return "Universite Centrale"  # fallback, should rarely trigger

    def _extract_description(self, soup: BeautifulSoup) -> str | None:
        candidate = soup.find(lambda tag: tag.name == "p" and "Fond" in tag.get_text())
        return candidate.get_text(strip=True) if candidate else None

    def _extract_logo(self, soup: BeautifulSoup) -> str | None:
        img = soup.select_one('img[alt*="Logo"]')
        if not img or not img.has_attr("src"):
            return None
        return urljoin(self.HOME_URL, img["src"])

    def _extract_email(self, soup: BeautifulSoup, page_text: str) -> str | None:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto:
            return mailto["href"].replace("mailto:", "").strip()
        match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", page_text)
        return match.group(0) if match else None

    def _extract_phone(self, page_text: str) -> str | None:
        match = re.search(r"T[ée]l\.?\s*:?\s*([+()\d\s]{8,20})", page_text)
        return match.group(1).strip() if match else None

    def _extract_address(self, page_text: str) -> tuple[str | None, str | None]:
        match = re.search(r"Adresse\s*:\s*(.+)", page_text)
        if not match:
            return None, None
        full_address = match.group(1).strip()
        city_match = re.search(r"(Tunis\w*)", full_address, re.IGNORECASE)
        city = city_match.group(1) if city_match else None
        return full_address, city

    def _extract_school_tags(self, soup: BeautifulSoup) -> list[str]:
        links = soup.select('a[href*="/ecole/"]')
        tags = {a.get_text(strip=True) for a in links if a.get_text(strip=True)}
        return sorted(tags)

    def _extract_events(self, soup: BeautifulSoup) -> list[RawEvent]:
        """
        Uses the /blog teaser cards on the homepage as a stand-in for events.
        No dates are available on these teaser cards, so start_date/end_date
        stay None -- that's a real data gap, not a scraping bug.
        """
        events: dict[str, RawEvent] = {}
        for link in soup.select('a[href*="/blog/"]'):
            href = link.get("href", "")
            if href.rstrip("/").endswith("/blog"):
                continue  # skip the "Voir toutes les actualites" nav link

            heading = link.find_previous(["h1", "h2", "h3", "h4"])
            title = heading.get_text(strip=True) if heading else link.get_text(strip=True)
            if not title or title in events:
                continue

            events[title] = RawEvent(title=title, description=None, start_date=None, end_date=None)

        return list(events.values())

    def _scrape_clubs(self) -> list[RawClub]:
        """
        Fetches /student-life and extracts the 5 named club "work axes"
        (the only individually-named club-related entities this site
        exposes as text -- see module docstring for why).
        """
        try:
            resp = requests.get(self.STUDENT_LIFE_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"  student-life page fetch failed: {exc}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        self.polite_wait()

        start = soup.find(lambda t: t.name in ("h1", "h2", "h3") and "AXES DE TRAVAIL DES CLUBS" in t.get_text())
        end = soup.find(lambda t: t.name in ("h1", "h2", "h3") and "COMMUNAUTÉ SPORTIVE" in t.get_text())
        if not start:
            return []

        clubs: dict[str, RawClub] = {}
        for el in start.find_all_next():
            if end and el is end:
                break
            if el.name == "img" and el.get("alt"):
                club_name = el["alt"].strip()
                if club_name and club_name not in clubs:
                    logo_src = el.get("src", "")
                    clubs[club_name] = RawClub(
                        name=club_name,
                        description=None,
                        logo_url=urljoin(self.STUDENT_LIFE_URL, logo_src) if logo_src else None,
                        contact_info=None,
                    )

        return list(clubs.values())