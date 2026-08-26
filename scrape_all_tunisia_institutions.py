#!/usr/bin/env python3
"""
scrape_all_tunisia_institutions.py

Builds a JSON list of Tunisian higher-ed institutions -- the 14 top-level
public universities (13 traditional + Universite Virtuelle de Tunis), EVERY
constituent faculty/school/institute under each of them, the ISETs, and
private institutions -- in the format:

    [{"name": ..., "type": "PUBLIC" | "PRIVATE", "website": ... or null}, ...]

Data source: https://www.universite.tn.

WHAT CHANGED IN THIS VERSION (v3)
----------------------------------
Two real problems showed up on an actual run:

1. Intermittent 403 Forbidden on some pages (ISET listing, a private
   category page), even though nearby, similarly-shaped requests
   succeeded. This looks like basic bot-mitigation (burst/behavior based),
   not a hard IP ban -- so:
     - the session now does a one-time "warm up" GET of the homepage
       before anything else, to pick up cookies like a real browser would
     - on a 403 specifically, the retry backs off much longer AND
       re-warms the session before trying again
     - the rate limiter now adds small random jitter instead of a
       perfectly metronomic delay
     - default concurrency was lowered (5 -> 3 workers) and default
       delay raised (0.6s -> 0.8s)
     - every URL that's ultimately given up on is written to
       `<out>.failed_urls.txt` so you can see exactly what's missing and
       optionally re-run later when/if the rate limiting has cooled off

2. The private-institution scraper found almost nothing (ESPRIT, EPI,
   TEK-UP, SESAME, etc. were all missing). The previous version assumed
   institution detail links matched a specific URL shape
   (".../Universite-privee-<something>.html"). A real run showed that
   assumption was wrong -- the governorate pages loaded fine but almost
   no links matched that shape, meaning the real site doesn't structure
   institution URLs that way (they're likely flat top-level slugs named
   after the institution itself, not nested under the category page).
   Rather than guess a second specific pattern blindly, this version uses
   a pattern-agnostic approach: it collects every internal link from all
   8 governorate pages, then tells "this is a nav/menu link" apart from
   "this is an institution" using CROSS-PAGE FREQUENCY -- a link that
   shows up on most/all 8 governorate pages is almost certainly
   boilerplate navigation, while a link that shows up on only one or two
   pages is almost certainly a specific institution listed there. That
   plus a small denylist of obvious nav text (Accueil, Contact, etc.)
   should surface real institutions regardless of their exact URL shape.

   There's also now a `--dump-links PATH` diagnostic mode: it fetches one
   page and prints/saves every link + its text, so if this still misses
   things you can run that once and send me the output -- real HTML beats
   another blind guess.

HONESTY NOTE: I have not been able to execute this against the live site
myself (no network path to universite.tn from where I write this), so
everything above is inference from the error log you shared, not from
having seen the real markup. If `--find` still comes up short after this
version, please run `--dump-links /Universites-privees-Tunis.html` (or
whichever page is missing entries) and paste me the output.

Usage:
    pip install requests beautifulsoup4
    python scrape_all_tunisia_institutions.py --out data/universities_seed.json
    python scrape_all_tunisia_institutions.py --out data/universities_seed.json --find esprit
    python scrape_all_tunisia_institutions.py --dump-links /Universites-privees-Tunis.html
"""

import argparse
import json
import os
import random
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from typing import Optional
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

BASE = "https://www.universite.tn"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8,ar;q=0.7",
    # NOTE: deliberately NOT advertising "br" (Brotli) here. requests/urllib3
    # only auto-decompresses Brotli if the optional `brotli` package is
    # installed; if it's absent, the server may still send Brotli-encoded
    # bytes (since we said we accept it) and requests will hand back raw
    # compressed bytes mis-decoded as garbled text. gzip/deflate are always
    # safely auto-decompressed with no extra dependency, so we stick to those.
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Referer": BASE + "/",
}
MAX_RETRIES = 4

