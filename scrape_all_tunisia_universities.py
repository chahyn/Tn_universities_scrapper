#!/usr/bin/env python3
"""
scrape_all_tunisia_universities.py

Builds a complete JSON list of Tunisian universities / higher-ed establishments
(public + private) in the format:

    [{"name": ..., "type": "PUBLIC" | "PRIVATE", "website": ... or null}, ...]

Data source: https://www.universite.tn (public annuaire of Tunisian higher-ed
establishments). This site lists:
  - The 13 traditional public universities + Universite Virtuelle de Tunis (UVT)
  - Private establishments grouped by governorate (Tunis, Nabeul, Sousse, Sfax,
    Monastir, Gabes, Gafsa, Kairouan)

For each entry we visit its detail page and try to pull the "Site web :" field
that universite.tn publishes for most establishments. Some private
institutions don't list a website there -- in that case "website" is null and
you'll need to fill it manually or find it via a search engine.

Usage:
    pip install requests beautifulsoup4
    python scrape_all_tunisia_universities.py --out output/raw/universities_full.json

Notes:
  - This is a *courteous* scraper: it sleeps briefly between requests and
    sends a normal browser User-Agent. Don't crank up concurrency against a
    small third-party site.
  - universite.tn's HTML is a bit messy (old-school site), so the parsing
    below is intentionally defensive/tolerant rather than assuming a rigid
    structure.
"""

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.universite.tn"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": BASE + "/",
}
REQUEST_DELAY_SECONDS = 1.2  # be polite
MAX_RETRIES = 3

# universite.tn's own page for some universities carries outdated info (it's
# an old, manually-maintained directory). Where we KNOW the published site is
# wrong/stale, override it here instead of trusting the scrape blindly.
KNOWN_GOOD_OVERRIDES = {
    "universite de sousse": "https://www.uso.rnu.tn",
}

# Hard fallback for the 14 public institutions, verified manually. If the
# live scrape fails for any of these (network issue, page reshuffled, site
# blocks us, etc.) we still guarantee they appear in the final output instead
# of silently vanishing.
PUBLIC_FALLBACK = {
    "universite de tunis": "http://www.utunis.rnu.tn",
    "universite de tunis el manar": "https://www.utm.rnu.tn",
    "universite de carthage": "https://www.ucar.rnu.tn",
    "universite de la manouba": "https://www.uma.rnu.tn",
    "universite de sfax": "https://www.uss.rnu.tn",
    "universite de monastir": "http://www.um.rnu.tn",
    "universite de jendouba": "https://www.uj.rnu.tn",
    "universite de kairouan": "https://www.univ-k.rnu.tn",
    "universite de gabes": "https://www.univgb.rnu.tn",
    "universite ez-zitouna": "https://www.uz.rnu.tn",
    "universite de sousse": "https://www.uso.rnu.tn",
    "universite de gafsa": "https://www.ugaf.rnu.tn",
    "universite virtuelle de tunis": "https://www.uvt.rnu.tn",
    "universite virtuelle": "https://www.uvt.rnu.tn",
    "universite centrale": "https://www.universitecentrale.net",
}


def _normalize(name: str) -> str:
    """Lowercase, strip accents-ish, collapse whitespace for dict lookups."""
    n = name.lower().strip()
    for a, b in [("é", "e"), ("è", "e"), ("à", "a"), ("ô", "o"), ("â", "a"), ("’", "'")]:
        n = n.replace(a, b)
    n = re.sub(r"\s+", " ", n)
    return n


SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# The 13 traditional public universities + the virtual university, as listed
# on universite.tn's homepage.
PUBLIC_UNIVERSITY_PAGES = [
    "/Universite-de-Tunis.html",
    "/Universite-de-Tunis-El-Manar.html",
    "/Universite-de-Carthage.html",
    "/Universite-de-la-Manouba.html",
    "/Universite-de-Sfax.html",
    "/Universite-de-Monastir.html",
    "/Universite-de-Jendouba.html",
    "/Universite-de-Kairouan.html",
    "/Universite-de-Gabes.html",
    "/Universite-Ez-Zitouna.html",
    "/Universite-de-Sousse.html",
    "/Universite-de-Gafsa.html",
    "/Universite-virtuelle.html",
]

# Private establishments are grouped by governorate on these listing pages.
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

SITE_WEB_RE = re.compile(r"Site\s*web\s*:?\s*", re.IGNORECASE)


