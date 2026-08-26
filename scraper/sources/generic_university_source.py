"""
Generic, heuristic-based scraper for any Tunisian university site listed in
data/universities_seed.json.

Unlike universite_centrale.py (which is hand-tuned to one site's structure),
this scraper has to work across many different, unknown sites -- so it
relies only on very generic signals: meta tags, mailto:/tel: links, JSON-LD
structured data, <address>/footer blocks, and common nav-link keywords
(ecole/faculte/institut for tags, club/vie-etudiante for clubs,
actualites/blog/news for events, formation/filiere for programs).

This means coverage per university will vary a lot: some sites will give
up name/description/contact easily; others (heavy JS, unusual layout,
Arabic-only text, etc.) may return mostly nulls. That's expected -- treat
low completeness on a given source as a signal that it deserves its own
dedicated scraper (copy universite_centrale.py as a starting point) rather
than a bug in this generic one.

For PUBLIC universities, this source also enriches the record with
`sub_institutions`: the constituent faculties/schools/institutes listed
under that university on universite.tn (e.g. Universite de Carthage ->
ENICarthage, INSAT, Sup'Com, ...). See scraper/sources/subinstitutions.py.

--- Clubs/events/programs: structural filtering ---
Earlier versions grabbed every h1-h4 heading on whichever linked page
matched a keyword. That pulls in the page's OWN title/banner heading as a
fake club/event -- e.g. following a nav link literally labelled
"Associations" lands on a page whose <h1> is also "Associations", and the
old code returned that <h1> as if it were a club. It also picked up other
nav labels ("Contact", "Services") the same way.

This version requires headings to belong to a *repeated* sibling structure
(a card/list grid -- the actual signal a real listing page gives you) and
excludes anything inside <nav>/<header>/<footer>. A lone page-banner
heading is never part of a group of 3+ siblings sharing the same parent
tag/class, so it's correctly dropped instead of returned as a "club". A
page with only that one-off banner and no real listing correctly yields
nothing rather than nav junk; that's a coverage trade-off, not a bug --
it's the signal that this particular site needs a dedicated scraper.
"""
import json
import re
import unicodedata
import urllib3
from collections import defaultdict
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from scraper.base_scraper import BaseScraper
from scraper.models import RawUniversity, RawLocation, RawClub, RawEvent, RawProgram
from scraper.sources.subinstitutions import find_sub_institutions

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; tn-universities-scraper/1.0)"}

CLUB_LINK_KEYWORDS = ["club", "vie-etudiante", "vie_etudiante", "student-life", "associations"]
EVENT_LINK_KEYWORDS = ["actualite", "blog", "news", "evenement", "event"]
PROGRAM_LINK_KEYWORDS = ["formation", "filiere", "filière", "programme", "cursus", "diplome", "diplôme"]

# Minimum number of headings sharing the same (parent tag, parent class)
# signature before we trust them as a real repeated listing rather than a
# one-off page banner/title. 3 is a deliberately low bar -- real
# club/event/program grids usually have far more than 3 -- chosen so small
# but genuine listings aren't thrown out, while a single banner heading
# (group size 1) never qualifies.
MIN_REPEATED_GROUP_SIZE = 3

# Headings we never accept as club/event/program names even if they somehow
# sit in a repeated group, because these words show up constantly as
# structural section labels on Tunisian .rnu.tn sites regardless of layout.
GENERIC_HEADING_BLOCKLIST = {
    "contact", "contacts", "accueil", "services", "etudiants", "étudiants",
    "la faculte", "la faculté", "galerie de photos", "historique",
    "organigramme", "enseignants", "departements", "départements",
    "partenariat", "projets", "conseil scientifique", "associations",
    "clubs et associations", "espace des clubs", "vie etudiante",
    "vie étudiante", "mot du doyen", "mot de la doyenne",
    "evenements", "évènements", "actualites", "actualités",
    "liens utiles", "suivez-nous",
}

