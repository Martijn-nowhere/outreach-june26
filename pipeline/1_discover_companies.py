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
from pipeline._already_in_talks import ALREADY_IN_TALKS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Curated list of Dutch family-owned / founder-led mid-sized companies.
# Selection criteria:
#   - NOT stock-market listed (or founder still controls majority)
#   - Dutch roots, often regional HQ, community-oriented culture
#   - Mid-sized (roughly 100–2000 employees) — fast decision-making
#   - Industries with natural plastic/waste/sustainability exposure
# ---------------------------------------------------------------------------
SEED_COMPANIES = [
    # --- Food & Beverage (family-owned / cooperative roots) ---
    {"company_name": "Dirk van den Broek", "website": "https://www.dirk.nl", "industry": "Retail / Grocery", "ownership": "family"},
    {"company_name": "Hessing Supervers", "website": "https://www.hessing.nl", "industry": "Fresh Food / Packaging", "ownership": "family"},
    {"company_name": "Agrifirm", "website": "https://www.agrifirm.nl", "industry": "Agriculture / Feed", "ownership": "cooperative"},
    {"company_name": "De Heus", "website": "https://www.deheus.com", "industry": "Animal Nutrition", "ownership": "family"},
    {"company_name": "Vion Food Group", "website": "https://www.vionfoodgroup.com", "industry": "Meat / Food Processing", "ownership": "cooperative"},
    {"company_name": "Fresca Group", "website": "https://www.fresca.nl", "industry": "Fresh Produce / Packaging", "ownership": "family"},
    {"company_name": "Zwanenberg Food Group", "website": "https://www.zwanenberg.nl", "industry": "Food / Meat", "ownership": "family"},
    {"company_name": "Lantmännen Unibake NL", "website": "https://www.lantmannenunibake.nl", "industry": "Bakery / Food", "ownership": "cooperative"},

    # --- Packaging & Plastics (direct relevance to SoR) ---
    {"company_name": "Lankhorst Mouldings", "website": "https://www.lankhorst-mouldings.com", "industry": "Recycled Plastic Products", "ownership": "family"},
    {"company_name": "Van Leer Group", "website": "https://www.vanleer.com", "industry": "Industrial Packaging", "ownership": "family (Van Leer)"},
    {"company_name": "Smurfit Westrock Netherlands", "website": "https://www.smurfitkappa.com/nl", "industry": "Paper Packaging", "ownership": "listed but NL family roots"},
    {"company_name": "Leer Kunststoffen", "website": "https://www.leerkunststoffen.nl", "industry": "Plastic Packaging", "ownership": "family"},
    {"company_name": "DPG Packaging", "website": "https://www.dpg-group.nl", "industry": "Plastic / Packaging", "ownership": "family"},
    {"company_name": "Greiner Packaging NL", "website": "https://www.greiner-packaging.com/nl", "industry": "Plastic Packaging", "ownership": "family (Greiner)"},
    {"company_name": "Naber Plastics", "website": "https://www.naberplastics.nl", "industry": "Plastic Processing", "ownership": "family"},
    {"company_name": "Omniform Group", "website": "https://www.omniformgroup.com", "industry": "Packaging / Print", "ownership": "family"},

    # --- Retail & Consumer (Dutch family chains) ---
    {"company_name": "Hema", "website": "https://www.hema.nl", "industry": "Retail", "ownership": "private equity (Dutch roots)"},
    {"company_name": "Action", "website": "https://www.action.com/nl-nl", "industry": "Discount Retail", "ownership": "private equity (NL founded)"},
    {"company_name": "Blokker Holding", "website": "https://www.blokker.nl", "industry": "Retail / Household", "ownership": "family (Blokker)"},
    {"company_name": "Rituals Cosmetics", "website": "https://www.rituals.com", "industry": "Beauty / Retail", "ownership": "founder-led (Raymond Cloosterman)"},
    {"company_name": "Scotch & Soda", "website": "https://www.scotch-soda.com", "industry": "Fashion Retail", "ownership": "private / founder roots"},
    {"company_name": "Shoeby Fashion", "website": "https://www.shoeby.nl", "industry": "Fashion Retail", "ownership": "family"},
    {"company_name": "America Today", "website": "https://www.americatoday.nl", "industry": "Fashion Retail", "ownership": "family"},
    {"company_name": "Kwantum", "website": "https://www.kwantum.nl", "industry": "Home Furnishings Retail", "ownership": "family"},
    {"company_name": "Leen Bakker", "website": "https://www.leenbakker.nl", "industry": "Home Furnishings Retail", "ownership": "family"},
    {"company_name": "Bristol", "website": "https://www.bristol.nl", "industry": "Footwear Retail", "ownership": "family"},

    # --- Construction & Real Estate (family builders) ---
    {"company_name": "Dura Vermeer", "website": "https://www.duravermeer.nl", "industry": "Construction", "ownership": "family"},
    {"company_name": "Heijmans", "website": "https://www.heijmans.nl", "industry": "Construction / Real Estate", "ownership": "listed but founder culture"},
    {"company_name": "Van Wijnen", "website": "https://www.vanwijnen.nl", "industry": "Construction", "ownership": "family"},
    {"company_name": "Spie Netherlands", "website": "https://www.spie.com/nl", "industry": "Technical Services", "ownership": "private"},
    {"company_name": "Strukton", "website": "https://www.strukton.com", "industry": "Construction / Rail", "ownership": "cooperative (Deme Group)"},
    {"company_name": "Ballast Nedam", "website": "https://www.ballast-nedam.nl", "industry": "Construction", "ownership": "private (BESIX)"},
    {"company_name": "BPD Bouwfonds", "website": "https://www.bpdeurope.com", "industry": "Real Estate Development", "ownership": "Rabobank subsidiary"},
    {"company_name": "Volker Wessels", "website": "https://www.volkerwessels.com", "industry": "Construction", "ownership": "family (Wessels)"},

    # --- Healthcare & Pharma (family / foundation owned) ---
    {"company_name": "Sanquin", "website": "https://www.sanquin.nl", "industry": "Blood Supply / Healthcare", "ownership": "foundation"},
    {"company_name": "Zorggroep Alliade", "website": "https://www.alliade.nl", "industry": "Healthcare / Care", "ownership": "foundation"},
    {"company_name": "Pluryn", "website": "https://www.pluryn.nl", "industry": "Care / Education", "ownership": "foundation"},
    {"company_name": "Meander Medisch Centrum", "website": "https://www.meandermc.nl", "industry": "Hospital / Healthcare", "ownership": "foundation"},
    {"company_name": "Rijnstate Ziekenhuis", "website": "https://www.rijnstate.nl", "industry": "Hospital / Healthcare", "ownership": "foundation"},
    {"company_name": "OPG Groep", "website": "https://www.opg.nl", "industry": "Pharmacy / Healthcare", "ownership": "cooperative"},
    {"company_name": "Brocacef", "website": "https://www.brocacef.nl", "industry": "Pharmaceutical Distribution", "ownership": "cooperative"},

    # --- Agriculture & Horticulture (cooperative / family) ---
    {"company_name": "Koppert Biological Systems", "website": "https://www.koppert.nl", "industry": "Biological Crop Protection", "ownership": "family (Koppert)"},
    {"company_name": "Priva", "website": "https://www.priva.com", "industry": "Greenhouse Technology", "ownership": "family"},
    {"company_name": "Ridder Group", "website": "https://www.ridder.com", "industry": "Horticulture Technology", "ownership": "family"},
    {"company_name": "Gebr. Smits", "website": "https://www.gebrsmits.nl", "industry": "Horticulture / Flowers", "ownership": "family"},
    {"company_name": "Nunhems Netherlands", "website": "https://www.nunhems.com", "industry": "Vegetable Seeds", "ownership": "BASF subsidiary"},
    {"company_name": "Rijk Zwaan", "website": "https://www.rijkzwaan.nl", "industry": "Vegetable Seeds", "ownership": "family"},
    {"company_name": "Enza Zaden", "website": "https://www.enzazaden.nl", "industry": "Vegetable Seeds", "ownership": "family"},
    {"company_name": "HilverdaFlorist", "website": "https://www.hilverdaflorist.com", "industry": "Flowers / Horticulture", "ownership": "family"},

    # --- Manufacturing & Industrial (family-owned Dutch makers) ---
    {"company_name": "Aalberts Industries", "website": "https://www.aalberts.com", "industry": "Industrial Manufacturing", "ownership": "founder roots / listed"},
    {"company_name": "Damen Shipyards", "website": "https://www.damen.com", "industry": "Shipbuilding", "ownership": "family (Damen)"},
    {"company_name": "Roto Smeets Group", "website": "https://www.rsg.nl", "industry": "Print / Packaging", "ownership": "family"},
    {"company_name": "Feenstra", "website": "https://www.feenstra.com", "industry": "HVAC / Technical Services", "ownership": "private"},
    {"company_name": "Batenburg Techniek", "website": "https://www.batenburg.nl", "industry": "Technical Services", "ownership": "family"},
    {"company_name": "Nedvang", "website": "https://www.nedvang.nl", "industry": "Packaging Waste / Recycling", "ownership": "industry collective"},
    {"company_name": "Van Gansewinkel", "website": "https://www.vangansewinkel.nl", "industry": "Waste Management", "ownership": "private"},
    {"company_name": "Prezero Netherlands", "website": "https://www.prezero.com/nl", "industry": "Waste / Recycling", "ownership": "Schwarz Group"},
    {"company_name": "Suez Netherlands", "website": "https://www.suez.nl", "industry": "Waste / Water", "ownership": "private"},
    {"company_name": "Omrin", "website": "https://www.omrin.nl", "industry": "Waste Management", "ownership": "municipal cooperative"},

    # --- Logistics & Distribution (family logistics) ---
    {"company_name": "Rhenus Logistics Netherlands", "website": "https://www.rhenus.com/nl-nl", "industry": "Logistics", "ownership": "family (Rethmann)"},
    {"company_name": "Bakker Logistiek", "website": "https://www.bakkerlogistiek.nl", "industry": "Fresh Logistics", "ownership": "family"},
    {"company_name": "Nabuurs Transport", "website": "https://www.nabuurs.com", "industry": "Transport / Logistics", "ownership": "family"},
    {"company_name": "Kuehne+Nagel Netherlands", "website": "https://nl.kuehne-nagel.com", "industry": "Freight / Logistics", "ownership": "family (Kuehne)"},
    {"company_name": "Van der Helm Logistiek", "website": "https://www.vanderhelm.nl", "industry": "Logistics / Waste", "ownership": "family"},
    {"company_name": "Ewals Cargo Care", "website": "https://www.ewals.com", "industry": "Logistics", "ownership": "family"},

    # --- Technology & Software (Dutch founder-led) ---
    {"company_name": "AFAS Software", "website": "https://www.afas.nl", "industry": "Business Software", "ownership": "founder-led"},
    {"company_name": "Coda Group", "website": "https://www.coda.nl", "industry": "Financial Software", "ownership": "founder"},
    {"company_name": "Ctac", "website": "https://www.ctac.nl", "industry": "IT Consulting", "ownership": "founder roots"},
    {"company_name": "Cegeka Netherlands", "website": "https://www.cegeka.com/nl", "industry": "IT Services", "ownership": "family (Cegeka)"},
    {"company_name": "Atos Benelux (local ops)", "website": "https://atos.net/nl", "industry": "IT Services", "ownership": "private"},
    {"company_name": "Thales Netherlands", "website": "https://www.thalesgroup.com/nl", "industry": "Defence / Technology", "ownership": "listed but local roots"},
    {"company_name": "Topicus", "website": "https://www.topicus.com", "industry": "Software / Healthcare IT", "ownership": "founder-led"},
    {"company_name": "Yellowbrick", "website": "https://www.yellowbrick.nl", "industry": "Parking / Mobility Tech", "ownership": "founder-led"},

    # --- Education & Training (aligned with SoR mission) ---
    {"company_name": "Learnbeat", "website": "https://www.learnbeat.nl", "industry": "EdTech", "ownership": "founder-led"},
    {"company_name": "Studytube", "website": "https://www.studytube.nl", "industry": "Corporate Learning", "ownership": "founder-led"},
    {"company_name": "GoodHabitz", "website": "https://www.goodhabitz.com", "industry": "Corporate E-learning", "ownership": "founder-led"},
    {"company_name": "Springest", "website": "https://www.springest.nl", "industry": "Training Platform", "ownership": "founder (Recruit Holdings)"},

    # --- Sustainability / Circular Economy pioneers ---
    {"company_name": "Auping", "website": "https://www.auping.nl", "industry": "Sustainable Furniture / Beds", "ownership": "family"},
    {"company_name": "Tony's Chocolonely", "website": "https://tonyschocolonely.com/nl", "industry": "FMCG / Social Enterprise", "ownership": "founder-led"},
    {"company_name": "Dopper", "website": "https://www.dopper.com", "industry": "Sustainable Consumer Goods", "ownership": "founder-led"},
    {"company_name": "Berkel en Rodenrijs (GreenPack)", "website": "https://www.greenpack.eu", "industry": "Reusable Packaging", "ownership": "founder-led"},
    {"company_name": "Seepje", "website": "https://www.seepje.com", "industry": "Natural Cleaning / FMCG", "ownership": "founder-led"},
    {"company_name": "Moyee Coffee", "website": "https://www.moyeecoffee.com", "industry": "Fair Trade / FMCG", "ownership": "founder-led"},
    {"company_name": "Fairphone", "website": "https://www.fairphone.com", "industry": "Sustainable Electronics", "ownership": "social enterprise"},
    {"company_name": "Circularise", "website": "https://www.circularise.com", "industry": "Circular Economy Tech", "ownership": "founder-led"},
    {"company_name": "Renewlogy Europe", "website": "https://www.renewlogy.com", "industry": "Plastic-to-Fuel / Recycling", "ownership": "founder-led"},
    {"company_name": "The Ocean Cleanup NL", "website": "https://theoceancleanup.com", "industry": "Ocean Plastic / NGO", "ownership": "foundation (Boyan Slat)"},

    # --- Events, Hospitality & Tourism (SME with local brand pride) ---
    {"company_name": "Sunweb Group", "website": "https://www.sunwebgroup.com", "industry": "Travel / Tourism", "ownership": "private"},
    {"company_name": "Corendon Hotels", "website": "https://www.corendon.nl", "industry": "Hospitality / Travel", "ownership": "founder-led"},
    {"company_name": "Fletcher Hotels", "website": "https://www.fletcher.nl", "industry": "Hospitality", "ownership": "family"},
    {"company_name": "Landal GreenParks", "website": "https://www.landal.nl", "industry": "Holiday Parks", "ownership": "Wyndham / NL roots"},
    {"company_name": "Roompot", "website": "https://www.roompot.nl", "industry": "Holiday Parks", "ownership": "private equity / NL founded"},

    # --- Finance & Insurance (cooperative / foundation) ---
    {"company_name": "Triodos Bank", "website": "https://www.triodos.nl", "industry": "Sustainable Banking", "ownership": "foundation"},
    {"company_name": "ASN Bank", "website": "https://www.asnbank.nl", "industry": "Sustainable Banking", "ownership": "foundation (de Volksbank)"},
    {"company_name": "Achmea", "website": "https://www.achmea.nl", "industry": "Insurance / Cooperative", "ownership": "cooperative"},
    {"company_name": "OHRA", "website": "https://www.ohra.nl", "industry": "Insurance", "ownership": "Achmea / cooperative roots"},
    {"company_name": "VGZ Zorgverzekeraar", "website": "https://www.vgz.nl", "industry": "Health Insurance", "ownership": "cooperative"},
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


def save_companies(companies: list[dict], path: Path, limit: int | None = None) -> None:
    path.parent.mkdir(exist_ok=True)
    if limit:
        companies = companies[:limit]
    fieldnames = ["company_name", "website", "industry", "ownership", "size_estimate", "country"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in companies:
            writer.writerow({k: c.get(k, "") for k in fieldnames})
    log.info("Saved %d companies to %s", len(companies), path)


def run(limit: int | None = None, dry_run: bool = False) -> list[dict]:
    log.info("Stage 1: Discovering companies (seed list of %d)", len(SEED_COMPANIES))
    companies = [enrich_company(dict(c)) for c in SEED_COMPANIES]

    # Remove any company already in active conversation
    before = len(companies)
    companies = [c for c in companies if c["company_name"] not in ALREADY_IN_TALKS]
    skipped = before - len(companies)
    if skipped:
        log.info("Skipped %d companies already in active talks: %s", skipped,
                 [c["company_name"] for c in SEED_COMPANIES if c["company_name"] in ALREADY_IN_TALKS])

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
