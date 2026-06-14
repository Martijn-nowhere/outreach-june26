"""
Stage 2: Scan CSR/MVO reports for each company.
For each company, finds their CSR report URL, downloads/parses it,
then uses free keyword matching to detect education, sustainability,
and plastic/waste mentions. No AI API needed — 100% free.
Saves results to data/csr_analysis.csv
"""
import sys
from typing import Optional, Tuple, List
import csv
import json
import time
import logging
import re
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

try:
    import importlib as _il
    _il.import_module("pdfplumber")
    HAS_PDFPLUMBER = True
    del _il
except BaseException:
    HAS_PDFPLUMBER = False

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

CSR_ANALYSIS_FIELDS = [
    "company_name",
    "csr_url",
    "mentions_education",
    "mentions_sustainability",
    "mentions_plastic_waste",
    "mentions_community",
    "best_angle",
    "angle_label",
    "relevance_score",
    "key_quotes",
    "analysis_summary",
]

MOCK_ANALYSIS = {
    "mentions_education": True,
    "mentions_sustainability": True,
    "mentions_plastic_waste": False,
    "mentions_community": False,
    "relevance_score": 7,
    "key_quotes": "Wij investeren in de opleiding van medewerkers op het gebied van duurzaamheid.",
    "analysis_summary": "[DRY-RUN] Mock analysis - company appears relevant based on seed data.",
}


# ---------------------------------------------------------------------------
# URL discovery
# ---------------------------------------------------------------------------

def get_base_url(website: str) -> str:
    """Return scheme + netloc from a website URL."""
    parsed = urlparse(website)
    return f"{parsed.scheme}://{parsed.netloc}"


MAINTENANCE_SIGNALS = [
    "maintenance", "onderhoud", "we'll be back", "we zijn zo terug",
    "temporarily unavailable", "tijdelijk niet beschikbaar",
    "coming soon", "under construction", "site is down",
]


def try_url(url: str, session: requests.Session, timeout: int = 8) -> Optional[requests.Response]:
    """Try fetching a URL, return Response or None. Skips maintenance/error pages."""
    try:
        resp = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 500:
            # Reject maintenance/down pages
            text_lower = resp.text[:2000].lower()
            if any(signal in text_lower for signal in MAINTENANCE_SIGNALS):
                log.debug("  Skipping maintenance/down page: %s", url)
                return None
            return resp
    except Exception:
        pass
    return None


def find_csr_url(company: dict, session: requests.Session) -> Optional[str]:
    """
    Find the CSR/sustainability page for a company.
    Strategy:
      1. Try common URL patterns
      2. Scan homepage links (nav, footer) for sustainability keywords
      3. Fall back to homepage itself if it contains sustainability content
    """
    base = get_base_url(company["website"])
    homepage_url = company["website"]

    csr_keywords = [
        "sustainability", "duurzaamheid", "csr", "mvo", "verantwoord",
        "impact", "responsibility", "rapport", "report", "milieu",
        "circulair", "maatschappelijk", "planet", "klimaat",
    ]

    # 1. Try URL patterns on the primary domain first, then .com/.co.uk variants
    alt_bases = []
    if base.endswith(".nl"):
        stem = base[:-3]
        alt_bases += [stem + ".com", stem + "group.com", stem + ".co.uk", stem + "group.co.uk"]

    # Primary domain patterns first, then alternates (so dirk.nl is tried before dirk.com)
    primary_candidates = [p.format(base=base) for p in config.CSR_URL_PATTERNS]
    alt_candidates = [p.format(base=b) for b in alt_bases for p in config.CSR_URL_PATTERNS]
    candidates = primary_candidates + alt_candidates

    for url in candidates:
        resp = try_url(url, session)
        if resp:
            log.info("  Found CSR page via pattern: %s", url)
            return url
        time.sleep(0.2)

    # 2. Fetch homepage and scan ALL links for CSR keywords
    log.debug("  Scanning homepage links for CSR page...")
    try:
        resp = try_url(homepage_url, session, timeout=10)
        if resp:
            soup = BeautifulSoup(resp.text, "html.parser")

            # Collect all internal links with sustainability keywords in href or text
            candidates_from_page = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                href_lower = href.lower()
                text_lower = a.get_text().lower().strip()

                if any(kw in href_lower or kw in text_lower for kw in csr_keywords):
                    full_url = urljoin(homepage_url, href)
                    # Only follow internal links
                    if get_base_url(full_url) == base:
                        candidates_from_page.append(full_url)

            # Try each candidate link
            for url in candidates_from_page[:5]:  # limit to first 5 matches
                r = try_url(url, session)
                if r:
                    log.info("  Found CSR link via homepage scan: %s", url)
                    return url
                time.sleep(0.3)

            # 3. No dedicated CSR page found — use homepage itself if it has content
            homepage_text = extract_text_from_html(resp)
            homepage_lower = homepage_text.lower()
            if any(kw in homepage_lower for kw in csr_keywords):
                log.info("  No dedicated CSR page — using homepage (has sustainability content)")
                return homepage_url

    except Exception as e:
        log.debug("  Homepage scan failed: %s", e)

    # 4. Google search fallback: "[Company] sustainability report" / "mvo rapport"
    name = company["company_name"]
    log.info("  Trying Google search fallback for %s", name)
    google_result = _google_csr_search(name, session)
    if google_result:
        log.info("  Found via Google: %s", google_result)
        return google_result

    log.warning("  No CSR page found for %s", name)
    return None