# Domains that can legitimately appear on a page but are never "the
# official institution website" -- excluded from the last-resort scan.
NON_OFFICIAL_DOMAINS = (
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "youtu.be", "wikipedia.org",
    "wikimedia.org", "google.com", "goo.gl", "maps.app.goo.gl",
    "universite.tn",
)

SITE_WEB_RE = re.compile(
    r"site\s*(?:web|internet)\s*:?|web\s*site\s*:?", re.IGNORECASE
)
# Fallback: catch a bare domain in plain text, e.g. "Site web : www.ihec.rnu.tn"
DOMAIN_TEXT_RE = re.compile(
    r"((?:https?://)?(?:www\.)?[a-z0-9][a-z0-9\-]*\.[a-z]{2,}(?:\.[a-z]{2,})?(?:/[^\s]*)?)",
    re.IGNORECASE,
)

# Obvious nav/menu/boilerplate link text seen on essentially every page of
# a French-language institutional directory site. Used to filter private-
# institution link candidates (see collect_private_institution_links).
NAV_TEXT_DENYLIST = {
    "accueil", "contact", "contactez-nous", "recherche", "recherche avancee",
    "recherche avancée", "actualites", "actualité", "actualités", "annuaire",
    "plan du site", "mentions legales", "mentions légales", "ministere",
    "ministère", "mesrs", "en savoir plus", "voir plus", "lire la suite",
    "detail", "détails", "haut de page", "espace etudiant", "espace étudiant",
    "connexion", "login", "universites publiques", "universités publiques",
    "universites privees", "universités privées",
    "instituts superieurs des etudes technologiques",
    "instituts supérieurs des études technologiques", "iset", "facebook",
    "twitter", "accessibilite", "accessibilité", "carte", "cartographie",
    "presentation", "présentation", "liens utiles", "partenaires", "faq",
    "tunis", "sousse", "sfax", "nabeul", "monastir", "gabes", "gabès",
    "gafsa", "kairouan",
}


# ---------------------------------------------------------------------------
# Canonical public university list. The display name here is authoritative
# and is NEVER overwritten by scraped text -- only the website is scraped.
# This is what guarantees "university of Gafsa", "university of Carthage",
# etc. always show up, correctly named, even if universite.tn's markup
# changes or a page fails to load.
# ---------------------------------------------------------------------------
CANONICAL_PUBLIC_UNIVERSITIES = [
    {"path": "/Universite-de-Tunis.html", "key": "universite de tunis",
     "display_name": "Universite de Tunis", "website": "http://www.utunis.rnu.tn"},
    {"path": "/Universite-de-Tunis-El-Manar.html", "key": "universite de tunis el manar",
     "display_name": "Universite de Tunis El Manar", "website": "https://www.utm.rnu.tn"},
    {"path": "/Universite-de-Carthage.html", "key": "universite de carthage",
     "display_name": "Universite de Carthage", "website": "https://www.ucar.rnu.tn"},
    {"path": "/Universite-de-la-Manouba.html", "key": "universite de la manouba",
     "display_name": "Universite de la Manouba", "website": "https://www.uma.rnu.tn"},
    {"path": "/Universite-de-Sfax.html", "key": "universite de sfax",
     "display_name": "Universite de Sfax", "website": "https://www.uss.rnu.tn"},
    {"path": "/Universite-de-Monastir.html", "key": "universite de monastir",
     "display_name": "Universite de Monastir", "website": "http://www.um.rnu.tn"},
    {"path": "/Universite-de-Jendouba.html", "key": "universite de jendouba",
     "display_name": "Universite de Jendouba", "website": "https://www.uj.rnu.tn"},
    {"path": "/Universite-de-Kairouan.html", "key": "universite de kairouan",
     "display_name": "Universite de Kairouan", "website": "https://www.univ-k.rnu.tn"},
    {"path": "/Universite-de-Gabes.html", "key": "universite de gabes",
     "display_name": "Universite de Gabes", "website": "https://www.univgb.rnu.tn"},
    {"path": "/Universite-Ez-Zitouna.html", "key": "universite ez-zitouna",
     "display_name": "Universite Ez-Zitouna", "website": "https://www.uz.rnu.tn"},
    {"path": "/Universite-de-Sousse.html", "key": "universite de sousse",
     "display_name": "Universite de Sousse", "website": "https://www.uso.rnu.tn"},
    {"path": "/Universite-de-Gafsa.html", "key": "universite de gafsa",
     "display_name": "Universite de Gafsa", "website": "https://www.ugaf.rnu.tn"},
    {"path": "/Universite-virtuelle.html", "key": "universite virtuelle de tunis",
     "display_name": "Universite Virtuelle de Tunis", "website": "https://www.uvt.rnu.tn"},
]