# A number of Tunisian .rnu.tn sites share/misconfigure their TLS certs
# (hostname mismatch, expired, etc.) even though the content itself is
# publicly served and fine to read. We retry once without verification
# rather than losing the whole source over a cert issue. Since we do this,
# silence the "InsecureRequestWarning" noise that urllib3 would otherwise
# print for every one of those retries.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class GenericUniversitySource(BaseScraper):
    """One instance = one university site. source_name is set per-instance
    (a slug of the name) so each gets its own output/raw/<slug>.json file."""

    def __init__(
        self,
        name_hint: str,
        type_hint: str | None,
        website: str,
        include_sub_institutions: bool = True,
        fetch_sub_institution_websites: bool = True,
    ):
        self.name_hint = name_hint
        self.type_hint = type_hint
        self.website = website.rstrip("/") + "/"
        self.source_name = self._slugify(name_hint)
        # Sub-institution lookup only makes sense for PUBLIC universities --
        # private establishments generally don't have constituent schools.
        self.include_sub_institutions = include_sub_institutions and type_hint == "PUBLIC"
        self.fetch_sub_institution_websites = fetch_sub_institution_websites
        # Several .rnu.tn hosts fail cert verification on every request (see
        # _fetch). Once we've had to fall back to verify=False for a given
        # host, remember it -- scrape() fetches the homepage plus up to 3
        # linked sub-pages (programs/clubs/events), all on the same host, so
        # without this we'd repeat the same failed handshake 4 times.
        self._insecure_hosts: set[str] = set()

    @staticmethod
    def _slugify(text: str) -> str:
        # Strip accents to their plain ASCII equivalent BEFORE collapsing
        # non-alphanumeric runs, so "Université de Gabès" -> "universite de
        # gabes" -> "universite_de_gabes" instead of losing the accented
        # letters and producing "universit_de_gab_s".
        ascii_text = "".join(
            c for c in unicodedata.normalize("NFKD", text)
            if not unicodedata.combining(c)
        )
        slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
        return slug or "unknown_university"

    def scrape(self) -> list[RawUniversity]:
        soup, page_text = self._fetch(self.website)
        if soup is None:
            raise RuntimeError(f"could not fetch {self.website}")

        address, city = self._extract_address(soup, page_text)

        sub_institutions = []
        if self.include_sub_institutions:
            try:
                sub_institutions = find_sub_institutions(
                    self.name_hint, fetch_websites=self.fetch_sub_institution_websites
                )
            except Exception as exc:
                # Enrichment failing should never take down the whole
                # university record -- just log it and move on with [].
                print(f"  [{self.source_name}] sub-institution lookup failed: {exc}")

        university = RawUniversity(
            name=self._extract_name(soup) or self.name_hint,
            type=self.type_hint,
            description=self._extract_description(soup),
            logo_url=self._extract_logo(soup),
            cover_image_url=None,
            website=self.website,
            email=self._extract_email(soup, page_text),
            phone=self._extract_phone(soup, page_text),
            location=RawLocation(address=address, city=city),
            tags=self._extract_tags(soup),
            programs=self._scrape_linked_section(soup, PROGRAM_LINK_KEYWORDS, self._parse_programs_page),
            clubs=self._scrape_linked_section(soup, CLUB_LINK_KEYWORDS, self._parse_clubs_page),
            events=self._scrape_linked_section(soup, EVENT_LINK_KEYWORDS, self._parse_events_page),
            sub_institutions=sub_institutions,
            source_url=self.website,
            source_name=self.source_name,
        )

        self.polite_wait()
        return [university]

    # --- fetching ---

    def _fetch(self, url: str) -> tuple[BeautifulSoup | None, str]:
        host = urlparse(url).netloc
        verify = host not in self._insecure_hosts

        try:
            resp = requests.get(url, timeout=15, headers=HEADERS, verify=verify)
            resp.raise_for_status()
        except requests.exceptions.SSLError as exc:
            if not verify:
                # We already knew this host needed verify=False and it
                # STILL failed -- a genuinely broken/unreachable host, not
                # just a cert mismatch. Don't loop, just give up on this URL.
                print(f"  [{self.source_name}] fetch failed for {url} even without SSL verify: {exc}")
                return None, ""
            # Several .rnu.tn sites serve a cert that doesn't match their own
            # hostname (shared/misconfigured certs). The content itself is
            # public, so retry once without verification rather than losing
            # the whole source over this -- and remember the host so the
            # other 2-3 sub-page fetches in this same scrape() don't repeat
            # the same failed handshake.
            print(f"  [{self.source_name}] SSL verify failed for {url} ({exc}); retrying without verification")
            self._insecure_hosts.add(host)
            try:
                resp = requests.get(url, timeout=15, headers=HEADERS, verify=False)
                resp.raise_for_status()
            except requests.RequestException as exc2:
                print(f"  [{self.source_name}] fetch failed for {url} even without SSL verify: {exc2}")
                return None, ""
        except requests.RequestException as exc:
            print(f"  [{self.source_name}] fetch failed for {url}: {exc}")
            return None, ""
        soup = BeautifulSoup(resp.text, "html.parser")
        return soup, soup.get_text(separator="\n")

    # --- field extractors ---

    def _extract_name(self, soup: BeautifulSoup) -> str | None:
        for selector in ('meta[property="og:site_name"]', 'meta[name="application-name"]'):
            meta = soup.select_one(selector)
            if meta and meta.get("content"):
                return meta["content"].strip()
        if soup.title and soup.title.get_text(strip=True):
            return soup.title.get_text(strip=True).split("|")[0].split("-")[0].strip()
        return None

    def _extract_description(self, soup: BeautifulSoup) -> str | None:
        for selector in ('meta[name="description"]', 'meta[property="og:description"]'):
            meta = soup.select_one(selector)
            if meta and meta.get("content") and len(meta["content"].strip()) > 40:
                return meta["content"].strip()
        # fallback: first reasonably long paragraph on the page
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) > 80:
                return text
        return None

    def _extract_logo(self, soup: BeautifulSoup) -> str | None:
        img = soup.select_one('img[alt*="logo" i]') or soup.select_one('img[src*="logo" i]')
        if not img or not img.has_attr("src"):
            return None
        return urljoin(self.website, img["src"])

    def _extract_email(self, soup: BeautifulSoup, page_text: str) -> str | None:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto:
            return mailto["href"].replace("mailto:", "").split("?")[0].strip()
        match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", page_text)
        return match.group(0) if match else None

    # --- JSON-LD structured data (schema.org) ---
    # Several university sites embed an Organization/CollegeOrUniversity
    # JSON-LD block in <head> with clean address/phone fields. This is a
    # far more reliable source than scraping visible text when present, so
    # we try it first for both phone and address.

    def _extract_json_ld(self, soup: BeautifulSoup) -> dict | None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            candidates = data if isinstance(data, list) else [data]
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("@type") in (
                    "Organization", "CollegeOrUniversity", "EducationalOrganization",
                ):
                    return candidate
        return None

    def _extract_phone(self, soup: BeautifulSoup, page_text: str) -> str | None:
        # 1. tel: links are unambiguous when present.
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            return tel["href"].replace("tel:", "").strip()

        # 2. JSON-LD structured data.
        ld = self._extract_json_ld(soup)
        if ld and ld.get("telephone"):
            return str(ld["telephone"]).strip()

        # 3. Fallback: label-based regex over visible text. Broadened to
        # cover "Standard"/"GSM" labels seen on some faculty sites in
        # addition to Tel/Phone.
        match = re.search(
            r"(?:T[ée]l\.?|Tel\.?|Phone|GSM|Standard)\s*:?\s*([+()\d\s.-]{8,20})",
            page_text,
        )
        return match.group(1).strip() if match else None

    def _extract_address(self, soup: BeautifulSoup, page_text: str) -> tuple[str | None, str | None]:
        # 1. JSON-LD structured data -- cleanest source when present.
        ld = self._extract_json_ld(soup)
        if ld and isinstance(ld.get("address"), dict):
            addr = ld["address"]
            street = addr.get("streetAddress")
            city = addr.get("addressLocality")
            if street or city:
                full = ", ".join(part for part in [street, city] if part)
                return full or None, city

        # 2. A dedicated <address> tag, or common contact/footer containers.
        for el in soup.find_all("address"):
            text = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
            if text and len(text) > 5:
                return text, self._guess_city(text)

        for selector in (
            '[class*="adresse" i]', '[class*="address" i]',
            '[id*="adresse" i]', '[id*="address" i]',
            'footer [class*="contact" i]', 'footer',
        ):
            el = soup.select_one(selector)
            if not el:
                continue
            text = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
            m = re.search(r"(?:Adresse|Address)\s*:?\s*(.{5,150})", text)
            if m:
                full_address = m.group(1).strip()
                return full_address, self._guess_city(full_address)

        # 3. Fallback: original label-based regex over the whole page text.
        match = re.search(r"(?:Adresse|Address)\s*:?\s*(.+)", page_text)
        if not match:
            return None, None
        full_address = match.group(1).strip()[:200]
        return full_address, self._guess_city(full_address)

    @staticmethod
    def _guess_city(address_text: str) -> str | None:
        # crude city guess: look for a known-ish capitalized trailing word/phrase
        city_match = re.search(r"([A-ZÀ-Ý][a-zà-ÿ]+(?:\s[A-ZÀ-Ý][a-zà-ÿ]+)?)\s*$", address_text)
        return city_match.group(1) if city_match else None

    def _extract_tags(self, soup: BeautifulSoup) -> list[str]:
        links = soup.select(
            'a[href*="ecole" i], a[href*="faculte" i], a[href*="institut" i], '
            'a[href*="faculty" i], a[href*="school" i]'
        )
        tags = {a.get_text(strip=True) for a in links if a.get_text(strip=True)}
        return sorted(tags)[:15]  # cap to avoid pulling in an entire nav menu as "tags"

    # --- best-effort linked-section scraping (clubs / events / programs) ---

    def _scrape_linked_section(self, soup: BeautifulSoup, keywords: list[str], parser) -> list:
        link = None
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            if any(kw in href for kw in keywords):
                link = urljoin(self.website, a["href"])
                break
        if not link:
            return []

        sub_soup, _ = self._fetch(link)
        self.polite_wait()
        if sub_soup is None:
            return []
        return parser(sub_soup, link)

    def _structurally_repeated_headings(self, soup: BeautifulSoup) -> list:
        """
        Return headings (h1-h4) that belong to a repeated sibling structure
        -- i.e. several headings share the same parent tag+class -- which is
        the actual signal a card/list grid gives you. A lone page-title/
        banner heading (the #1 source of false positives -- e.g. landing on
        a page whose own <h1> is literally the nav link's label, like
        "Associations") is a group of size 1 and is dropped. Headings inside
        <nav>/<header>/<footer>, or matching a known generic structural
        label, are dropped regardless of repetition.
        """
        groups: dict[tuple, list] = defaultdict(list)
        for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
            if heading.find_parent(["nav", "header", "footer"]):
                continue
            text = heading.get_text(strip=True)
            if not text or len(text) > 120:
                continue
            if text.strip().lower() in GENERIC_HEADING_BLOCKLIST:
                continue
            parent = heading.parent
            if parent is None:
                continue
            signature = (parent.name, tuple(parent.get("class") or []))
            groups[signature].append(heading)

        accepted = []
        for signature, headings in groups.items():
            if len(headings) >= MIN_REPEATED_GROUP_SIZE:
                accepted.extend(headings)
        return accepted

    def _parse_clubs_page(self, soup: BeautifulSoup, page_url: str) -> list[RawClub]:
        clubs: dict[str, RawClub] = {}
        for heading in self._structurally_repeated_headings(soup):
            text = heading.get_text(strip=True)
            if text in clubs:
                continue
            nearby_img = heading.find_previous("img") or heading.find_next("img")
            logo = urljoin(page_url, nearby_img["src"]) if nearby_img and nearby_img.has_attr("src") else None
            clubs[text] = RawClub(name=text, description=None, logo_url=logo, contact_info=None)
        return list(clubs.values())[:25]  # sanity cap

    def _parse_events_page(self, soup: BeautifulSoup, page_url: str) -> list[RawEvent]:
        events: dict[str, RawEvent] = {}
        for heading in self._structurally_repeated_headings(soup):
            text = heading.get_text(strip=True)
            if text not in events:
                events[text] = RawEvent(title=text, description=None, start_date=None, end_date=None)
        return list(events.values())[:25]  # sanity cap

    # --- degree-type inference for programs ---
    _DEGREE_PATTERNS = (
        (re.compile(r"licence|bachelor", re.IGNORECASE), "LICENCE"),
        (re.compile(r"mast[eè]re|master", re.IGNORECASE), "MASTER"),
        (re.compile(r"ing[ée]nieur|engineering", re.IGNORECASE), "ENGINEERING"),
        (re.compile(r"doctorat|phd|doctoral", re.IGNORECASE), "PHD"),
    )

    @classmethod
    def _infer_degree_type(cls, title: str) -> str | None:
        for pattern, degree in cls._DEGREE_PATTERNS:
            if pattern.search(title):
                return degree
        return None

    def _parse_programs_page(self, soup: BeautifulSoup, page_url: str) -> list[RawProgram]:
        programs: dict[str, RawProgram] = {}
        for heading in self._structurally_repeated_headings(soup):
            text = heading.get_text(strip=True)
            if text in programs:
                continue
            programs[text] = RawProgram(
                name=text,
                degree_type=self._infer_degree_type(text),
                duration_years=None,
                description=None,
            )
        return list(programs.values())[:40]  # programs pages tend to list more items than clubs/events