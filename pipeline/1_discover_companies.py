"""
Stage 1: Discover Dutch B2B companies.
Uses a curated seed list of 100 real mid-to-high level Netherlands companies.
Saves to data/companies.csv
"""
import sys
import csv
import json
import time
import logging
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Allow running from project root or pipeline/ dir
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Curated seed list of 100 real Dutch mid-to-large companies
# ---------------------------------------------------------------------------
SEED_COMPANIES = [
    # Energy & Utilities
    {"company_name": "Shell Netherlands", "website": "https://www.shell.nl", "industry": "Energy"},
    {"company_name": "Eneco", "website": "https://www.eneco.nl", "industry": "Energy"},
    {"company_name": "Vattenfall Netherlands", "website": "https://www.vattenfall.nl", "industry": "Energy"},
    {"company_name": "TenneT", "website": "https://www.tennet.eu", "industry": "Energy / Grid"},
    {"company_name": "Urenco", "website": "https://www.urenco.com", "industry": "Nuclear Energy"},
    {"company_name": "OCI N.V.", "website": "https://www.oci.nl", "industry": "Chemicals / Fertilizers"},
    # Chemicals & Materials
    {"company_name": "AkzoNobel", "website": "https://www.akzonobel.com", "industry": "Paints & Coatings"},
    {"company_name": "DSM-Firmenich", "website": "https://www.dsm-firmenich.com", "industry": "Life Sciences / Nutrition"},
    {"company_name": "Corbion", "website": "https://www.corbion.com", "industry": "Biobased Chemicals"},
    {"company_name": "Covestro Netherlands", "website": "https://www.covestro.com", "industry": "Polymers"},
    {"company_name": "LyondellBasell Netherlands", "website": "https://www.lyondellbasell.com", "industry": "Petrochemicals"},
    {"company_name": "DOW Benelux", "website": "https://www.dow.com", "industry": "Specialty Chemicals"},
    {"company_name": "Perstorp Netherlands", "website": "https://www.perstorp.com", "industry": "Specialty Chemicals"},
    {"company_name": "Renewi", "website": "https://www.renewi.com", "industry": "Waste Management"},
    # Food & Agriculture
    {"company_name": "Heineken", "website": "https://www.heineken.com", "industry": "Beverages"},
    {"company_name": "Unilever Netherlands", "website": "https://www.unilever.nl", "industry": "FMCG"},
    {"company_name": "FrieslandCampina", "website": "https://www.frieslandcampina.com", "industry": "Dairy"},
    {"company_name": "Wessanen", "website": "https://www.wessanen.com", "industry": "Organic Food"},
    {"company_name": "Royal FloraHolland", "website": "https://www.royalfloraholland.com", "industry": "Horticulture"},
    {"company_name": "Albert Heijn", "website": "https://www.ah.nl", "industry": "Retail / Grocery"},
    {"company_name": "Ahold Delhaize", "website": "https://www.aholddelhaize.com", "industry": "Retail / Grocery"},
    {"company_name": "Jumbo Supermarkten", "website": "https://www.jumbo.com", "industry": "Retail / Grocery"},
    # Technology & IT
    {"company_name": "Philips", "website": "https://www.philips.nl", "industry": "Technology / Healthcare"},
    {"company_name": "ASML", "website": "https://www.asml.com", "industry": "Semiconductor Equipment"},
    {"company_name": "TomTom", "website": "https://www.tomtom.com", "industry": "Navigation Technology"},
    {"company_name": "Exact Software", "website": "https://www.exact.com", "industry": "Business Software"},
    {"company_name": "AFAS Software", "website": "https://www.afas.nl", "industry": "Business Software"},
    {"company_name": "Nedap", "website": "https://www.nedap.com", "industry": "Technology"},
    {"company_name": "Adyen", "website": "https://www.adyen.com", "industry": "Fintech / Payments"},
    {"company_name": "Booking.com", "website": "https://www.booking.com", "industry": "Travel Technology"},
    {"company_name": "Coolblue", "website": "https://www.coolblue.nl", "industry": "E-commerce / Electronics"},
    {"company_name": "Bol.com", "website": "https://www.bol.com", "industry": "E-commerce"},
    # Consulting & Professional Services
    {"company_name": "Capgemini Netherlands", "website": "https://www.capgemini.com/nl-nl", "industry": "IT Consulting"},
    {"company_name": "Atos Netherlands", "website": "https://atos.net/nl", "industry": "IT Services"},
    {"company_name": "CGI Netherlands", "website": "https://www.cgi.com/nl/nl", "industry": "IT Services"},
    {"company_name": "Accenture Netherlands", "website": "https://www.accenture.com/nl-nl", "industry": "Management Consulting"},
    {"company_name": "Deloitte Netherlands", "website": "https://www2.deloitte.com/nl", "industry": "Professional Services"},
    {"company_name": "PwC Netherlands", "website": "https://www.pwc.nl", "industry": "Professional Services"},
    {"company_name": "KPMG Netherlands", "website": "https://home.kpmg/nl", "industry": "Professional Services"},
    {"company_name": "EY Netherlands", "website": "https://www.ey.com/nl_nl", "industry": "Professional Services"},
    {"company_name": "McKinsey Netherlands", "website": "https://www.mckinsey.com/nl", "industry": "Management Consulting"},
    {"company_name": "BCG Netherlands", "website": "https://www.bcg.com/nl-nl", "industry": "Management Consulting"},
    {"company_name": "Roland Berger Netherlands", "website": "https://www.rolandberger.com/nl", "industry": "Management Consulting"},
    {"company_name": "Arcadis", "website": "https://www.arcadis.com/nl", "industry": "Engineering Consulting"},
    {"company_name": "Ordina", "website": "https://www.ordina.com", "industry": "IT Consulting"},
    # Finance & Banking
    {"company_name": "ABN AMRO", "website": "https://www.abnamro.nl", "industry": "Banking"},
    {"company_name": "ING Bank", "website": "https://www.ing.nl", "industry": "Banking"},
    {"company_name": "Rabobank", "website": "https://www.rabobank.nl", "industry": "Banking / Cooperative"},
    {"company_name": "Triodos Bank", "website": "https://www.triodos.nl", "industry": "Sustainable Banking"},
    {"company_name": "ASN Bank", "website": "https://www.asnbank.nl", "industry": "Sustainable Banking"},
    {"company_name": "SNS Bank", "website": "https://www.snsbank.nl", "industry": "Retail Banking"},
    {"company_name": "Bunq", "website": "https://www.bunq.com", "industry": "Neobank / Fintech"},
    {"company_name": "Aegon Netherlands", "website": "https://www.aegon.nl", "industry": "Insurance / Finance"},
    {"company_name": "NN Group", "website": "https://www.nn-group.com", "industry": "Insurance / Finance"},
    {"company_name": "Achmea", "website": "https://www.achmea.nl", "industry": "Insurance"},
    {"company_name": "Wolters Kluwer", "website": "https://www.wolterskluwer.com/nl-nl", "industry": "Information Services"},
    # Logistics & Transport
    {"company_name": "PostNL", "website": "https://www.postnl.nl", "industry": "Postal / Logistics"},
    {"company_name": "NS (Nederlandse Spoorwegen)", "website": "https://www.ns.nl", "industry": "Public Transport"},
    {"company_name": "ProRail", "website": "https://www.prorail.nl", "industry": "Rail Infrastructure"},
    {"company_name": "Schiphol Group", "website": "https://www.schiphol.nl", "industry": "Aviation / Airport"},
    {"company_name": "Rotterdam Port Authority", "website": "https://www.portofrotterdam.com", "industry": "Port / Logistics"},
    {"company_name": "Port of Amsterdam", "website": "https://www.portofamsterdam.com", "industry": "Port / Logistics"},
    {"company_name": "Vopak", "website": "https://www.vopak.com", "industry": "Tank Storage / Logistics"},
    {"company_name": "Boskalis", "website": "https://www.boskalis.com", "industry": "Marine Services / Dredging"},
    {"company_name": "Mammoet", "website": "https://www.mammoet.com", "industry": "Heavy Lifting / Transport"},
    {"company_name": "Fugro", "website": "https://www.fugro.com", "industry": "Geotechnical Services"},
    {"company_name": "SBM Offshore", "website": "https://www.sbmoffshore.com", "industry": "Offshore Energy"},
    {"company_name": "Randstad", "website": "https://www.randstad.nl", "industry": "Staffing / HR"},
    {"company_name": "Connexxion", "website": "https://www.connexxion.nl", "industry": "Public Transport"},
    {"company_name": "Arriva Netherlands", "website": "https://www.arriva.nl", "industry": "Public Transport"},
    {"company_name": "Transdev Netherlands", "website": "https://www.transdev.nl", "industry": "Public Transport"},
    # Construction & Real Estate
    {"company_name": "BAM Group", "website": "https://www.bam.com/nl", "industry": "Construction"},
    {"company_name": "Vanderlande", "website": "https://www.vanderlande.com", "industry": "Logistics Automation"},
    {"company_name": "SHV Holdings", "website": "https://www.shv.nl", "industry": "Diversified / Energy"},
    # Retail & Consumer
    {"company_name": "Rituals Cosmetics", "website": "https://www.rituals.com", "industry": "Beauty / Retail"},
    {"company_name": "HEMA", "website": "https://www.hema.nl", "industry": "Retail"},
    {"company_name": "Action", "website": "https://www.action.com/nl-nl", "industry": "Discount Retail"},
    {"company_name": "Makro Netherlands", "website": "https://www.makro.nl", "industry": "Wholesale / Retail"},
    {"company_name": "IKEA Netherlands", "website": "https://www.ikea.com/nl", "industry": "Furniture / Retail"},
    {"company_name": "H&M Netherlands", "website": "https://www.hm.com/nl", "industry": "Fashion Retail"},
    {"company_name": "Zara Netherlands", "website": "https://www.zara.com/nl", "industry": "Fashion Retail"},
    # Telecoms & Media
    {"company_name": "KPN", "website": "https://www.kpn.com", "industry": "Telecommunications"},
    {"company_name": "Tele2 Netherlands", "website": "https://www.tele2.nl", "industry": "Telecommunications"},
    {"company_name": "VodafoneZiggo", "website": "https://www.vodafone.nl", "industry": "Telecommunications"},
    # Healthcare & Life Sciences
    {"company_name": "Siemens Healthineers NL", "website": "https://www.siemens-healthineers.com/nl", "industry": "Medical Technology"},
    {"company_name": "Johnson Controls Netherlands", "website": "https://www.johnsoncontrols.com/nl", "industry": "Building Technology"},
    # Industrial & Engineering
    {"company_name": "Siemens Netherlands", "website": "https://new.siemens.com/nl", "industry": "Industrial Technology"},
    {"company_name": "ABB Netherlands", "website": "https://new.abb.com/nl", "industry": "Electrification / Automation"},
    {"company_name": "Schneider Electric NL", "website": "https://www.se.com/nl", "industry": "Energy Management"},
    {"company_name": "Honeywell Netherlands", "website": "https://www.honeywell.com/nl-nl", "industry": "Industrial Technology"},
    {"company_name": "Daikin Netherlands", "website": "https://www.daikin.nl", "industry": "HVAC / Climate Systems"},
    {"company_name": "GrandVision Netherlands", "website": "https://www.grandvision.com/nl", "industry": "Optical Retail"},
    # Additional notable companies
    {"company_name": "Polimoon", "website": "https://www.polimoon.com", "industry": "Plastic Packaging"},
    {"company_name": "Imtech", "website": "https://www.imtech.nl", "industry": "Technical Services"},
    {"company_name": "Lidl Netherlands", "website": "https://www.lidl.nl", "industry": "Discount Retail"},
    {"company_name": "Aldi Netherlands", "website": "https://www.aldi.nl", "industry": "Discount Retail"},
    {"company_name": "Dirk van den Broek", "website": "https://www.dirk.nl", "industry": "Retail / Grocery"},
    {"company_name": "Metro Netherlands", "website": "https://www.metro.nl", "industry": "Wholesale / Retail"},
    {"company_name": "eBay Netherlands", "website": "https://www.ebay.nl", "industry": "E-commerce"},
    {"company_name": "Amazon Netherlands", "website": "https://www.amazon.nl", "industry": "E-commerce"},
    {"company_name": "TripAdvisor Netherlands", "website": "https://www.tripadvisor.nl", "industry": "Travel Technology"},
]


def enrich_company(company: dict) -> dict:
    """Add country and size_estimate fields."""
    company["country"] = "NL"
    # Rough size estimate based on industry / known scale
    large_industries = {
        "Banking", "Insurance / Finance", "Energy", "Retail / Grocery", "Beverages",
        "FMCG", "Semiconductor Equipment", "Petrochemicals", "Telecommunications",
        "Postal / Logistics", "Professional Services",
    }
    if company["industry"] in large_industries:
        company["size_estimate"] = "large (>1000 employees)"
    else:
        company["size_estimate"] = "mid-to-large (250-1000 employees)"
    return company


def save_companies(companies: list[dict], path: Path, limit: int | None = None) -> None:
    path.parent.mkdir(exist_ok=True)
    if limit:
        companies = companies[:limit]
    fieldnames = ["company_name", "website", "industry", "size_estimate", "country"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in companies:
            writer.writerow({k: c.get(k, "") for k in fieldnames})
    log.info("Saved %d companies to %s", len(companies), path)


def run(limit: int | None = None, dry_run: bool = False) -> list[dict]:
    log.info("Stage 1: Discovering companies (seed list of %d)", len(SEED_COMPANIES))
    companies = [enrich_company(dict(c)) for c in SEED_COMPANIES]

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