PRIVATE_CATEGORY_PAGES = [
    "/Universites-privees-Tunis.html",
    "/Universite-privee-Sousse.html",
    "/Universite-privee-Sfax.html",
    "/Universite-privee-Nabeul.html",
    "/Universite-privee-Monastir.html",
    "/Universite-privee-Gabes.html",
    "/Universite-privee-Gafsa.html",
    "/Universite-privee-Kairouan.html",
]

ISET_LISTING_PAGE = "/Instituts-Superieurs-des-Etudes-Technologiques.html"

# Pages that should never be mistaken for an "institution" link when
# generically scanning a listing page.
KNOWN_NAV_PATHS = {"/", "/index.html", "/Contact.html", "/Accueil.html"}
KNOWN_NAV_PATHS |= set(PRIVATE_CATEGORY_PAGES)
KNOWN_NAV_PATHS |= {e["path"] for e in CANONICAL_PUBLIC_UNIVERSITIES}
KNOWN_NAV_PATHS.add(ISET_LISTING_PAGE)


def _normalize(name: str) -> str:
    """Lowercase, strip accents, collapse whitespace -- used as a dedup key."""
    n = name.lower().strip()
    n = unicodedata.normalize("NFKD", n)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.replace("’", "'")
    n = re.sub(r"\s+", " ", n)
    return n


def _path_of(url: str) -> str:
    return urlsplit(url).path


# ---------------------------------------------------------------------------
# Networking: a shared session, a jittered rate limiter, a URL-level cache,
# and 403-aware retry/warm-up logic.
# ---------------------------------------------------------------------------
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


class RateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            jitter = random.uniform(0, self.min_interval * 0.5)
            sleep_for = (self.min_interval + jitter) - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last = time.monotonic()


class FetchCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[str, Optional[BeautifulSoup]] = {}

    def get(self, url):
        with self._lock:
            return self._cache.get(url, "MISS")

    def set(self, url, soup):
        with self._lock:
            self._cache[url] = soup


RATE_LIMITER: Optional[RateLimiter] = None
CACHE = FetchCache()
FAILED_URLS: set[str] = set()
FAILED_LOCK = threading.Lock()


def warm_up_session():
    """One real-browser-like GET of the homepage to pick up cookies before
    hitting any subpage. Also used to 're-warm' after a 403."""
    try:
        SESSION.get(BASE + "/", timeout=20)
    except requests.RequestException as exc:
        print(f"  ! warm-up request failed (continuing anyway): {exc}", file=sys.stderr)


def _looks_like_html(text: str) -> bool:
    """Sanity check that we actually got readable markup back, not
    mis-decoded/garbled bytes (e.g. Brotli content the client couldn't
    decompress, or a non-text error page). Cheap and permissive by design
    -- it only needs to catch the 'this is obviously not HTML' case."""
    if not text:
        return False
    head = text[:2000].lower()
    if "<html" in head or "<!doctype" in head or "<body" in head or "<a " in head or "<div" in head:
        return True
    # Fall back to a printable-character ratio check for pages with an
    # unusual head (e.g. XML declaration first).
    printable = sum(1 for c in text[:2000] if c.isprintable() and ord(c) < 0x2000)
    return (printable / max(1, len(text[:2000]))) > 0.9