def _google_csr_search(company_name: str, session: requests.Session) -> Optional[str]:
    """
    Search for a company's CSR/sustainability page.
    Uses Apify Google Search if key is available, falls back to direct Google scraping.
    """
    import urllib.parse
    query = f'"{company_name}" duurzaamheid OR MVO OR sustainability OR "mvo rapport"'

    # Apify Google Search (no rate limiting)
    if config.APIFY_API_KEY:
        try:
            run_resp = session.post(
                "https://api.apify.com/v2/acts/apify~google-search-scraper/runs",
                json={"queries": query, "maxPagesPerQuery": 1, "resultsPerPage": 5,
                      "languageCode": "nl", "countryCode": "nl"},
                headers={"Authorization": f"Bearer {config.APIFY_API_KEY}"},
                timeout=30,
            )
            if run_resp.status_code in (200, 201):
                run_id = run_resp.json().get("data", {}).get("id", "")
                dataset_id = None
                for _ in range(12):
                    time.sleep(5)
                    status = session.get(
                        f"https://api.apify.com/v2/actor-runs/{run_id}",
                        headers={"Authorization": f"Bearer {config.APIFY_API_KEY}"},
                        timeout=15,
                    ).json().get("data", {})
                    if status.get("status") == "SUCCEEDED":
                        dataset_id = status.get("defaultDatasetId")
                        break
                    elif status.get("status") in ("FAILED", "ABORTED", "TIMED-OUT"):
                        break
                if dataset_id:
                    items = session.get(
                        f"https://api.apify.com/v2/datasets/{dataset_id}/items",
                        headers={"Authorization": f"Bearer {config.APIFY_API_KEY}"},
                        timeout=15,
                    ).json()
                    for item in items:
                        for result in item.get("organicResults", []):
                            url = result.get("url", "")
                            if url.startswith("http") and any(
                                kw in url.lower() for kw in
                                ["sustain", "duurzaam", "csr", "mvo", "rapport", "report", "impact", "planet"]
                            ):
                                log.info("  Apify found CSR URL: %s", url)
                                return url
        except Exception as e:
            log.debug("  Apify CSR search error: %s", e)

    # Fallback: direct Google scraping
    try:
        url = "https://www.google.com/search?q=" + urllib.parse.quote(query) + "&num=5"
        resp = session.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 429:
            log.warning("  Google rate-limited, skipping search fallback")
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/url?q=" in href:
                href = href.split("/url?q=")[1].split("&")[0]
                href = urllib.parse.unquote(href)
            if href.startswith("http") and "google.com" not in href:
                if href.lower().endswith(".pdf") or any(
                    kw in href.lower() for kw in ["sustain", "duurzaam", "csr", "mvo", "rapport", "report"]
                ):
                    return href
    except Exception as e:
        log.debug("  Google search error: %s", e)
    return None