@dataclass
class University:
    name: str
    type: str  # "PUBLIC" or "PRIVATE"
    website: Optional[str]


def fetch(url: str) -> Optional[BeautifulSoup]:
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = SESSION.get(url, timeout=20)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            return BeautifulSoup(resp.text, "html.parser")
        except requests.RequestException as exc:
            last_exc = exc
            wait = REQUEST_DELAY_SECONDS * attempt * 2
            print(f"  ! attempt {attempt}/{MAX_RETRIES} failed for {url} ({exc}); retrying in {wait:.1f}s")
            time.sleep(wait)
        finally:
            time.sleep(REQUEST_DELAY_SECONDS)
    print(f"  !! giving up on {url}: {last_exc}", file=sys.stderr)
    return None


def clean_name(raw: str) -> str:
    name = raw.strip()
    # Titles on universite.tn often look like "Universite de X : Liste des ..."
    name = name.split(":")[0].strip()
    name = re.sub(r"\s+", " ", name)
    return name


def extract_website_from_detail_page(soup: BeautifulSoup) -> Optional[str]:
    """Look for the 'Site web : <link>' field used across universite.tn pages."""
    page_text_node = soup.find(string=SITE_WEB_RE)
    if page_text_node:
        # The link is usually the next <a> after this text node.
        container = page_text_node.parent
        link = None
        # search siblings/following elements for the first <a>
        for el in container.find_all_next("a", limit=3):
            href = el.get("href", "")
            if href.startswith("http") and "universite.tn" not in href:
                link = href
                break
        if link:
            return link.strip()

    # Fallback: any external (non universite.tn) link with an .tn/.com/.net/.org
    # domain near the top of the page content area is likely the official site.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") and "universite.tn" not in href and "facebook.com" not in href:
            return href.strip()
    return None


def scrape_public_universities() -> list[University]:
    results = []
    seen_normalized = set()

    for path in PUBLIC_UNIVERSITY_PAGES:
        url = urljoin(BASE, path)
        print(f"[public] fetching {url}")
        soup = fetch(url)
        name = None
        website = None
        if soup is not None:
            h1 = soup.find("h1")
            raw_name = h1.get_text() if h1 else path
            name = clean_name(raw_name.replace("Universités & Facultés en Tunisie", ""))
            name = name.replace("Universités Tunisiennes", "").strip(" :")
            website = extract_website_from_detail_page(soup)

        # Derive a fallback name from the URL slug if the page failed entirely.
        if not name:
            name = path.strip("/").replace(".html", "").replace("-", " ")

        norm = _normalize(name)
        # Known-stale scrape result -> trust our verified override instead.
        if norm in KNOWN_GOOD_OVERRIDES:
            website = KNOWN_GOOD_OVERRIDES[norm]
        # Nothing scraped at all -> fall back to the verified hardcoded value
        # so this university still shows up with a working link.
        if not website and norm in PUBLIC_FALLBACK:
            website = PUBLIC_FALLBACK[norm]

        seen_normalized.add(norm)
        results.append(University(name=name, type="PUBLIC", website=website))

    # Safety net: if any of the 14 known public institutions never got
    # matched above (e.g. universite.tn reshuffled its URLs), add it from the
    # fallback table so it isn't silently missing from the output.
    display_names = {
        "universite de tunis": "Universite de Tunis",
        "universite de tunis el manar": "Universite de Tunis El Manar",
        "universite de carthage": "Universite de Carthage",
        "universite de la manouba": "Universite de la Manouba",
        "universite de sfax": "Universite de Sfax",
        "universite de monastir": "Universite de Monastir",
        "universite de jendouba": "Universite de Jendouba",
        "universite de kairouan": "Universite de Kairouan",
        "universite de gabes": "Universite de Gabes",
        "universite ez-zitouna": "Universite Ez-Zitouna",
        "universite de sousse": "Universite de Sousse",
        "universite de gafsa": "Universite de Gafsa",
        "universite virtuelle de tunis": "Universite Virtuelle de Tunis",
    }
    for norm, website in PUBLIC_FALLBACK.items():
        if norm in ("universite virtuelle", "universite centrale"):
            continue  # aliases / handled elsewhere
        if norm not in seen_normalized:
            print(f"[public] safety net: adding missing entry '{norm}'")
            results.append(University(name=display_names.get(norm, norm.title()), type="PUBLIC", website=website))

    return results