def fetch(url: str) -> Optional[BeautifulSoup]:
    cached = CACHE.get(url)
    if cached != "MISS":
        return cached

    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        RATE_LIMITER.wait()
        try:
            resp = SESSION.get(url, timeout=20)
            if resp.status_code == 403:
                wait = 4.0 * attempt + random.uniform(0, 2)
                print(f"  ! attempt {attempt}/{MAX_RETRIES} got 403 for {url}; "
                      f"re-warming session and retrying in {wait:.1f}s")
                time.sleep(wait)
                warm_up_session()
                continue
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            text = resp.text
            if not _looks_like_html(text):
                last_exc = RuntimeError(
                    f"response did not look like HTML (possibly undecoded compressed "
                    f"content; Content-Encoding={resp.headers.get('Content-Encoding')!r})"
                )
                wait = 2.0 * attempt
                print(f"  ! attempt {attempt}/{MAX_RETRIES} got non-HTML/garbled content for {url}; "
                      f"retrying in {wait:.1f}s")
                time.sleep(wait)
                continue
            soup = BeautifulSoup(text, "html.parser")
            CACHE.set(url, soup)
            return soup
        except requests.RequestException as exc:
            last_exc = exc
            wait = RATE_LIMITER.min_interval * attempt * 2
            print(f"  ! attempt {attempt}/{MAX_RETRIES} failed for {url} ({exc}); retrying in {wait:.1f}s")
            time.sleep(wait)

    print(f"  !! giving up on {url}: {last_exc or '403 Forbidden after retries'}", file=sys.stderr)
    with FAILED_LOCK:
        FAILED_URLS.add(url)
    CACHE.set(url, None)
    return None


def fetch_many(urls: list[str], max_workers: int, label: str) -> dict:
    """Fetch a batch of detail-page URLs concurrently (politely), returning
    {url: soup_or_None}."""
    results = {}
    if not urls:
        return results
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch, u): u for u in urls}
        done = 0
        for fut in as_completed(futures):
            url = futures[fut]
            done += 1
            try:
                results[url] = fut.result()
            except Exception as exc:
                print(f"  !! unexpected error fetching {url}: {exc}", file=sys.stderr)
                results[url] = None
            print(f"  [{label}] {done}/{len(urls)} done", end="\r")
    print()
    return results


