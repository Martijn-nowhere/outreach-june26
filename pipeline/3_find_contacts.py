"""
Stage 3: Find contact persons for relevant companies.
For each company with relevance_score >= threshold:
  - Searches company website for sustainability/CSR team pages
  - Uses Claude to extract contact names and titles
  - Attempts email pattern guessing or Hunter.io lookup
Saves to data/contacts.csv
"""
import sys
import csv
import json
import time
import logging
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

# Titles to target (Dutch and English)
TARGET_TITLES = [
    "duurzaamheidsmanager", "duurzaamheid manager",
    "sustainability manager", "sustainability director",
    "csr manager", "csr director", "csr lead",
    "mvo manager", "mvo coordinator",
    "corporate responsibility",
    "head of sustainability", "director sustainability",
    "manager duurzaamheid", "hoofd duurzaamheid",
    "environmental manager", "climate manager",
    "impact manager", "impact director",
    "ESG manager", "ESG director",
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


def try_url(url: str, session: requests.Session, timeout: int = 8) -> requests.Response | None:
    try:
        resp = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 200:
            return resp
    except Exception:
        pass
    return None


def find_team_pages(website: str, session: requests.Session) -> list[str]:
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


def extract_linkedin_links(resp: requests.Response) -> list[str]:
    """Find LinkedIn profile URLs on a page."""
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "linkedin.com/in/" in href:
            links.append(href)
    return list(set(links))


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def get_domain(website: str) -> str:
    """Extract domain from website URL."""
    parsed = urlparse(website)
    netloc = parsed.netloc.lower()
    # Strip www.
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def guess_email_patterns(first: str, last: str, domain: str) -> list[tuple[str, str]]:
    """
    Generate common corporate email patterns.
    Returns list of (email, pattern_name) tuples.
    """
    first = first.lower().strip()
    last = last.lower().strip()
    # Handle compound last names
    last_clean = last.replace(" ", "").replace("-", "")
    first_initial = first[0] if first else ""
    last_initial = last[0] if last else ""

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


def hunter_lookup(company_name: str, domain: str, session: requests.Session) -> list[dict]:
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
    page_texts: list[str],
    linkedin_links: list[str],
    dry_run: bool = False,
) -> list[dict]:
    """Use Claude to extract contact persons from team page text."""
    if dry_run or not HAS_ANTHROPIC or not config.ANTHROPIC_API_KEY:
        return MOCK_CONTACTS.copy()

    combined_text = "\n\n---\n\n".join(page_texts)[:6000]
    linkedin_text = "\n".join(linkedin_links[:10]) if linkedin_links else "geen LinkedIn links gevonden"

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    prompt = f"""Analyseer de volgende tekst van de website van **{company_name}** en zoek naar contactpersonen die verantwoordelijk zijn voor duurzaamheid, CSR, MVO, of milieu.

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

Geef een JSON array met maximaal 3 meest relevante contactpersonen. Als er geen directe match is, neem dan de meest senior persoon op het gebied van duurzaamheid of een algemeen directeur/manager.

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
            # Ensure required fields
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


def load_csr_analysis() -> list[dict]:
    if not config.CSR_ANALYSIS_CSV.exists():
        return []
    with open(config.CSR_ANALYSIS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_companies() -> dict:
    """Return dict of company_name -> company_data."""
    if not config.COMPANIES_CSV.exists():
        return {}
    with open(config.COMPANIES_CSV, newline="", encoding="utf-8") as f:
        return {r["company_name"]: dict(r) for r in csv.DictReader(f)}


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run(limit: int | None = None, dry_run: bool = False) -> list[dict]:
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
            # 1. Try Hunter.io
            if config.HUNTER_API_KEY and domain:
                hunter_contacts = hunter_lookup(name, domain, session)
                if hunter_contacts:
                    log.info("  Hunter.io found %d contacts", len(hunter_contacts))
                    contacts_found.extend(hunter_contacts)
                time.sleep(config.REQUEST_DELAY)

            # 2. Scrape team/about pages
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

            # 3. Claude extraction from pages
            if page_texts and not contacts_found:
                time.sleep(config.API_DELAY)
                claude_contacts = extract_contacts_with_claude(
                    name, page_texts, list(set(linkedin_links)), dry_run=dry_run
                )
                contacts_found.extend(claude_contacts)
                log.info("  Claude extracted %d contacts", len(claude_contacts))

            # 4. Add email guesses for contacts without emails
            for contact in contacts_found:
                if not contact.get("contact_email") and domain:
                    cname = contact.get("contact_name", "")
                    parts = cname.strip().split()
                    if len(parts) >= 2:
                        first, last = parts[0], parts[-1]
                        patterns = guess_email_patterns(first, last, domain)
                        if patterns:
                            # Use first (most common) pattern
                            contact["contact_email"] = patterns[0][0]
                            contact["email_confidence"] = f"pattern:{patterns[0][1]}"

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
