"""
Stage 3: Find contact persons for relevant companies.
For each company with relevance_score >= threshold:
  - Searches LinkedIn profiles via Google (site:linkedin.com/in) FIRST
  - Falls back to scraping company website team pages
  - Uses Claude to extract contact names and titles
  - Attempts email pattern guessing or Hunter.io lookup
Saves to data/contacts.csv
"""
import sys
from typing import Optional, Tuple, List, Dict
import csv
import json
import time
import logging
import random
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from pipeline._already_in_talks import ALREADY_IN_TALKS

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

CONTACTS_FIELDS = [
    "company_name",
    "website",
    "ownership",
    "contact_name",
    "contact_title",
    "contact_email",
    "email_confidence",
    "linkedin_url",
    "contact_source",
    "relevance_score",
]

# URL patterns to find team/about pages
TEAM_URL_PATTERNS = [
    "{base}/over-ons/team",
    "{base}/about/team",
    "{base}/over-ons",
    "{base}/about-us",
    "{base}/team",
    "{base}/contact",
    "{base}/leadership",
    "{base}/management",
    "{base}/bestuur",
    "{base}/directie",
    "{base}/duurzaamheid/team",
    "{base}/sustainability/team",
    "{base}/sustainability/governance",
    "{base}/duurzaamheid/beleid",
    "{base}/nl/over-ons",
    "{base}/nl/contact",
]

# Titles to target (Dutch and English) — sustainability, community and communications roles
TARGET_TITLES = [
    # Core sustainability
    "duurzaamheidsmanager", "duurzaamheid manager",
    "sustainability manager", "sustainability director",
    "csr manager", "csr director", "csr lead",
    "mvo manager", "mvo coordinator",
    "corporate responsibility",
    "head of sustainability", "director sustainability",
    "manager duurzaamheid", "hoofd duurzaamheid",
    "environmental manager", "climate manager",
    "impact manager", "impact director",
    "esg manager", "esg director",
    # Community and communications (relevant for school sponsorship angle)
    "hoofd communicatie", "communicatiemanager", "pr manager",
    "community manager", "maatschappelijke betrokkenheid",
    # Senior decision-makers at small family companies
    "directeur", "eigenaar", "oprichter", "algemeen directeur",
    "ceo", "managing director",
]

# Keywords that indicate a preferred contact for sustainability/community outreach
# Ordered by preference — first match wins
PREFERRED_TITLE_KEYWORDS = [
    # Tier 1 — sustainability specialist, perfect contact
    "duurzaamheidsmanager", "csr manager", "mvo manager", "sustainability manager",
    "duurzaamheid", "csr", "mvo", "sustainability", "environmental",
    # Tier 2 — community / communications, good for school/gift angles
    "maatschappelijk", "community", "communicatie", "pr manager",
    # Tier 3 — HR / learning, good for employee education angle
    "opleiding", "hr manager", "people", "learning",
    # Tier 4 — senior decision-maker, always valid at family companies
    "directeur", "eigenaar", "oprichter", "ceo", "algemeen directeur",
]

MOCK_CONTACTS = [
    {
        "contact_name": "Jan de Vries",
        "contact_title": "Sustainability Manager",
        "contact_email": "j.devries@example.com",
        "email_confidence": "pattern",
        "linkedin_url": "https://www.linkedin.com/in/jandevries",
        "contact_source": "dry-run mock",
    }
]


# ---------------------------------------------------------------------------
# Web helpers
# ---------------------------------------------------------------------------

def get_base_url(website: str) -> str:
    parsed = urlparse(website)
    return f"{parsed.scheme}://{parsed.netloc}"


def try_url(url: str, session: requests.Session, timeout: int = 8) -> Optional[requests.Response]:
    try:
        resp = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 200:
            return resp
    except Exception:
        pass
    return None


