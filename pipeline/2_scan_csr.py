"""
Stage 2: Scan CSR/MVO reports for each company.
For each company, finds their CSR report URL, downloads/parses it,
then uses Claude to analyze for education, sustainability, plastic waste mentions.
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
# Claude analysis
# ---------------------------------------------------------------------------

def analyze_with_claude(company_name: str, text: str, dry_run: bool = False) -> dict:
    """Use Claude Haiku to analyze CSR text. Returns dict with analysis fields."""
    if dry_run or not HAS_ANTHROPIC or not config.ANTHROPIC_API_KEY:
        return MOCK_ANALYSIS.copy()

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    prompt = f"""Je bent een analist die CSR/duurzaamheidsrapporten van Nederlandse bedrijven analyseert.

Analyseer de volgende tekst van het CSR-rapport van **{company_name}** en geef een gestructureerde analyse.

TEKST:
---
{text[:8000]}
---

Beantwoord de volgende vragen in JSON-formaat:

1. Wordt **onderwijs** of **opleidingen** (voor medewerkers, de samenleving, of in het kader van duurzaamheid) genoemd?
2. Wordt **duurzaamheid** of **sustainability** als kernthema behandeld?
3. Wordt **plasticreductie**, **kunststof afval**, of **verpakkingsvermindering** specifiek besproken?
4. Geef een relevantiescore van 1-10 (10 = sterk relevant voor een educatief platform dat bedrijven helpt bij het behalen van duurzaamheidsdoelen via medewerkersopleiding).
5. Geef maximaal 2 korte quotes (max 100 tekens elk) die het meest relevant zijn.
6. Geef een samenvatting van maximaal 2 zinnen.

Antwoord ALLEEN met geldige JSON in dit formaat:
{{
  "mentions_education": true/false,
  "mentions_sustainability": true/false,
  "mentions_plastic_waste": true/false,
  "relevance_score": <int 1-10>,
  "key_quotes": "<quote1> | <quote2>",
  "analysis_summary": "<2 sentences max>"
}}"""

    try:
        msg = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Extract JSON from response (handle potential markdown fences)
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        log.warning("  Could not parse Claude JSON response for %s", company_name)
        return _fallback_analysis(text)
    except Exception as e:
        log.warning("  Claude API error for %s: %s", company_name, e)
        return _fallback_analysis(text)


def _fallback_analysis(text: str) -> dict:
    """Keyword-based fallback if Claude API fails."""
    text_lower = text.lower()
    edu_keywords = ["onderwijs", "opleiding", "training", "educatie", "leren", "education"]
    sus_keywords = ["duurzaamheid", "sustainability", "co2", "klimaat", "milieu", "groen"]
    plastic_keywords = ["plastic", "kunststof", "verpakking", "afval", "recycl"]

    mentions_edu = any(k in text_lower for k in edu_keywords)
    mentions_sus = any(k in text_lower for k in sus_keywords)
    mentions_plastic = any(k in text_lower for k in plastic_keywords)

    score = 3
    if mentions_edu:
        score += 2
    if mentions_sus:
        score += 2
    if mentions_plastic:
        score += 1

    return {
        "mentions_education": mentions_edu,
        "mentions_sustainability": mentions_sus,
        "mentions_plastic_waste": mentions_plastic,
        "relevance_score": score,
        "key_quotes": "(keyword-based fallback analysis)",
        "analysis_summary": "Fallback keyword analysis used - Claude API unavailable.",
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
    log.info("Stage 2: Scanning CSR reports")

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

        # 3. Analyze with Claude
        time.sleep(config.API_DELAY)
        analysis = analyze_with_claude(name, text, dry_run=dry_run)
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