def clean_name(raw: str) -> str:
    name = raw.strip()
    name = name.split(":")[0].strip()
    name = re.sub(r"\s*[|\-–]\s*universit[ée]\.tn.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name)
    return name.strip(" -:")


def extract_website_from_detail_page(soup: Optional[BeautifulSoup]) -> Optional[str]:
    if soup is None:
        return None

    def is_official(href: str) -> bool:
        if not href.startswith("http"):
            return False
        return not any(bad in href.lower() for bad in NON_OFFICIAL_DOMAINS)

    label_node = soup.find(string=SITE_WEB_RE)
    if label_node:
        container = label_node.parent
        for el in container.find_all_next("a", limit=5):
            href = el.get("href", "")
            if is_official(href):
                return href.strip()

        remainder = str(label_node)
        text_after = SITE_WEB_RE.split(remainder, maxsplit=1)
        candidate_text = text_after[-1] if text_after else ""
        siblings_text = " ".join(
            s for s in label_node.find_all_next(string=True, limit=3)
        )
        for chunk in (candidate_text, siblings_text):
            m = DOMAIN_TEXT_RE.search(chunk)
            if m:
                candidate = m.group(1).strip().rstrip(".,;")
                if "universite.tn" not in candidate.lower():
                    if not candidate.startswith("http"):
                        candidate = "http://" + candidate
                    return candidate

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if is_official(href):
            return href.strip()

    return None


@dataclass
class University:
    name: str
    type: str  # "PUBLIC" or "PRIVATE"
    website: Optional[str]


def scrape_public_universities() -> list[University]:
    results = []
    for entry in CANONICAL_PUBLIC_UNIVERSITIES:
        url = urljoin(BASE, entry["path"])
        print(f"[public] fetching {url}")
        soup = fetch(url)
        website = extract_website_from_detail_page(soup) or entry["website"]
        results.append(University(name=entry["display_name"], type="PUBLIC", website=website))
    return results


def collect_links_under_page(page_url: str, page_soup: BeautifulSoup) -> list[tuple[str, str]]:
    base_slug = page_url.rstrip("/")
    if base_slug.endswith(".html"):
        base_slug = base_slug[: -len(".html")]
    prefix = base_slug + "/"

    links = []
    seen = set()
    for a in page_soup.find_all("a", href=True):
        full_url = urljoin(page_url, a["href"])
        if not full_url.startswith(prefix) or not full_url.endswith(".html"):
            continue
        name = clean_name(a.get_text())
        if not name or full_url in seen:
            continue
        seen.add(full_url)
        links.append((name, full_url))
    return links


def scrape_constituent_institutions(max_workers: int) -> list[University]:
    results = []
    for entry in CANONICAL_PUBLIC_UNIVERSITIES:
        url = urljoin(BASE, entry["path"])
        print(f"\n[constituents] scanning {url} for constituent institutions")
        soup = fetch(url)
        if soup is None:
            print(f"  !! could not load {url} -- constituent institutions under it will be MISSING")
            continue

        sub_links = collect_links_under_page(url, soup)
        print(f"  -> found {len(sub_links)} constituent institution link(s)")
        if not sub_links:
            continue

        detail_urls = [u for _, u in sub_links]
        fetched = fetch_many(detail_urls, max_workers, label=entry["display_name"])

        for name, detail_url in sub_links:
            detail_soup = fetched.get(detail_url)
            if detail_soup is not None:
                h1 = detail_soup.find("h1")
                if h1:
                    h1_name = clean_name(h1.get_text())
                    if h1_name and len(h1_name) >= len(name) * 0.6:
                        name = h1_name
            website = extract_website_from_detail_page(detail_soup)
            results.append(University(name=name, type="PUBLIC", website=website))

    return results


def scrape_iset_listing(max_workers: int) -> list[University]:
    url = urljoin(BASE, ISET_LISTING_PAGE)
    print(f"\n[ISET] fetching {url}")
    soup = fetch(url)
    if soup is None:
        print("  !! could not load ISET listing page -- ISETs will be MISSING")
        return []

    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        full_url = urljoin(url, a["href"])
        if "/Instituts-Superieurs-des-Etudes-Technologiques/" not in full_url or not full_url.endswith(".html"):
            continue
        name = clean_name(a.get_text())
        if not name or full_url in seen:
            continue
        seen.add(full_url)
        links.append((name, full_url))

    print(f"  -> found {len(links)} ISET link(s)")
    fetched = fetch_many([u for _, u in links], max_workers, label="ISET")

    results = []
    for name, detail_url in links:
        detail_soup = fetched.get(detail_url)
        website = extract_website_from_detail_page(detail_soup)
        results.append(University(name=name, type="PUBLIC", website=website))
        print(f"  - {name}: {website or 'no website found'}")
    return results


def collect_private_institution_links() -> list[tuple[str, str]]:
    """Pattern-agnostic: gather every internal link from all 8 governorate
    pages, then separate 'institution' from 'nav boilerplate' using
    cross-page frequency (a link repeated on most/all pages is nav; one
    seen on only 1-2 pages is a specific institution) plus a small denylist
    of obvious nav text. This makes no assumption about the institution
    detail pages' URL shape, which is what broke the previous version."""
    # raw[url] = {"name": best_name_seen, "pages": set_of_page_paths_it_appeared_on}
    raw: dict[str, dict] = {}

    for path in PRIVATE_CATEGORY_PAGES:
        url = urljoin(BASE, path)
        print(f"[private-category] fetching {url}")
        soup = fetch(url)
        if soup is None:
            print(f"  !! could not load category page {url} after retries -- "
                  f"institutions in this governorate will be MISSING.")
            continue

        page_found = set()
        for a in soup.find_all("a", href=True):
            full_url = urljoin(url, a["href"])
            full_url = full_url.split("#")[0]
            p = _path_of(full_url)

            if not full_url.startswith(BASE):
                continue  # external link, not an institution page on this site
            if not full_url.endswith(".html"):
                continue
            if p in KNOWN_NAV_PATHS:
                continue

            name = clean_name(a.get_text())
            if not name or len(name) < 4:
                continue
            if _normalize(name) in NAV_TEXT_DENYLIST:
                continue

            page_found.add(full_url)
            entry = raw.setdefault(full_url, {"name": name, "pages": set()})
            if len(name) > len(entry["name"]):
                entry["name"] = name

        for u in page_found:
            raw[u]["pages"].add(path)

        print(f"  -> {len(page_found)} candidate link(s) on this page (pre-filter)")

    # Cross-page frequency filter: something linked from more than 2 of the
    # 8 governorate pages is almost certainly shared navigation, not an
    # institution that legitimately belongs to only one (or occasionally
    # two, for jointly-listed institutions) governorate.
    NAV_FREQUENCY_THRESHOLD = 2
    links = []
    for url, info in raw.items():
        if len(info["pages"]) > NAV_FREQUENCY_THRESHOLD:
            continue
        links.append((info["name"], url))

    print(f"\n[private] {len(raw)} raw candidate links -> {len(links)} after nav-frequency filtering\n")
    return links


def scrape_private_universities(max_workers: int) -> list[University]:
    links = collect_private_institution_links()
    print(f"[private] total institutions discovered across all governorates: {len(links)}\n")

    fetched = fetch_many([u for _, u in links], max_workers, label="private")

    results = []
    for name, url in links:
        website = extract_website_from_detail_page(fetched.get(url))
        results.append(University(name=name, type="PRIVATE", website=website))
    return results


def merge_and_dedupe(all_unis: list[University]) -> list[University]:
    by_name: dict[str, University] = {}
    for u in all_unis:
        key = _normalize(u.name)
        if key not in by_name:
            by_name[key] = u
        elif not by_name[key].website and u.website:
            by_name[key] = University(name=by_name[key].name, type=by_name[key].type, website=u.website)
    return list(by_name.values())


def save_json(unis: list[University], path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(u) for u in unis], f, ensure_ascii=False, indent=2)