def find_pdf_link(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    """Look for PDF links on a CSR page."""
    pdf_keywords = ["rapport", "report", "sustainability", "csr", "mvo", "annual", "jaarverslag"]
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            text = a.get_text().lower()
            link_lower = href.lower()
            if any(kw in text or kw in link_lower for kw in pdf_keywords):
                return urljoin(base_url, href)
    # Any PDF link
    for a in soup.find_all("a", href=True):
        if a["href"].lower().endswith(".pdf"):
            return urljoin(base_url, a["href"])
    return None


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using pdfplumber."""
    if not HAS_PDFPLUMBER:
        return ""
    import io
    import pdfplumber
    text_parts = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:30]:  # limit to first 30 pages
                t = page.extract_text()
                if t:
                    text_parts.append(t)
    except Exception as e:
        log.warning("  PDF extraction error: %s", e)
    return "\n".join(text_parts)


def extract_text_from_html(resp: requests.Response) -> str:
    """Extract meaningful text from an HTML response."""
    soup = BeautifulSoup(resp.text, "html.parser")
    # Remove nav, footer, scripts
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:15000]  # Cap at 15k chars for API efficiency


def fetch_content_firecrawl(csr_url: str) -> Tuple[str, str]:
    """Fetch page content via Firecrawl API — handles JS, blocks, paywalls."""
    if not config.FIRECRAWL_API_KEY:
        return "", "empty"
    try:
        resp = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            json={"url": csr_url, "formats": ["markdown"]},
            headers={
                "Authorization": f"Bearer {config.FIRECRAWL_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("data", {}).get("markdown", "") or ""
            if len(text.strip()) > 200:
                log.info("  Firecrawl fetched %d chars", len(text))
                return text[:15000], "html"
            elif text.strip():
                log.debug("  Firecrawl returned too little content (%d chars) — skipping", len(text))
    except Exception as e:
        log.debug("  Firecrawl error: %s", e)
    return "", "empty"


def fetch_content(csr_url: str, session: requests.Session) -> Tuple[str, str]:
    """
    Fetch content from CSR URL. Returns (text, content_type).
    Uses Firecrawl if available, falls back to direct requests.
    """
    # PDFs: always fetch directly
    if not csr_url.lower().endswith(".pdf"):
        text, ct = fetch_content_firecrawl(csr_url)
        if text:
            return text, ct

    try:
        resp = session.get(csr_url, headers=HEADERS, timeout=15, allow_redirects=True)
        ct = resp.headers.get("content-type", "")

        if "pdf" in ct or csr_url.lower().endswith(".pdf"):
            text = extract_text_from_pdf_bytes(resp.content)
            return text, "pdf"

        # HTML page - look for embedded PDF link
        soup = BeautifulSoup(resp.text, "html.parser")
        pdf_url = find_pdf_link(soup, csr_url)
        if pdf_url:
            log.info("  Found PDF link on CSR page: %s", pdf_url)
            pdf_resp = session.get(pdf_url, headers=HEADERS, timeout=20)
            text = extract_text_from_pdf_bytes(pdf_resp.content)
            if text.strip():
                return text, "pdf"

        # Fall back to HTML text
        text = extract_text_from_html(resp)
        return text, "html"

    except Exception as e:
        log.warning("  Content fetch error for %s: %s", csr_url, e)
        return "", "empty"


# ---------------------------------------------------------------------------
# Keyword analysis — free, no API needed
# ---------------------------------------------------------------------------

# Keywords that signal each category (Dutch + English)
KEYWORDS = {
    "plastic_waste": [
        # Dutch
        "plastic", "kunststof", "verpakking", "verpakkingen", "verpakkingsmateriaal",
        "afval", "afvalreductie", "recycl", "hergebruik", "circulair", "zwerfafval",
        "bioplastic", "verpakkingsvrij", "statiegeld", "single-use", "wegwerpplastic",
        # English
        "packaging", "plastic waste", "waste reduction", "circular", "recyclable",
        "single use", "plastic-free", "recycled material", "waste management",
    ],
    "employee_education": [
        # Dutch — internal training angle
        "medewerkersopleiding", "medewerkers leren", "medewerkerstraining",
        "interne opleiding", "personeelsontwikkeling", "duurzaamheidstraining",
        "bewustwording medewerkers", "kennis medewerkers", "scholing personeel",
        "leertraject", "e-learning", "online cursus", "leerprogramma",
        # English
        "employee education", "employee training", "staff training",
        "internal training", "learning programme", "e-learning", "upskilling",
        "workforce development", "employee awareness",
    ],
    "school_sponsorship": [
        # Dutch — company sponsors schools or educational projects
        "schoolsponsoring", "onderwijs sponsoring", "sponsoring onderwijs",
        "steun aan scholen", "educatief programma", "schoolprogramma",
        "jeugd", "jongeren", "kinderen", "basisschool", "middelbare school",
        "maatschappelijke betrokkenheid", "lokale gemeenschap", "buurt",
        "stichting", "fonds", "donatie", "bijdrage aan onderwijs",
        "sociale impact", "social return", "community",
        # English
        "school sponsorship", "educational sponsorship", "youth programme",
        "children", "schools", "community investment", "social impact",
        "foundation", "donation", "local community", "giving back",
    ],
    "client_gift": [
        # Dutch — company offers education to clients / customers
        "klanten", "relaties", "relatiegeschenk", "klantbeleving",
        "kennisdeling met klanten", "klantprogramma", "klanttevredenheid",
        "consument", "consumenten", "eindgebruiker", "afnemer",
        "klantenservice", "loyaliteit", "klantrelatie",
        # English
        "clients", "customers", "customer experience", "client programme",
        "consumer education", "customer loyalty", "value-add", "end user",
        "client relations", "customer engagement",
    ],
    "sustainability": [
        # Dutch — general sustainability signal (lower weight)
        "duurzaamheid", "duurzaam", "mvo", "maatschappelijk verantwoord",
        "klimaat", "co2", "co₂", "uitstoot", "milieu", "groen", "carbon",
        "netto nul", "net zero", "energietransitie", "fossielvrij",
        # English
        "sustainability", "sustainable", "climate", "emissions", "carbon neutral",
        "net zero", "environmental", "green", "esg", "responsibility",
    ],
    "community_local": [
        # Dutch — local roots and community giving
        "lokale gemeenschap", "onze stad", "onze regio", "thuisstad", "buurt",
        "buurtbetrokkenheid", "lokale betrokkenheid", "regionaal", "regio",
        "lokale sponsor", "sponsoring", "sponsoren", "voetbalclub", "sportclub",
        "lokaal initiatief", "maatschappelijke bijdrage", "teruggeven aan",
        "verbonden met", "geworteld in", "geboren in", "onze roots",
        "lokale scholen", "scholen in onze regio", "buurtschool",
        # English
        "local community", "our city", "our region", "hometown", "community roots",
        "local sponsorship", "giving back", "rooted in", "community investment",
        "local schools", "neighbourhood",
    ],
}


def extract_quote(text: str, keyword: str, context_chars: int = 120) -> str:
    """Extract a short sentence around a keyword match."""
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return ""
    start = max(0, idx - context_chars // 2)
    end = min(len(text), idx + context_chars // 2)
    snippet = text[start:end].strip()
    # Clean up partial words at edges
    if start > 0 and not text[start - 1].isspace():
        snippet = snippet[snippet.find(" ") + 1:]
    if end < len(text) and not text[end].isspace():
        snippet = snippet[: snippet.rfind(" ")]
    return snippet.replace("\n", " ").strip()


def keyword_analysis(text: str) -> dict:
    """
    Scan text for relevant keywords. Free, instant, no API.

    Detects four SoR sales angles and picks the best fit:
      employee_education — train own staff on waste/sustainability
      school_sponsorship — company sponsors schools or youth programmes
      client_gift        — offer courses to clients/customers
      custom_course      — strong brand + plastic identity (inferred)

    Scoring (1-10):
      plastic/waste keywords     → +3 (core topic)
      employee education signals → +2
      school sponsorship signals → +2
      client gift signals        → +1
      sustainability signals     → +1
      bonus +1 per category with 3+ distinct hits
    """
    text_lower = text.lower()
    quotes = []
    hits = {}

    for category, keywords in KEYWORDS.items():
        matched = [kw for kw in keywords if kw in text_lower]
        hits[category] = matched
        if matched:
            quote = extract_quote(text, matched[0])
            if quote:
                quotes.append(quote[:120])

    mentions_plastic = bool(hits["plastic_waste"])
    mentions_emp_edu = bool(hits["employee_education"])
    mentions_school = bool(hits["school_sponsorship"])
    mentions_client = bool(hits["client_gift"])
    mentions_sus = bool(hits["sustainability"])
    mentions_community = bool(hits["community_local"])

    # Combined education signal for backwards-compatible field
    mentions_edu = mentions_emp_edu or mentions_school

    # Scoring
    score = 1
    if mentions_plastic:
        score += 3
        if len(hits["plastic_waste"]) >= 3:
            score += 1
    if mentions_emp_edu:
        score += 2
        if len(hits["employee_education"]) >= 3:
            score += 1
    if mentions_school:
        score += 2
        if len(hits["school_sponsorship"]) >= 3:
            score += 1
    if mentions_client:
        score += 1
    if mentions_sus:
        score += 1
    if mentions_community:
        score += 2
        # Warm lead combo: plastic waste + local community = +1 bonus
        if mentions_plastic:
            score += 1
    score = min(score, 10)

    # Determine best sales angle
    # community_local signal boosts the school_sponsorship angle
    community_boost = len(hits["community_local"])
    angle_scores = {
        "employee_education": len(hits["employee_education"]) * 2 + len(hits["plastic_waste"]),
        "school_sponsorship": len(hits["school_sponsorship"]) * 2 + community_boost,
        "client_gift":        len(hits["client_gift"]) * 2,
        "custom_course":      len(hits["plastic_waste"]) * 2 + len(hits["sustainability"]),
    }
    best_angle = max(angle_scores, key=lambda k: angle_scores[k])
    # Only assign an angle if we have at least some signal
    if angle_scores[best_angle] == 0:
        best_angle = "none"

    # Human-readable angle labels
    angle_labels = {
        "employee_education": "Medewerkerseducatie — train eigen personeel",
        "school_sponsorship": "Schoolsponsoring — subsidieer cursussen voor scholen",
        "client_gift":        "Klantgeschenk — bied cursussen aan klanten/relaties",
        "custom_course":      "Maatwerk cursus — bedrijfsspecifieke afvalcursus",
        "none":               "Onbekend — nader onderzoek nodig",
    }

    # Build summary
    signals = []
    if hits["plastic_waste"]:
        signals.append(f"plastic/afval ({', '.join(hits['plastic_waste'][:3])})")
    if hits["employee_education"]:
        signals.append(f"medewerkerseducatie ({', '.join(hits['employee_education'][:2])})")
    if hits["school_sponsorship"]:
        signals.append(f"schoolsponsoring ({', '.join(hits['school_sponsorship'][:2])})")
    if hits["client_gift"]:
        signals.append(f"klantrelaties ({', '.join(hits['client_gift'][:2])})")
    if hits["sustainability"]:
        signals.append(f"duurzaamheid ({', '.join(hits['sustainability'][:2])})")
    if hits["community_local"]:
        signals.append(f"lokale gemeenschap ({', '.join(hits['community_local'][:2])})")

    summary = f"Beste hoek: {angle_labels[best_angle]}. Gevonden: {' | '.join(signals)}." if signals else "Geen relevante trefwoorden gevonden."

    return {
        "mentions_education": mentions_edu,
        "mentions_sustainability": mentions_sus,
        "mentions_plastic_waste": mentions_plastic,
        "mentions_community": mentions_community,
        "best_angle": best_angle,
        "angle_label": angle_labels[best_angle],
        "relevance_score": score,
        "key_quotes": " | ".join(quotes[:2]) if quotes else "",
        "analysis_summary": summary,
    }


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def load_progress() -> dict:
    if config.PROGRESS_JSON.exists():
        with open(config.PROGRESS_JSON) as f:
            return json.load(f)
    return {}


def analyze_with_claude(company_name: str, text: str) -> Optional[dict]:
    """
    Use Claude Haiku to deeply analyze CSR text.
    Called only when keyword scan score < 6 but a page was found.
    Returns updated analysis dict or None if API unavailable.
    """
    if not HAS_ANTHROPIC or not config.ANTHROPIC_API_KEY:
        return None

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    prompt = f"""Analyseer deze tekst van de website van {company_name} en beoordeel de relevantie voor School of Recycling — een educatief platform dat bedrijven helpt via online cursussen over afval, plastic en recycling (schoolofrecycling.com).

TEKST:
---
{text[:6000]}
---

Beantwoord in JSON:
{{
  "mentions_education": true/false,
  "mentions_sustainability": true/false,
  "mentions_plastic_waste": true/false,
  "mentions_community": true/false,
  "relevance_score": <1-10>,
  "best_angle": "<employee_education|school_sponsorship|client_gift|custom_course|none>",
  "key_quotes": "<max 2 relevante quotes, max 100 tekens elk, gescheiden door |>",
  "analysis_summary": "<max 2 zinnen: beste hoek + waarom relevant>"
}}

Scoringsregels:
- Plastic/verpakking/afval/recycling/circulair als kernthema: +3
- Medewerkersopleiding/training/educatie: +2
- Schoolsponsoring/jeugd/lokale gemeenschap/lokale betrokkenheid: +2
- Klantrelaties/klantbeleving: +1
- Algemene duurzaamheid: +1
- Score 8-10 = sterke lead, 6-7 = goede lead, 4-5 = interessant, <4 = skip

Geef ALLEEN geldige JSON terug."""

    try:
        msg = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
            # Add angle_label
            angle_labels = {
                "employee_education": "Medewerkerseducatie — train eigen personeel",
                "school_sponsorship": "Schoolsponsoring — subsidieer cursussen voor scholen",
                "client_gift":        "Klantgeschenk — bied cursussen aan klanten/relaties",
                "custom_course":      "Maatwerk cursus — bedrijfsspecifieke afvalcursus",
                "none":               "Onbekend — nader onderzoek nodig",
            }
            result["angle_label"] = angle_labels.get(result.get("best_angle", "none"), "")
            return result
    except Exception as e:
        log.warning("  Claude API error for %s: %s", company_name, e)
    return None


def save_progress(progress: dict) -> None:
    with open(config.PROGRESS_JSON, "w") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)


def load_existing_results(path: Path) -> dict:
    """Load already-processed companies from CSV to allow resume."""
    results = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                results[row["company_name"]] = row
    return results


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run(limit: Optional[int] = None, dry_run: bool = False) -> List[dict]:
    api_mode = "keywords + Claude API" if (HAS_ANTHROPIC and config.ANTHROPIC_API_KEY) else "keywords only (free)"
    log.info("Stage 2: Scanning CSR reports (%s)", api_mode)

    # Load companies
    if not config.COMPANIES_CSV.exists():
        log.error("companies.csv not found - run stage 1 first")
        return []

    companies = []
    with open(config.COMPANIES_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            companies.append(dict(row))

    if limit:
        companies = companies[:limit]
        log.info("Limited to %d companies", limit)

    # Load existing results for resume capability
    existing = load_existing_results(config.CSR_ANALYSIS_CSV)
    progress = load_progress()

    session = requests.Session()
    session.headers.update(HEADERS)

    results = []
    for i, company in enumerate(companies, 1):
        name = company["company_name"]
        log.info("[%d/%d] Processing: %s", i, len(companies), name)

        # Skip if already processed
        if name in existing:
            log.info("  Skipping (already processed)")
            results.append(existing[name])
            continue

        row = {"company_name": name, "csr_url": "", **MOCK_ANALYSIS.copy()}

        if dry_run:
            row["csr_url"] = f"{company['website']}/sustainability (dry-run)"
            results.append(row)
            progress[name] = {"stage": "csr_scanned", "dry_run": True}
            log.info("  [DRY-RUN] Mock CSR analysis done")
            time.sleep(0.1)
            continue

        # 1. Find CSR URL
        csr_url = find_csr_url(company, session)
        row["csr_url"] = csr_url or ""

        if not csr_url:
            log.warning("  No CSR URL found for %s", name)
            row.update({
                "mentions_education": False,
                "mentions_sustainability": False,
                "mentions_plastic_waste": False,
                "mentions_community": False,
                "relevance_score": 0,
                "key_quotes": "",
                "analysis_summary": "No CSR page found",
            })
            results.append(row)
            progress[name] = {"stage": "csr_no_url"}
            save_progress(progress)
            time.sleep(config.REQUEST_DELAY)
            continue

        # 2. Fetch content
        text, content_type = fetch_content(csr_url, session)
        log.info("  Fetched %s content (%d chars)", content_type, len(text))

        if not text.strip():
            log.warning("  Empty content for %s", name)
            row.update({
                "mentions_education": False,
                "mentions_sustainability": False,
                "mentions_plastic_waste": False,
                "mentions_community": False,
                "relevance_score": 1,
                "key_quotes": "",
                "analysis_summary": "Could not extract text from CSR page",
            })
            results.append(row)
            progress[name] = {"stage": "csr_empty"}
            save_progress(progress)
            time.sleep(config.REQUEST_DELAY)
            continue

        # 3. Keyword analysis — free, no API
        analysis = keyword_analysis(text)

        # 4. If keyword score < 6 and API available, let Claude re-read the page
        kw_score = analysis.get("relevance_score", 0)
        if kw_score < 6 and config.ANTHROPIC_API_KEY:
            log.info("  Keyword score=%d — asking Claude for deeper read...", kw_score)
            claude_analysis = analyze_with_claude(name, text)
            if claude_analysis:
                claude_score = claude_analysis.get("relevance_score", 0)
                log.info("  Claude score=%d (was %d) — using %s",
                         claude_score, kw_score,
                         "Claude" if claude_score >= kw_score else "keywords")
                if claude_score >= kw_score:
                    analysis = claude_analysis
            time.sleep(config.API_DELAY)

        row.update(analysis)
        results.append(row)

        progress[name] = {
            "stage": "csr_scanned",
            "relevance_score": analysis.get("relevance_score", 0),
            "csr_url": csr_url,
        }
        save_progress(progress)
        log.info(
            "  Score: %s | edu=%s sus=%s plastic=%s",
            analysis.get("relevance_score"),
            analysis.get("mentions_education"),
            analysis.get("mentions_sustainability"),
            analysis.get("mentions_plastic_waste"),
        )
        time.sleep(config.REQUEST_DELAY)

    # Save all results
    config.DATA_DIR.mkdir(exist_ok=True)
    with open(config.CSR_ANALYSIS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSR_ANALYSIS_FIELDS)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in CSR_ANALYSIS_FIELDS})

    relevant = [r for r in results if int(r.get("relevance_score", 0)) >= config.RELEVANCE_THRESHOLD]
    log.info(
        "Stage 2 complete: %d companies scanned, %d relevant (score >= %d)",
        len(results), len(relevant), config.RELEVANCE_THRESHOLD,
    )
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Stage 2: Scan CSR reports")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)