def collect_private_institution_links() -> list[tuple[str, str]]:
    """Returns list of (name, detail_url) across all private category pages."""
    links = []
    seen = set()
    for path in PRIVATE_CATEGORY_PAGES:
        url = urljoin(BASE, path)
        print(f"[private-category] fetching {url}")
        soup = fetch(url)
        if soup is None:
            print(f"  !! could not load category page {url} after retries -- "
                  f"institutions in this governorate will be MISSING. Re-run "
                  f"later or check your network/proxy.")
            continue

        found_here = 0
        # Institution links live inside the big listing table on each category
        # page. They all point to /Universite-privee-<City>/<slug>.html
        # (note: the Tunis category page itself is a plural exception:
        # "Universites-privees-Tunis.html", but institution links under it
        # still use the singular "/Universite-privee-Tunis/..." form.)
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/Universite-privee-" not in href or not href.endswith(".html"):
                continue
            # Skip links back to a category page itself, e.g.
            # ".../Universite-privee-Sousse.html" (no sub-path after the city).
            if re.search(r"/Universite-privee-[A-Za-z]+\.html$", href):
                continue
            full_url = urljoin(BASE, href)
            name = clean_name(a.get_text())
            if not name:
                continue
            if full_url not in seen:
                seen.add(full_url)
                links.append((name, full_url))
                found_here += 1

        print(f"  -> found {found_here} institution links on this page")
        if found_here == 0:
            print("  !! zero links found -- the page structure may have "
                  "changed, or the request was served a blocked/CAPTCHA page. "
                  "Consider printing str(soup)[:500] here to inspect it.")

    return links


def scrape_private_universities() -> list[University]:
    results = []
    links = collect_private_institution_links()
    print(f"\n[private] total institutions discovered across all governorates: {len(links)}\n")

    for i, (name, url) in enumerate(links, start=1):
        print(f"[private] ({i}/{len(links)}) fetching {url}")
        soup = fetch(url)
        website = extract_website_from_detail_page(soup) if soup else None
        results.append(University(name=name, type="PRIVATE", website=website))

    n_missing_site = sum(1 for u in results if not u.website)
    if n_missing_site:
        print(f"[private] note: {n_missing_site}/{len(results)} private "
              f"institutions had no 'Site web' field on universite.tn -- "
              f"their website is null and needs manual lookup.")

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="output/raw/universities_full.json",
        help="Path to write the combined JSON file to.",
    )
    parser.add_argument(
        "--skip-private",
        action="store_true",
        help="Only scrape the 14 public universities (fast, ~15 requests).",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("STEP 1/2: public universities (14 expected)")
    print("=" * 70)
    public_unis = scrape_public_universities()
    print(f"\n-> collected {len(public_unis)} public universities "
          f"({'OK, matches expected 14' if len(public_unis) == 14 else 'MISMATCH -- check log above'})\n")

    private_unis: list[University] = []
    if not args.skip_private:
        print("=" * 70)
        print("STEP 2/2: private institutions (expect ~80+, varies year to year)")
        print("=" * 70)
        private_unis = scrape_private_universities()

    all_unis = public_unis + private_unis

    # De-dupe by normalized name, keeping the first (public) occurrence and
    # preferring an entry that actually has a website over one that doesn't.
    by_name: dict[str, University] = {}
    for u in all_unis:
        key = _normalize(u.name)
        if key not in by_name or (not by_name[key].website and u.website):
            by_name[key] = u
    all_unis = list(by_name.values())

    import os

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump([asdict(u) for u in all_unis], f, ensure_ascii=False, indent=2)

    n_public = sum(1 for u in all_unis if u.type == "PUBLIC")
    n_private = sum(1 for u in all_unis if u.type == "PRIVATE")
    n_with_site = sum(1 for u in all_unis if u.website)
    print("\n" + "=" * 70)
    print(f"DONE. Wrote {len(all_unis)} institutions to {args.out}")
    print(f"  PUBLIC:  {n_public} (expected 14)")
    print(f"  PRIVATE: {n_private} (expected ~80+; if this is 0, the private "
          f"crawl failed above -- scroll up for '!!' warnings)")
    print(f"  {n_with_site} have a website filled in, {len(all_unis) - n_with_site} are null and need manual lookup")
    print("=" * 70)


if __name__ == "__main__":
    main()