def checkpoint(all_so_far: list[University], out_path: str, step_label: str):
    partial_path = out_path + ".partial.json"
    save_json(merge_and_dedupe(all_so_far), partial_path)
    print(f"  [checkpoint after {step_label}] saved {len(all_so_far)} raw records -> {partial_path}")


def find_in_dataset(unis: list[University], query: str) -> list[University]:
    q = _normalize(query)
    return [u for u in unis if q in _normalize(u.name)]


def dump_links(path: str):
    """Diagnostic mode: fetch a single page, print every link + text found,
    and save the raw HTML next to the script so it can be inspected or
    shared for further debugging."""
    global RATE_LIMITER
    RATE_LIMITER = RateLimiter(0.5)
    warm_up_session()
    url = urljoin(BASE, path)
    print(f"Fetching {url} ...\n")
    soup = fetch(url)
    if soup is None:
        print("Could not fetch that page (see errors above).")
        return

    raw_html_path = "debug_" + re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_") + ".html"
    with open(raw_html_path, "w", encoding="utf-8") as f:
        f.write(str(soup))
    print(f"Saved raw HTML to {raw_html_path}\n")

    h1 = soup.find("h1")
    print(f"<h1>: {h1.get_text().strip() if h1 else '(none found)'}\n")

    print(f"All <a href> links found ({len(soup.find_all('a', href=True))} total):")
    for a in soup.find_all("a", href=True):
        full_url = urljoin(url, a["href"])
        text = clean_name(a.get_text()) or "(empty text)"
        print(f"  {text!r:60s} -> {full_url}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="data/universities_seed.json")
    parser.add_argument("--skip-private", action="store_true")
    parser.add_argument("--skip-constituents", action="store_true",
                         help="Only the 14 top-level public universities, skip crawling their sub-institutions.")
    parser.add_argument("--skip-iset", action="store_true")
    parser.add_argument("--delay", type=float, default=0.8,
                         help="Minimum seconds between requests, shared across all workers (default: 0.8).")
    parser.add_argument("--workers", type=int, default=3,
                         help="Concurrent workers for fetching detail pages (default: 3).")
    parser.add_argument("--find", default=None,
                         help="After scraping, print institutions whose name contains this "
                              "substring (case/accent-insensitive), e.g. --find esprit")
    parser.add_argument("--dump-links", metavar="PATH", default=None,
                         help="Diagnostic mode: fetch one page (e.g. /Universites-privees-Tunis.html), "
                              "print every link + text on it, save the raw HTML, and exit. "
                              "Use this if a section still comes back thin -- send me the output.")
    args = parser.parse_args()

    global RATE_LIMITER
    RATE_LIMITER = RateLimiter(args.delay)

    if args.dump_links:
        dump_links(args.dump_links)
        return

    warm_up_session()

    all_unis: list[University] = []

    try:
        print("=" * 70)
        print("STEP 1/4: public universities (14 expected)")
        print("=" * 70)
        public_unis = scrape_public_universities()
        print(f"\n-> collected {len(public_unis)} public universities\n")
        all_unis.extend(public_unis)
        checkpoint(all_unis, args.out, "STEP 1 (public universities)")

        if not args.skip_constituents:
            print("=" * 70)
            print("STEP 2/4: constituent institutions under each public university")
            print("=" * 70)
            constituents = scrape_constituent_institutions(args.workers)
            print(f"\n-> collected {len(constituents)} constituent institutions\n")
            all_unis.extend(constituents)
            checkpoint(all_unis, args.out, "STEP 2 (constituents)")

        if not args.skip_iset:
            print("=" * 70)
            print("STEP 3/4: ISETs (Instituts Superieurs des Etudes Technologiques)")
            print("=" * 70)
            isets = scrape_iset_listing(args.workers)
            print(f"\n-> collected {len(isets)} ISETs\n")
            all_unis.extend(isets)
            checkpoint(all_unis, args.out, "STEP 3 (ISETs)")

        if not args.skip_private:
            print("=" * 70)
            print("STEP 4/4: private institutions")
            print("=" * 70)
            private_unis = scrape_private_universities(args.workers)
            all_unis.extend(private_unis)
            checkpoint(all_unis, args.out, "STEP 4 (private)")

    except KeyboardInterrupt:
        print("\n!! interrupted by user -- saving whatever was collected so far.", file=sys.stderr)
    except Exception as exc:
        print(f"\n!! unexpected error: {exc} -- saving whatever was collected so far.", file=sys.stderr)
        raise
    finally:
        final = merge_and_dedupe(all_unis)
        save_json(final, args.out)

        n_public = sum(1 for u in final if u.type == "PUBLIC")
        n_private = sum(1 for u in final if u.type == "PRIVATE")
        n_with_site = sum(1 for u in final if u.website)
        print("\n" + "=" * 70)
        print(f"DONE. Wrote {len(final)} institutions to {args.out}")
        print(f"  PUBLIC:  {n_public}  (14 universities + their constituents + ISETs)")
        print(f"  PRIVATE: {n_private}")
        print(f"  {n_with_site} have a website, {len(final) - n_with_site} are null and need manual lookup")
        print("=" * 70)

        final_keys = {_normalize(u.name) for u in final}
        missing = [e["display_name"] for e in CANONICAL_PUBLIC_UNIVERSITIES
                   if e["key"] not in final_keys]
        if missing:
            print(f"  !! WARNING: these canonical public universities are missing from output: {missing}",
                  file=sys.stderr)

        if FAILED_URLS:
            failed_path = args.out + ".failed_urls.txt"
            with open(failed_path, "w", encoding="utf-8") as f:
                f.write("\n".join(sorted(FAILED_URLS)))
            print(f"  {len(FAILED_URLS)} URL(s) failed after all retries -- logged to {failed_path}")

        if args.find:
            matches = find_in_dataset(final, args.find)
            print(f"\n--find '{args.find}' matched {len(matches)} institution(s):")
            for u in matches:
                print(f"  - [{u.type}] {u.name} -> {u.website or '(no website found)'}")


if __name__ == "__main__":
    main()