"""
Stage 2: Scan CSR/MVO reports for each company.
For each company, finds their CSR report URL, downloads/parses it,
then uses free keyword matching to detect education, sustainability,
and plastic/waste mentions. No AI API needed — 100% free.
Saves results to data/csr_analysis.csv
"""
import sys
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
    "relevance_score",
    "key_quotes",
    "analysis_summary",
]

MOCK_ANALYSIS = {
    "mentions_education": True,
    "mentions_sustainability": True,
    "mentions_plastic_waste": False,
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


def try_url(url: str, session: requests.Session, timeout: int = 8) -> requests.Response | None:
    """Try fetching a URL, return Response or None."""
    try:
        resp = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 500:
            return resp
    except Exception:
        pass
    return None


def find_csr_url(company: dict, session: requests.Session) -> str | None:
    """
    Try common CSR URL patterns first, then fall back to Google search scraping.
    Returns the best URL found, or None.
    """
    base = get_base_url(company["website"])
    candidates = [p.format(base=base) for p in config.CSR_URL_PATTERNS]

    for url in candidates:
        log.debug("  Trying pattern URL: %s", url)
        resp = try_url(url, session)
        if resp:
            log.info("  Found CSR page via pattern: %s", url)
            return url
        time.sleep(0.3)

    # Fall back to searching page links on the homepage
    log.debug("  Pattern URLs failed, checking homepage for CSR links...")
    csr_keywords = [
        "sustainability", "duurzaamheid", "csr", "mvo", "verantwoord",
        "impact", "responsibility", "rapport", "report",
    ]
    try:
        resp = try_url(company["website"], session)
        if resp:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].lower()
                text = a.get_text().lower()
                if any(kw in href or kw in text for kw in csr_keywords):
                    full_url = urljoin(company["website"], a["href"])
                    log.info("  Found CSR link via homepage scan: %s", full_url)
                    return full_url
    except Exception as e:
        log.debug("  Homepage scan failed: %s", e)

    return None


def find_pdf_link(soup: BeautifulSoup, base_url: str) -> str | None:
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


def fetch_content(csr_url: str, session: requests.Session) -> tuple[str, str]:
    """
    Fetch content from CSR URL. Returns (text, content_type).
    content_type: 'pdf', 'html', or 'empty'
    """
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
    "education": [
        # Dutch
        "opleiding", "opleidingen", "onderwijs", "educatie", "training", "leren",
        "bewustwording", "kennisdeling", "medewerkersopleiding", "scholing",
        "cursus", "leerprogramma", "bewust", "kennis", "voorlichting",
        # English
        "education", "training", "learning", "awareness", "employee education",
        "knowledge", "upskilling", "programme", "curriculum",
    ],
    "sustainability": [
        # Dutch
        "duurzaamheid", "duurzaam", "mvo", "maatschappelijk verantwoord",
        "klimaat", "co2", "co₂", "uitstoot", "milieu", "groen", "carbon",
        "netto nul", "net zero", "energietransitie", "fossielvrij",
        # English
        "sustainability", "sustainable", "climate", "emissions", "carbon neutral",
        "net zero", "environmental", "green", "esg", "responsibility",
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
    Scores 1-10 based on signal strength:
      plastic/waste keywords found  → +3 (core SoR topic)
      education keywords found      → +2
      sustainability keywords found → +1
      bonus for multiple hits       → +1 each category (max +1)
    """
    text_lower = text.lower()
    quotes = []
    hits = {}

    for category, keywords in KEYWORDS.items():
        matched = [kw for kw in keywords if kw in text_lower]
        hits[category] = matched

        if matched:
            # Pull a real quote for the strongest keyword hit
            quote = extract_quote(text, matched[0])
            if quote:
                quotes.append(quote[:120])

    mentions_plastic = bool(hits["plastic_waste"])
    mentions_edu = bool(hits["education"])
    mentions_sus = bool(hits["sustainability"])

    # Scoring — weighted for SoR relevance
    score = 1
    if mentions_plastic:
        score += 3
        if len(hits["plastic_waste"]) >= 3:  # multiple plastic signals = strong lead
            score += 1
    if mentions_edu:
        score += 2
        if len(hits["education"]) >= 3:
            score += 1
    if mentions_sus:
        score += 1
    score = min(score, 10)

    # Build summary from actual matched keywords
    signals = []
    if hits["plastic_waste"]:
        signals.append(f"plastic/afval ({', '.join(hits['plastic_waste'][:3])})")
    if hits["education"]:
        signals.append(f"educatie ({', '.join(hits['education'][:3])})")
    if hits["sustainability"]:
        signals.append(f"duurzaamheid ({', '.join(hits['sustainability'][:2])})")

    summary = f"Gevonden: {' | '.join(signals)}." if signals else "Geen relevante trefwoorden gevonden."

    return {
        "mentions_education": mentions_edu,
        "mentions_sustainability": mentions_sus,
        "mentions_plastic_waste": mentions_plastic,
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

def run(limit: int | None = None, dry_run: bool = False) -> list[dict]:
    log.info("Stage 2: Scanning CSR reports (free keyword analysis — no API)")

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