def find_team_pages(website: str, session: requests.Session) -> List[str]:
    """Try common team/about page patterns. Return list of found URLs."""
    base = get_base_url(website)
    found = []
    for pattern in TEAM_URL_PATTERNS:
        url = pattern.format(base=base)
        resp = try_url(url, session)
        if resp:
            found.append(url)
            log.debug("  Found team page: %s", url)
        time.sleep(0.2)
        if len(found) >= 3:
            break
    return found


def extract_page_text(resp: requests.Response) -> str:
    """Clean text from HTML response."""
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)[:8000]


def extract_linkedin_links(resp: requests.Response) -> List[str]:
    """Find LinkedIn profile URLs on a page."""
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "linkedin.com/in/" in href:
            links.append(href)
    return list(set(links))


# ---------------------------------------------------------------------------
# LinkedIn via Google search
# ---------------------------------------------------------------------------

def _google_jitter_sleep() -> None:
    """Sleep between 8 and 15 seconds to avoid Google rate limiting."""
    delay = random.uniform(8, 15)
    log.debug("  Google rate limit delay: %.1fs", delay)
    time.sleep(delay)


def _parse_linkedin_from_google_results(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """
    Extract LinkedIn profile candidates from a Google search results page.
    Each result title typically looks like: "Name - Title - Company | LinkedIn"
    Returns list of {"name": ..., "title": ..., "linkedin_url": ...}
    """
    candidates = []
    seen_urls = set()

    # Google renders results in <div class="g"> blocks with an <a> containing the URL
    # We look for any anchor whose href contains linkedin.com/in/
    for a in soup.find_all("a", href=True):
        href = a["href"]

        # Google often wraps URLs as /url?q=https://...
        url_match = re.search(r"/url\?q=(https://[^&]+)", href)
        if url_match:
            href = url_match.group(1)

        if "linkedin.com/in/" not in href:
            continue

        # Clean the URL — strip query params
        clean_url = href.split("?")[0].rstrip("/")
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        # Try to get the title text — walk up to a result container
        name = ""
        title = ""
        # The h3 nearest to this link typically has "Name - Title - Company | LinkedIn"
        parent = a.find_parent()
        title_tag = None
        for _ in range(5):
            if parent is None:
                break
            title_tag = parent.find("h3")
            if title_tag:
                break
            parent = parent.find_parent()

        if title_tag:
            raw_title = title_tag.get_text(strip=True)
            # Strip trailing "| LinkedIn" or "- LinkedIn"
            raw_title = re.sub(r"[\|–\-]\s*LinkedIn\s*$", "", raw_title, flags=re.IGNORECASE).strip()
            # Split on " - " or " – "
            parts = re.split(r"\s+[-–]\s+", raw_title)
            if parts:
                name = parts[0].strip()
            if len(parts) >= 2:
                title = parts[1].strip()

        if clean_url:
            candidates.append({
                "name": name,
                "title": title,
                "linkedin_url": clean_url,
            })

    return candidates


def _pick_best_linkedin_candidate(candidates: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """
    Pick the best LinkedIn candidate by title preference order.
    Tier 1 (sustainability) beats Tier 2 (comms) beats Tier 3 (HR) beats Tier 4 (CEO).
    Falls back to first result if nothing matches at all.
    """
    if not candidates:
        return None

    # Score each candidate by the position of the first matching keyword
    # Lower index in PREFERRED_TITLE_KEYWORDS = higher priority
    def title_score(candidate: Dict[str, str]) -> int:
        title_lower = candidate.get("title", "").lower()
        for i, kw in enumerate(PREFERRED_TITLE_KEYWORDS):
            if kw in title_lower:
                return i
        return len(PREFERRED_TITLE_KEYWORDS)  # no match — lowest priority

    scored = sorted(candidates, key=title_score)
    best = scored[0]

    # Only return a CEO/owner if no better option exists
    best_score = title_score(best)
    ceo_threshold = PREFERRED_TITLE_KEYWORDS.index("directeur")
    if best_score >= ceo_threshold:
        log.debug("  Best match is a senior decision-maker (no specialist found) — still valid for family companies")

    return best


def find_contact_via_bing(company_name: str, website: str, session: requests.Session) -> Optional[Dict[str, str]]:
    """
    Smart contact finder using Bing search + Claude.

    Strategy:
    1. Bing: "[Company] duurzaamheid verantwoordelijke OR MVO OR sustainability"
    2. Fetch top 3 result pages (company site, press, news)
    3. Claude reads all text and extracts: who is responsible for sustainability/CSR/education?
    4. Bonus: grab LinkedIn URL if it appears anywhere in results

    Returns {"name": "...", "title": "...", "linkedin_url": "..."} or None.
    """
    if not HAS_ANTHROPIC or not config.ANTHROPIC_API_KEY:
        return None

    queries = [
        f'"{company_name}" duurzaamheid verantwoordelijke OR MVO manager OR sustainability',
        f'"{company_name}" directeur eigenaar oprichter CEO',
    ]

    all_text = []
    linkedin_urls_found = []

    for query in queries:
        try:
            resp = session.get(
                "https://www.bing.com/search",
                params={"q": query, "count": 8, "setlang": "nl"},
                headers={**HEADERS, "Accept-Language": "nl-NL,nl;q=0.9"},
                timeout=12,
            )
            if resp.status_code != 200:
                time.sleep(3)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract LinkedIn URLs from results
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "linkedin.com/in/" in href:
                    linkedin_urls_found.append(href)

            # Collect result snippets + titles
            snippets = []
            for result in soup.select(".b_algo")[:5]:
                title = result.select_one("h2")
                snippet = result.select_one(".b_caption p, .b_snippetBigText")
                url_tag = result.select_one("cite")
                parts = []
                if title:
                    parts.append(title.get_text())
                if snippet:
                    parts.append(snippet.get_text())
                if url_tag:
                    parts.append(url_tag.get_text())
                if parts:
                    snippets.append(" | ".join(parts))

            if snippets:
                all_text.append(f"[Query: {query}]\n" + "\n".join(snippets))

            # Also try fetching the top non-LinkedIn result page
            for a in soup.select(".b_algo h2 a")[:2]:
                href = a.get("href", "")
                if href.startswith("http") and "linkedin.com" not in href:
                    try:
                        page = session.get(href, headers=HEADERS, timeout=8)
                        if page.status_code == 200 and "text/html" in page.headers.get("content-type",""):
                            page_soup = BeautifulSoup(page.text, "html.parser")
                            for tag in page_soup(["script","style","nav","footer"]):
                                tag.decompose()
                            page_text = page_soup.get_text(separator=" ", strip=True)[:3000]
                            all_text.append(f"[Page: {href}]\n{page_text}")
                            # grab any LinkedIn URLs from this page too
                            for la in page_soup.find_all("a", href=True):
                                if "linkedin.com/in/" in la["href"]:
                                    linkedin_urls_found.append(la["href"])
                    except Exception:
                        pass
                    break

            time.sleep(random.uniform(3, 6))

        except Exception as e:
            log.debug("  Bing search error: %s", e)
            continue

        # Stop after first query if we already have good text
        if len(all_text) >= 2:
            break

    if not all_text:
        log.info("  Bing: no results for %s", company_name)
        return None

    # Ask Claude to extract the responsible person
    combined = "\n\n".join(all_text)[:8000]
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    prompt = f"""Je bent op zoek naar de juiste contactpersoon bij {company_name} voor School of Recycling — een platform voor duurzaamheidseducatie.

Zoek in de tekst hieronder naar een persoon die verantwoordelijk is voor duurzaamheid, MVO, CSR, communicatie, HR/opleiding, of de directeur/eigenaar.
Geef voorkeur aan: duurzaamheidsmanager > communicatiemanager > HR/opleiding > directeur/eigenaar.

TEKST:
{combined}

Geef ALLEEN deze JSON terug:
{{"name": "Voornaam Achternaam", "title": "Functietitel", "confidence": "high/medium/low"}}

Als er geen specifiek persoon gevonden wordt: {{"name": "", "title": "", "confidence": "low"}}
Alleen geldige JSON, niets anders."""

    try:
        msg = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
            name = result.get("name", "").strip()
            title = result.get("title", "").strip()
            confidence = result.get("confidence", "low")

            if name and len(name.split()) >= 2:
                # Pick best LinkedIn URL if found
                li_url = ""
                for url in linkedin_urls_found:
                    if any(part.lower() in url.lower() for part in name.lower().split()):
                        li_url = url
                        break
                if not li_url and linkedin_urls_found:
                    li_url = linkedin_urls_found[0]

                log.info("  Bing+Claude found: %s (%s) [%s]", name, title, confidence)
                return {
                    "contact_name": name,
                    "contact_title": title,
                    "contact_email": "",
                    "email_confidence": "",
                    "linkedin_url": li_url,
                    "contact_source": f"bing_claude:{confidence}",
                }
    except Exception as e:
        log.debug("  Claude contact extraction error: %s", e)

    log.info("  Bing+Claude: no contact found for %s", company_name)
    return None


# Keep old function name as alias so existing call sites still work
def find_linkedin_via_google(company_name: str, session: requests.Session) -> Optional[Dict[str, str]]:
    return None  # replaced by find_contact_via_bing


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def get_domain(website: str) -> str:
    """Extract domain from website URL."""
    parsed = urlparse(website)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def guess_email_patterns(first: str, last: str, domain: str) -> List[Tuple[str, str]]:
    """
    Generate common corporate email patterns.
    Returns list of (email, pattern_name) tuples.
    """
    first = first.lower().strip()
    last = last.lower().strip()
    last_clean = last.replace(" ", "").replace("-", "")
    first_initial = first[0] if first else ""

    patterns = [
        (f"{first}.{last}@{domain}", "first.last"),
        (f"{first_initial}.{last}@{domain}", "f.last"),
        (f"{first}{last}@{domain}", "firstlast"),
        (f"{first_initial}{last}@{domain}", "flast"),
        (f"{first}.{last_clean}@{domain}", "first.lastclean"),
        (f"{last}.{first}@{domain}", "last.first"),
        (f"{first}@{domain}", "first"),
    ]
    return patterns


def hunter_lookup(company_name: str, domain: str, session: requests.Session) -> List[dict]:
    """Query Hunter.io domain search API."""
    if not config.HUNTER_API_KEY:
        return []
    try:
        url = "https://api.hunter.io/v2/domain-search"
        params = {
            "domain": domain,
            "api_key": config.HUNTER_API_KEY,
            "limit": 10,
            "seniority": "senior,executive",
        }
        resp = session.get(url, params=params, timeout=10)
        data = resp.json()
        contacts = []
        for email_data in data.get("data", {}).get("emails", []):
            title = email_data.get("position", "")
            title_lower = title.lower()
            if any(kw in title_lower for kw in TARGET_TITLES):
                contacts.append({
                    "contact_name": f"{email_data.get('first_name', '')} {email_data.get('last_name', '')}".strip(),
                    "contact_title": title,
                    "contact_email": email_data.get("value", ""),
                    "email_confidence": f"hunter:{email_data.get('confidence', 0)}%",
                    "linkedin_url": email_data.get("linkedin", ""),
                    "contact_source": "hunter.io",
                })
        return contacts
    except Exception as e:
        log.debug("Hunter.io error for %s: %s", company_name, e)
        return []


# ---------------------------------------------------------------------------
# Claude contact extraction
# ---------------------------------------------------------------------------

def extract_contacts_with_claude(
    company_name: str,
    page_texts: List[str],
    linkedin_links: List[str],
    dry_run: bool = False,
) -> List[dict]:
    """Use Claude to extract contact persons from team page text."""
    if dry_run or not HAS_ANTHROPIC or not config.ANTHROPIC_API_KEY:
        return MOCK_CONTACTS.copy()

    combined_text = "\n\n---\n\n".join(page_texts)[:6000]
    linkedin_text = "\n".join(linkedin_links[:10]) if linkedin_links else "geen LinkedIn links gevonden"

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    prompt = f"""Analyseer de volgende tekst van de website van **{company_name}** en zoek naar contactpersonen die verantwoordelijk zijn voor duurzaamheid, CSR, MVO, milieu, communicatie of maatschappelijke betrokkenheid.

WEBSITETEKST:
---
{combined_text}
---

GEVONDEN LINKEDIN LINKS:
{linkedin_text}

Zoek naar mensen met titels zoals:
- Sustainability Manager/Director/Lead
- CSR Manager/Director
- MVO Manager/Coordinator
- Duurzaamheidsmanager
- Head of Sustainability
- Environmental Manager
- ESG Manager/Director
- Impact Manager
- Hoofd Communicatie / Communicatiemanager / PR Manager
- Community Manager / Maatschappelijke betrokkenheid
- Directeur / Eigenaar / Oprichter (voor kleine familiebedrijven)

Geef een JSON array met maximaal 3 meest relevante contactpersonen. Als er geen directe match is, neem dan de meest senior persoon op het gebied van duurzaamheid, communicatie of een algemeen directeur/manager.

Antwoord ALLEEN met geldige JSON:
[
  {{
    "contact_name": "volledige naam",
    "contact_title": "functietitel",
    "linkedin_url": "linkedin URL of leeg string",
    "contact_source": "website"
  }}
]

Als er echt geen relevante personen gevonden worden, geef een lege array: []"""

    try:
        msg = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        json_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if json_match:
            contacts = json.loads(json_match.group())
            for c in contacts:
                c.setdefault("contact_name", "")
                c.setdefault("contact_title", "")
                c.setdefault("linkedin_url", "")
                c.setdefault("contact_source", "website")
                c.setdefault("contact_email", "")
                c.setdefault("email_confidence", "")
            return contacts
    except Exception as e:
        log.warning("  Claude contact extraction error for %s: %s", company_name, e)

    return []


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


def load_existing_contacts(path: Path) -> dict:
    contacts = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                contacts[row["company_name"]] = row
    return contacts


def load_csr_analysis() -> List[dict]:
    if not config.CSR_ANALYSIS_CSV.exists():
        return []
    with open(config.CSR_ANALYSIS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_companies() -> Dict[str, dict]:
    """Return dict of company_name -> company_data."""
    if not config.COMPANIES_CSV.exists():
        return {}
    with open(config.COMPANIES_CSV, newline="", encoding="utf-8") as f:
        return {r["company_name"]: dict(r) for r in csv.DictReader(f)}


def find_contact_in_csr_text(company_name: str, csr_by_company: Dict[str, dict]) -> Optional[dict]:
    """
    Ask Claude to extract a named contact from the CSR report text already
    fetched in stage 2. Returns a contact dict or None.
    """
    if not HAS_ANTHROPIC or not config.ANTHROPIC_API_KEY:
        return None
    csr_row = csr_by_company.get(company_name, {})
    key_quotes = csr_row.get("key_quotes", "")
    analysis = csr_row.get("analysis_summary", "")
    csr_url = csr_row.get("csr_url", "")
    if not key_quotes and not analysis:
        return None

    # Re-fetch a snippet of the CSR page to give Claude more text
    text_for_claude = f"Bedrijf: {company_name}\nCSR URL: {csr_url}\nSamenvatting: {analysis}\nCitaten: {key_quotes}"

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    prompt = f"""Je krijgt informatie van het CSR/duurzaamheidsrapport van {company_name}.
Zoek naar een specifiek persoon die wordt genoemd met naam én functie — bij voorkeur duurzaamheidsmanager, MVO-manager, communicatiemanager, HR-manager, of directeur/eigenaar.

TEKST:
{text_for_claude}

Als je een persoon vindt, geef dan ALLEEN deze JSON terug:
{{"name": "Voornaam Achternaam", "title": "Functietitel"}}

Als er geen specifiek persoon met naam wordt genoemd, geef dan terug:
{{"name": "", "title": ""}}

Geef ALLEEN geldige JSON, niets anders."""

    try:
        msg = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
            name = result.get("name", "").strip()
            title = result.get("title", "").strip()
            if name and len(name.split()) >= 2:
                return {
                    "contact_name": name,
                    "contact_title": title,
                    "contact_email": "",
                    "email_confidence": "",
                    "linkedin_url": "",
                    "contact_source": "csr_report",
                }
    except Exception as e:
        log.debug("  CSR contact extraction error: %s", e)
    return None


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run(limit: Optional[int] = None, dry_run: bool = False) -> List[dict]:
    log.info("Stage 3: Finding contacts")

    csr_data = load_csr_analysis()
    if not csr_data:
        log.error("csr_analysis.csv not found - run stage 2 first")
        return []

    companies_map = load_companies()

    # Filter to relevant companies — exclude any already in active talks
    relevant = [
        r for r in csr_data
        if int(r.get("relevance_score", 0)) >= config.RELEVANCE_THRESHOLD
        and r.get("company_name") not in ALREADY_IN_TALKS
    ]
    log.info(
        "Found %d relevant companies (score >= %d) out of %d total",
        len(relevant), config.RELEVANCE_THRESHOLD, len(csr_data),
    )

    if limit:
        relevant = relevant[:limit]

    # Build CSR text lookup for contact extraction
    csr_by_company = {r["company_name"]: r for r in csr_data}

    existing = load_existing_contacts(config.CONTACTS_CSV)
    progress = load_progress()

    session = requests.Session()
    session.headers.update(HEADERS)

    all_contacts = []

    for i, csr_row in enumerate(relevant, 1):
        name = csr_row["company_name"]
        company_data = companies_map.get(name, {})
        website = company_data.get("website", "")
        ownership = company_data.get("ownership", "family / private")
        domain = get_domain(website) if website else ""

        log.info("[%d/%d] Finding contacts for: %s", i, len(relevant), name)

        if name in existing:
            log.info("  Skipping (already processed)")
            all_contacts.append(existing[name])
            continue

        contacts_found = []

        if dry_run:
            for mock in MOCK_CONTACTS:
                row = {
                    "company_name": name,
                    "website": website,
                    "ownership": ownership,
                    "relevance_score": csr_row.get("relevance_score", ""),
                    **mock,
                }
                contacts_found.append(row)
            log.info("  [DRY-RUN] Added mock contact")
        else:
            # 0. Try extracting contact from CSR report text (free, no web requests)
            csr_contact = find_contact_in_csr_text(name, csr_by_company)
            if csr_contact:
                log.info("  CSR report contact: %s (%s)", csr_contact.get("contact_name"), csr_contact.get("contact_title"))
                contacts_found.append(csr_contact)

            # 1. Bing + Claude smart search (skip if CSR already found a contact)
            bing_candidate = None
            if not contacts_found:
                log.info("  Trying Bing + Claude smart search...")
                bing_candidate = find_contact_via_bing(name, website, session)
            if bing_candidate:
                contact = {
                    "contact_name": bing_candidate.get("contact_name", ""),
                    "contact_title": bing_candidate.get("contact_title", ""),
                    "contact_email": "",
                    "email_confidence": "",
                    "linkedin_url": bing_candidate.get("linkedin_url", ""),
                    "contact_source": bing_candidate.get("contact_source", "bing_claude"),
                }
                contacts_found.append(contact)
                log.info(
                    "  Bing+Claude found: %s (%s)",
                    contact["contact_name"], contact["contact_title"],
                )

            # 2. Try Hunter.io (regardless of LinkedIn result — may add email)
            if config.HUNTER_API_KEY and domain:
                hunter_contacts = hunter_lookup(name, domain, session)
                if hunter_contacts:
                    log.info("  Hunter.io found %d contacts", len(hunter_contacts))
                    # If we already have a LinkedIn contact, enrich with Hunter email if name matches
                    if contacts_found and hunter_contacts:
                        li_name = contacts_found[0].get("contact_name", "").lower()
                        for hc in hunter_contacts:
                            hc_name = hc.get("contact_name", "").lower()
                            if li_name and hc_name and (
                                li_name.split()[0] in hc_name or hc_name.split()[0] in li_name
                            ):
                                contacts_found[0]["contact_email"] = hc.get("contact_email", "")
                                contacts_found[0]["email_confidence"] = hc.get("email_confidence", "")
                                log.info("  Enriched LinkedIn contact with Hunter.io email")
                                break
                    else:
                        contacts_found.extend(hunter_contacts)
                time.sleep(config.REQUEST_DELAY)

            # 3. Fallback: scrape company website team pages (if no LinkedIn result)
            if not contacts_found:
                log.info("  Falling back to website team page scraping...")
                page_texts = []
                linkedin_links = []
                if website:
                    team_pages = find_team_pages(website, session)
                    for page_url in team_pages[:3]:
                        resp = try_url(page_url, session)
                        if resp:
                            page_texts.append(extract_page_text(resp))
                            linkedin_links.extend(extract_linkedin_links(resp))
                        time.sleep(config.REQUEST_DELAY)

                # Also check CSR page for team info
                csr_url = csr_row.get("csr_url", "")
                if csr_url:
                    resp = try_url(csr_url, session)
                    if resp:
                        page_texts.append(extract_page_text(resp))
                        linkedin_links.extend(extract_linkedin_links(resp))

                # Claude extraction from pages
                if page_texts:
                    time.sleep(config.API_DELAY)
                    claude_contacts = extract_contacts_with_claude(
                        name, page_texts, list(set(linkedin_links)), dry_run=dry_run
                    )
                    contacts_found.extend(claude_contacts)
                    log.info("  Claude extracted %d contacts", len(claude_contacts))

            # 4. Add email guesses for contacts without emails
            for contact in contacts_found:
                # Do NOT guess email patterns — guessed emails bounce and hurt deliverability
                # Only real emails from Hunter.io are kept
                pass

        # If still no contacts found, create a generic entry
        if not contacts_found:
            log.warning("  No contacts found for %s", name)
            contacts_found = [{
                "contact_name": "",
                "contact_title": "Sustainability Manager (not found)",
                "contact_email": f"sustainability@{domain}" if domain else "",
                "email_confidence": "generic",
                "linkedin_url": "",
                "contact_source": "generic fallback",
            }]

        # Build final rows
        for contact in contacts_found[:2]:  # max 2 contacts per company
            row = {
                "company_name": name,
                "website": website,
                "ownership": ownership,
                "relevance_score": csr_row.get("relevance_score", ""),
                "contact_name": contact.get("contact_name", ""),
                "contact_title": contact.get("contact_title", ""),
                "contact_email": contact.get("contact_email", ""),
                "email_confidence": contact.get("email_confidence", ""),
                "linkedin_url": contact.get("linkedin_url", ""),
                "contact_source": contact.get("contact_source", ""),
            }
            all_contacts.append(row)

        progress[name] = {"stage": "contacts_found", "num_contacts": len(contacts_found)}
        save_progress(progress)
        time.sleep(config.REQUEST_DELAY)

    # Save contacts CSV
    config.DATA_DIR.mkdir(exist_ok=True)
    with open(config.CONTACTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CONTACTS_FIELDS)
        writer.writeheader()
        for row in all_contacts:
            writer.writerow({k: row.get(k, "") for k in CONTACTS_FIELDS})

    log.info("Stage 3 complete: %d contacts saved to %s", len(all_contacts), config.CONTACTS_CSV)
    return all_contacts


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Stage 3: Find contacts")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)
