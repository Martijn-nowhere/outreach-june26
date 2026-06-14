"""
Stage 1: Discover Dutch B2B companies.
Uses a curated seed list of 100 real mid-to-high level Netherlands companies.
Saves to data/companies.csv
"""
import sys
from typing import Optional, List
import csv
import json
import time
import logging
from pathlib import Path

import re
import requests
from bs4 import BeautifulSoup

# Allow running from project root or pipeline/ dir
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

ABOUT_URL_PATTERNS = [
    "{base}/over-ons",
    "{base}/about-us",
    "{base}/about",
    "{base}/nl/over-ons",
]

NON_FAMILY_SIGNALS = [
    "private equity", "buyout", "beursgenoteerd", "stock exchange",
    "listed on", "genoteerd op", "asahi", "wyndham", "mexichem",
    "overgenomen door", "acquired by",
]


def check_company_fit(company_name: str, website: str, stated_ownership: str) -> bool:
    """
    Single Firecrawl + Claude check: is this company still a good fit?
    Criteria: family/founder-owned, not listed, 100–1000 employees.

    Returns True to keep the company, False to exclude.
    Falls back to True (keep) if Firecrawl/API is unavailable or page can't be fetched.
    """
    if not config.FIRECRAWL_API_KEY or not HAS_ANTHROPIC or not config.ANTHROPIC_API_KEY or not website:
        return True

    from urllib.parse import urlparse
    parsed = urlparse(website)
    base = f"{parsed.scheme}://{parsed.netloc}"

    page_text = ""
    for pattern in ABOUT_URL_PATTERNS:
        url = pattern.format(base=base)
        try:
            resp = requests.post(
                "https://api.firecrawl.dev/v1/scrape",
                json={"url": url, "formats": ["markdown"]},
                headers={
                    "Authorization": f"Bearer {config.FIRECRAWL_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=20,
            )
            if resp.status_code == 200:
                text = resp.json().get("data", {}).get("markdown", "") or ""
                if len(text.strip()) > 200:
                    page_text = text[:5000]
                    break
        except Exception as e:
            log.debug("  Firecrawl fit check error for %s: %s", url, e)
        time.sleep(0.4)

    if not page_text:
        return True  # can't verify — keep with benefit of doubt

    # Fast path: no red flags on page → keep without Claude call
    text_lower = page_text.lower()
    has_red_flag = any(signal in text_lower for signal in NON_FAMILY_SIGNALS)
    if not has_red_flag:
        return True

    log.info("  Red flag on page for %s — asking Claude...", company_name)
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    prompt = f"""Beoordeel of {company_name} een goede potentiële klant is voor School of Recycling (duurzaamheidseducatie voor medewerkers).

Ideale klant: familiebedrijf of founder-led, NIET beursgenoteerd, 100–1000 medewerkers.
Opgegeven eigendom: {stated_ownership}

WEBSITETEKST:
{page_text}

Geef ALLEEN deze JSON:
{{"keep": true/false, "reason": "één zin"}}

keep=false alleen als: beursgenoteerd / PE-owned / multinational / te groot (>2000 medewerkers) / NGO/stichting zonder budget.
Bij twijfel: keep=true.

Alleen geldige JSON."""

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
            keep = result.get("keep", True)
            reason = result.get("reason", "")
            log.info("  %s %s — %s", "KEEP" if keep else "SKIP", company_name, reason)
            return bool(keep)
    except Exception as e:
        log.debug("  Claude fit check error for %s: %s", company_name, e)

    return True


# ---------------------------------------------------------------------------
# Curated list of Dutch family-owned / founder-led mid-sized companies.
# Selection criteria:
#   - NOT stock-market listed (or founder still controls majority)
#   - Dutch roots, often regional HQ, community-oriented culture
#   - Mid-sized (roughly 100–2000 employees) — fast decision-making
#   - Industries with natural plastic/waste/sustainability exposure
# ---------------------------------------------------------------------------
SEED_COMPANIES = [
    # --- Food & Beverage ---
    {"company_name": "Hessing Supervers", "website": "https://www.hessingsupervers.nl", "industry": "Fresh Food / Packaging", "ownership": "family", "hometown": "Woerden"},
    {"company_name": "De Heus", "website": "https://www.deheus.com", "industry": "Animal Nutrition", "ownership": "family", "hometown": "Ede"},
    {"company_name": "Fresca Group", "website": "https://www.frescagroup.co.uk", "industry": "Fresh Produce / Packaging", "ownership": "family", "hometown": ""},
    {"company_name": "Zwanenberg Food Group", "website": "https://www.zwanenberg.nl", "industry": "Food / Meat", "ownership": "family", "hometown": "Almelo"},
    {"company_name": "Hak", "website": "https://www.hak.nl", "industry": "Food / Vegetable Preserves", "ownership": "family", "hometown": "Giessen"},
    {"company_name": "Bakker Bart", "website": "https://www.bakkerbart.nl", "industry": "Bakery Chain / FMCG", "ownership": "family", "hometown": ""},
    {"company_name": "Farm Frites", "website": "https://www.farmfrites.com", "industry": "Potato / Food Processing", "ownership": "family", "hometown": "Oudenhoorn"},
    {"company_name": "Zwietering Groep", "website": "https://www.zwietering.nl", "industry": "Food / Regional", "ownership": "family", "hometown": "Almelo"},
    {"company_name": "Koninklijke Vezet", "website": "https://www.vezet.nl", "industry": "Fresh-cut Vegetables / Food", "ownership": "family", "hometown": "Kampen"},

    # --- Packaging & Plastics (direct relevance to SoR) ---
    {"company_name": "Lankhorst Mouldings", "website": "https://www.lankhorst-mouldings.com", "industry": "Recycled Plastic Products", "ownership": "family", "hometown": "Sneek"},
    {"company_name": "Leer Kunststoffen", "website": "https://www.leerkunststoffen.nl", "industry": "Plastic Packaging", "ownership": "family", "hometown": ""},
    {"company_name": "DPG Packaging", "website": "https://www.dpg-group.nl", "industry": "Plastic / Packaging", "ownership": "family", "hometown": ""},
    {"company_name": "Naber Plastics", "website": "https://www.naberplastics.nl", "industry": "Plastic Processing", "ownership": "family", "hometown": ""},
    {"company_name": "Omniform Group", "website": "https://www.omniformgroup.com", "industry": "Packaging / Print", "ownership": "family", "hometown": ""},

    # --- Retail & Consumer ---
    {"company_name": "Shoeby Fashion", "website": "https://www.shoeby.nl", "industry": "Fashion Retail", "ownership": "family", "hometown": ""},
    {"company_name": "America Today", "website": "https://www.americatoday.nl", "industry": "Fashion Retail", "ownership": "family", "hometown": ""},
    {"company_name": "Kwantum", "website": "https://www.kwantum.nl", "industry": "Home Furnishings Retail", "ownership": "family", "hometown": "Tilburg"},
    {"company_name": "Leen Bakker", "website": "https://www.leenbakker.nl", "industry": "Home Furnishings Retail", "ownership": "family", "hometown": ""},
    {"company_name": "Bristol", "website": "https://www.bristol.nl", "industry": "Footwear Retail", "ownership": "family", "hometown": ""},
    {"company_name": "Auping", "website": "https://www.auping.nl", "industry": "Sustainable Furniture / Beds", "ownership": "family", "hometown": "Deventer"},

    # --- Construction & Real Estate ---
    {"company_name": "Dura Vermeer", "website": "https://www.duravermeer.nl", "industry": "Construction", "ownership": "family", "hometown": ""},
    {"company_name": "Van Wijnen", "website": "https://www.vanwijnen.nl", "industry": "Construction", "ownership": "family", "hometown": ""},
    {"company_name": "Volker Wessels", "website": "https://www.volkerwessels.com", "industry": "Construction", "ownership": "family (Wessels)", "hometown": "Amersfoort"},
    {"company_name": "Ter Steege Groep", "website": "https://www.tersteege.nl", "industry": "Construction / Real Estate", "ownership": "family", "hometown": "Rijssen"},
    {"company_name": "Aan de Stegge Twello", "website": "https://www.aandestegge.nl", "industry": "Construction", "ownership": "family", "hometown": "Twello"},

    # --- Agriculture & Horticulture ---
    {"company_name": "Koppert Biological Systems", "website": "https://www.koppert.nl", "industry": "Biological Crop Protection", "ownership": "family (Koppert)", "hometown": "Berkel en Rodenrijs"},
    {"company_name": "Priva", "website": "https://www.priva.com", "industry": "Greenhouse Technology", "ownership": "family", "hometown": "De Lier"},
    {"company_name": "Ridder Group", "website": "https://www.ridder.com", "industry": "Horticulture Technology", "ownership": "family", "hometown": "Rockanje"},
    {"company_name": "Rijk Zwaan", "website": "https://www.rijkzwaan.nl", "industry": "Vegetable Seeds", "ownership": "family", "hometown": "De Lier"},
    {"company_name": "Enza Zaden", "website": "https://www.enzazaden.nl", "industry": "Vegetable Seeds", "ownership": "family", "hometown": "Enkhuizen"},
    {"company_name": "HilverdaFlorist", "website": "https://www.hilverdaflorist.com", "industry": "Flowers / Horticulture", "ownership": "family", "hometown": "Hilversum"},

    # --- Technical & Industrial ---
    {"company_name": "Batenburg Techniek", "website": "https://www.batenburg.nl", "industry": "Technical Services", "ownership": "family", "hometown": "Rotterdam"},

    # --- Logistics & Transport ---
    {"company_name": "Bakker Logistiek", "website": "https://www.bakkerlogistiek.nl", "industry": "Fresh Logistics", "ownership": "family", "hometown": "Zeewolde"},
    {"company_name": "Nabuurs Transport", "website": "https://www.nabuurs.com", "industry": "Transport / Logistics", "ownership": "family", "hometown": "Veghel"},
    {"company_name": "Van der Helm Logistiek", "website": "https://www.vanderhelm.nl", "industry": "Logistics / Waste", "ownership": "family", "hometown": ""},
    {"company_name": "Ewals Cargo Care", "website": "https://www.ewals.com", "industry": "Logistics", "ownership": "family", "hometown": "Tegelen"},
    {"company_name": "Nijhof-Wassink", "website": "https://www.nijhof-wassink.nl", "industry": "Transport / Logistics", "ownership": "family", "hometown": "Twello"},
    {"company_name": "Muller Logistiek", "website": "https://www.mullerlogistics.nl", "industry": "Logistics / Transport", "ownership": "family", "hometown": "Hoofddorp"},
    {"company_name": "Bolk Transport", "website": "https://www.bolk.nl", "industry": "Transport / Logistics", "ownership": "family", "hometown": "Hengelo"},
    {"company_name": "Brouwer Transport", "website": "https://www.brouwertransport.nl", "industry": "Transport / Logistics", "ownership": "family", "hometown": "Genemuiden"},

    # --- Grocery & Food Retail ---
    {"company_name": "Jan Linders", "website": "https://www.janlinders.nl", "industry": "Retail / Grocery", "ownership": "family", "hometown": "Venlo"},
    {"company_name": "Poiesz Supermarkten", "website": "https://www.poiesz-supermarkten.nl", "industry": "Retail / Grocery", "ownership": "family", "hometown": "Sneek"},
    {"company_name": "Hoogvliet", "website": "https://www.hoogvliet.com", "industry": "Retail / Grocery", "ownership": "family", "hometown": "Gouda"},
    {"company_name": "Vomar", "website": "https://www.vomar.nl", "industry": "Retail / Grocery", "ownership": "family", "hometown": "Alkmaar"},

    # --- Technology & Software ---
    {"company_name": "AFAS Software", "website": "https://www.afas.nl", "industry": "Business Software", "ownership": "founder-led", "hometown": "Leusden"},

    # --- Hospitality & Travel ---
    {"company_name": "Corendon Hotels", "website": "https://www.corendon.nl", "industry": "Hospitality / Travel", "ownership": "founder-led", "hometown": ""},
    {"company_name": "Fletcher Hotels", "website": "https://www.fletcher.nl", "industry": "Hospitality", "ownership": "family", "hometown": ""},
    {"company_name": "Sunweb Group", "website": "https://www.sunwebgroup.com", "industry": "Travel / Tourism", "ownership": "private", "hometown": "Rotterdam"},
]


def enrich_company(company: dict) -> dict:
    """Add country, size_estimate, and ownership fields."""
    company["country"] = "NL"
    company.setdefault("ownership", "family / private")
    # Rough size estimate based on industry
    large_industries = {
        "Retail / Grocery", "Food Wholesale", "Insurance / Cooperative",
        "Construction", "Logistics", "Pharmaceutical Distribution",
    }
    if company["industry"] in large_industries:
        company["size_estimate"] = "large (>1000 employees)"
    else:
        company["size_estimate"] = "mid-sized (100-1000 employees)"
    return company


def save_companies(companies: List[dict], path: Path, limit: Optional[int] = None) -> None:
    path.parent.mkdir(exist_ok=True)
    if limit:
        companies = companies[:limit]
    fieldnames = ["company_name", "website", "industry", "ownership", "hometown", "size_estimate", "country"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in companies:
            writer.writerow({k: c.get(k, "") for k in fieldnames})
    log.info("Saved %d companies to %s", len(companies), path)


def run(limit: Optional[int] = None, dry_run: bool = False) -> List[dict]:
    log.info("Stage 1: Discovering companies (seed list of %d)", len(SEED_COMPANIES))
    companies = [enrich_company(dict(c)) for c in SEED_COMPANIES]

    # Remove any company already in active conversation
    before = len(companies)
    companies = [c for c in companies if c["company_name"] not in ALREADY_IN_TALKS]
    skipped = before - len(companies)
    if skipped:
        log.info("Skipped %d companies already in active talks: %s", skipped,
                 [c["company_name"] for c in SEED_COMPANIES if c["company_name"] in ALREADY_IN_TALKS])

    # Remove companies that are no longer family/founder-owned
    NON_FAMILY_KEYWORDS = [
        "private equity", "listed", "asahi", "wyndham", "mexichem",
        "stock market", "beursgenoteerd", "pe-backed",
    ]
    before = len(companies)
    companies = [
        c for c in companies
        if not any(kw in c.get("ownership", "").lower() for kw in NON_FAMILY_KEYWORDS)
    ]
    removed = before - len(companies)
    if removed:
        log.info("Removed %d non-family/PE-owned companies", removed)

    # Firecrawl fit check — family-owned, not listed, 100–1000 employees
    if config.FIRECRAWL_API_KEY and not dry_run:
        log.info("Running Firecrawl company fit check...")
        before_fit = len(companies)
        companies = [
            c for c in companies
            if check_company_fit(c["company_name"], c.get("website", ""), c.get("ownership", ""))
        ]
        removed_fit = before_fit - len(companies)
        if removed_fit:
            log.info("Fit check removed %d companies", removed_fit)

    if limit:
        companies = companies[:limit]
        log.info("Limited to %d companies for this run", limit)

    save_companies(companies, config.COMPANIES_CSV)

    # Update progress tracker
    progress = _load_progress()
    for c in companies:
        name = c["company_name"]
        if name not in progress:
            progress[name] = {"stage": "discovered"}
    _save_progress(progress)

    log.info("Stage 1 complete: %d companies saved", len(companies))
    return companies


def _load_progress() -> dict:
    if config.PROGRESS_JSON.exists():
        with open(config.PROGRESS_JSON) as f:
            return json.load(f)
    return {}


def _save_progress(progress: dict) -> None:
    with open(config.PROGRESS_JSON, "w") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Stage 1: Discover companies")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)
