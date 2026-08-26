"""
Enrichment step: for public Tunisian universities, discover the constituent
faculties / schools / institutes ("sub-institutions") listed under them --
e.g. Universite de Carthage includes ENICarthage, INSAT, Sup'Com, etc.

Source: universite.tn, a public annuaire that groups Tunisian public
universities' constituent establishments on one page per university. This
is *not* the university's own website -- it's a third-party directory --
so treat "no sub-institutions found" as "this isn't a public university with
a directory page" (e.g. most private establishments), not as a scraping
failure.

Usage:
    from scraper.sources.subinstitutions import find_sub_institutions

    subs = find_sub_institutions("Universite de Carthage")
    # -> [RawSubInstitution(name="Ecole Polytechnique de Tunisie", website=...), ...]
"""
import re
import time
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.models import RawSubInstitution

BASE = "https://www.universite.tn"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
REQUEST_DELAY_SECONDS = 1.0
SITE_WEB_RE = re.compile(r"Site\s*web\s*:?\s*", re.IGNORECASE)

# Type-label keywords we can pull out of a sub-institution's own name, purely
# as a best-effort hint -- not authoritative.
TYPE_LABEL_PATTERNS = [
    (re.compile(r"\bfacult[ée]\b", re.IGNORECASE), "Faculte"),
    (re.compile(r"\b[ée]cole\b", re.IGNORECASE), "Ecole"),
    (re.compile(r"\binstitut\b", re.IGNORECASE), "Institut"),
    (re.compile(r"\bISET\b", re.IGNORECASE), "ISET"),
]

# Maps a normalized university name -> its directory page on universite.tn.
# Only the 13 traditional public universities + UVT have one of these pages;
# private institutions and standalone establishments don't, and that's
# expected -- find_sub_institutions() will just return [] for them.
_DIRECTORY_PAGES = {
    "universite de tunis": "/Universite-de-Tunis.html",
    "universite de tunis el manar": "/Universite-de-Tunis-El-Manar.html",
    "universite de carthage": "/Universite-de-Carthage.html",
    "universite de la manouba": "/Universite-de-la-Manouba.html",
    "universite de sfax": "/Universite-de-Sfax.html",
    "universite de monastir": "/Universite-de-Monastir.html",
    "universite de jendouba": "/Universite-de-Jendouba.html",
    "universite de kairouan": "/Universite-de-Kairouan.html",
    "universite de gabes": "/Universite-de-Gabes.html",
    "universite ez-zitouna": "/Universite-Ez-Zitouna.html",
    "universite de sousse": "/Universite-de-Sousse.html",
    "universite de gafsa": "/Universite-de-Gafsa.html",
    "universite virtuelle de tunis": "/Universite-virtuelle.html",
    "universite virtuelle": "/Universite-virtuelle.html",
}


def _normalize(name: str) -> str:
    n = name.lower().strip()
    for a, b in [("é", "e"), ("è", "e"), ("à", "a"), ("ô", "o"), ("â", "a"), ("’", "'")]:
        n = n.replace(a, b)
    n = re.sub(r"\s+", " ", n)
    return n


def _fetch(url: str) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or resp.encoding
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as exc:
        print(f"    [sub_institutions] fetch failed for {url}: {exc}")
        return None
    finally:
        time.sleep(REQUEST_DELAY_SECONDS)


def _clean_name(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip())


def _guess_type_label(name: str) -> Optional[str]:
    for pattern, label in TYPE_LABEL_PATTERNS:
        if pattern.search(name):
            return label
    return None


def _extract_website(soup: Optional[BeautifulSoup]) -> Optional[str]:
    """Look for the 'Site web : <link>' field universite.tn publishes on
    most of its establishment detail pages."""
    if soup is None:
        return None
    node = soup.find(string=SITE_WEB_RE)
    if node is not None:
        container = node.parent
        for el in container.find_all_next("a", limit=3):
            href = el.get("href", "")
            if href.startswith("http") and "universite.tn" not in href:
                return href.strip()
    # Fallback: first external (non-universite.tn, non-facebook) link on the page.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") and "universite.tn" not in href and "facebook.com" not in href:
            return href.strip()
    return None


def find_sub_institutions(university_name: str, fetch_websites: bool = True) -> list[RawSubInstitution]:
    """
    Given a public university's name (as it appears in your seed file),
    returns every constituent faculty/school/institute universite.tn lists
    under it, with its official website when discoverable.

    fetch_websites=True visits each sub-institution's own detail page on
    universite.tn (one extra request per sub-institution) to try to pull its
    "Site web :" field -- this is what lets ENICarthage/INSAT/etc. come back
    with a real website instead of just a name. Set to False for a much
    faster names-only pass (e.g. while iterating on this module).

    Returns [] for anything that isn't one of the 13 public universities +
    UVT -- that's expected, not a failure, since private establishments
    don't have a directory page listing children.
    """
    norm = _normalize(university_name)
    path = _DIRECTORY_PAGES.get(norm)
    if not path:
        return []

    url = urljoin(BASE, path)
    print(f"  [sub_institutions] looking up constituent schools for '{university_name}'")
    soup = _fetch(url)
    if soup is None:
        return []

    # Constituent-institution links on a university's directory page all
    # live under that same path as a "folder", e.g. for
    # "/Universite-de-Carthage.html" the children are
    # "/Universite-de-Carthage/Ecole-Polytechnique-de-Tunisie.html" etc.
    prefix = path.replace(".html", "") + "/"
    seen = set()
    child_links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if prefix not in href or not href.endswith(".html"):
            continue
        name = _clean_name(a.get_text())
        if not name:
            continue
        full_url = urljoin(BASE, href)
        if full_url in seen:
            continue
        seen.add(full_url)
        child_links.append((name, full_url))

    if not child_links:
        print(f"    !! no constituent institutions found on {url} -- "
              f"page structure may differ from what this parser expects")

    results = []
    for name, link in child_links:
        website = None
        if fetch_websites:
            sub_soup = _fetch(link)
            website = _extract_website(sub_soup)
        results.append(
            RawSubInstitution(name=name, website=website, type_label=_guess_type_label(name))
        )

    print(f"    -> found {len(results)} constituent institutions")
    return results