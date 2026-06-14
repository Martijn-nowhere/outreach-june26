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
    # --- Food & Beverage (family-owned / cooperative roots) ---
    {"company_name": "Dirk van den Broek", "website": "https://www.dirk.nl", "industry": "Retail / Grocery", "ownership": "family", "hometown": "Amsterdam"},
    {"company_name": "Hessing Supervers", "website": "https://www.hessing.nl", "industry": "Fresh Food / Packaging", "ownership": "family", "hometown": "Woerden"},
    {"company_name": "Agrifirm", "website": "https://www.agrifirm.nl", "industry": "Agriculture / Feed", "ownership": "cooperative", "hometown": "Apeldoorn"},
    {"company_name": "De Heus", "website": "https://www.deheus.com", "industry": "Animal Nutrition", "ownership": "family", "hometown": "Ede"},
    {"company_name": "Vion Food Group", "website": "https://www.vionfoodgroup.com", "industry": "Meat / Food Processing", "ownership": "cooperative", "hometown": ""},
    {"company_name": "Fresca Group", "website": "https://www.fresca.nl", "industry": "Fresh Produce / Packaging", "ownership": "family", "hometown": ""},
    {"company_name": "Zwanenberg Food Group", "website": "https://www.zwanenberg.nl", "industry": "Food / Meat", "ownership": "family", "hometown": "Almelo"},
    {"company_name": "Lantmännen Unibake NL", "website": "https://www.lantmannenunibake.nl", "industry": "Bakery / Food", "ownership": "cooperative", "hometown": ""},

    # --- Packaging & Plastics (direct relevance to SoR) ---
    {"company_name": "Lankhorst Mouldings", "website": "https://www.lankhorst-mouldings.com", "industry": "Recycled Plastic Products", "ownership": "family", "hometown": "Sneek"},
    {"company_name": "Van Leer Group", "website": "https://www.vanleer.com", "industry": "Industrial Packaging", "ownership": "family (Van Leer)", "hometown": ""},
    {"company_name": "Smurfit Westrock Netherlands", "website": "https://www.smurfitkappa.com/nl", "industry": "Paper Packaging", "ownership": "listed but NL family roots", "hometown": ""},
    {"company_name": "Leer Kunststoffen", "website": "https://www.leerkunststoffen.nl", "industry": "Plastic Packaging", "ownership": "family", "hometown": ""},
    {"company_name": "DPG Packaging", "website": "https://www.dpg-group.nl", "industry": "Plastic / Packaging", "ownership": "family", "hometown": ""},
    {"company_name": "Greiner Packaging NL", "website": "https://www.greiner-packaging.com/nl", "industry": "Plastic Packaging", "ownership": "family (Greiner)", "hometown": ""},
    {"company_name": "Naber Plastics", "website": "https://www.naberplastics.nl", "industry": "Plastic Processing", "ownership": "family", "hometown": ""},
    {"company_name": "Omniform Group", "website": "https://www.omniformgroup.com", "industry": "Packaging / Print", "ownership": "family", "hometown": ""},

    # --- Retail & Consumer (Dutch family chains) ---
    {"company_name": "Hema", "website": "https://www.hema.nl", "industry": "Retail", "ownership": "private equity (Dutch roots)", "hometown": "Amsterdam"},
    {"company_name": "Action", "website": "https://www.action.com/nl-nl", "industry": "Discount Retail", "ownership": "private equity (NL founded)", "hometown": "Zwaagdijk"},
    {"company_name": "Blokker Holding", "website": "https://www.blokker.nl", "industry": "Retail / Household", "ownership": "family (Blokker)", "hometown": "Amsterdam"},
    {"company_name": "Rituals Cosmetics", "website": "https://www.rituals.com", "industry": "Beauty / Retail", "ownership": "founder-led (Raymond Cloosterman)", "hometown": "Amsterdam"},
    {"company_name": "Scotch & Soda", "website": "https://www.scotch-soda.com", "industry": "Fashion Retail", "ownership": "private / founder roots", "hometown": "Amsterdam"},
    {"company_name": "Shoeby Fashion", "website": "https://www.shoeby.nl", "industry": "Fashion Retail", "ownership": "family", "hometown": ""},
    {"company_name": "America Today", "website": "https://www.americatoday.nl", "industry": "Fashion Retail", "ownership": "family", "hometown": ""},
    {"company_name": "Kwantum", "website": "https://www.kwantum.nl", "industry": "Home Furnishings Retail", "ownership": "family", "hometown": "Tilburg"},
    {"company_name": "Leen Bakker", "website": "https://www.leenbakker.nl", "industry": "Home Furnishings Retail", "ownership": "family", "hometown": ""},
    {"company_name": "Bristol", "website": "https://www.bristol.nl", "industry": "Footwear Retail", "ownership": "family", "hometown": ""},

    # --- Construction & Real Estate (family builders) ---
    {"company_name": "Dura Vermeer", "website": "https://www.duravermeer.nl", "industry": "Construction", "ownership": "family", "hometown": ""},
    {"company_name": "Heijmans", "website": "https://www.heijmans.nl", "industry": "Construction / Real Estate", "ownership": "listed but founder culture", "hometown": "Rosmalen"},
    {"company_name": "Van Wijnen", "website": "https://www.vanwijnen.nl", "industry": "Construction", "ownership": "family", "hometown": ""},
    {"company_name": "Spie Netherlands", "website": "https://www.spie.com/nl", "industry": "Technical Services", "ownership": "private", "hometown": ""},
    {"company_name": "Strukton", "website": "https://www.strukton.com", "industry": "Construction / Rail", "ownership": "cooperative (Deme Group)", "hometown": "Utrecht"},
    {"company_name": "Ballast Nedam", "website": "https://www.ballast-nedam.nl", "industry": "Construction", "ownership": "private (BESIX)", "hometown": ""},
    {"company_name": "BPD Bouwfonds", "website": "https://www.bpdeurope.com", "industry": "Real Estate Development", "ownership": "Rabobank subsidiary", "hometown": ""},
    {"company_name": "Volker Wessels", "website": "https://www.volkerwessels.com", "industry": "Construction", "ownership": "family (Wessels)", "hometown": "Amersfoort"},

    # --- Healthcare & Pharma (family / foundation owned) ---
    {"company_name": "Sanquin", "website": "https://www.sanquin.nl", "industry": "Blood Supply / Healthcare", "ownership": "foundation", "hometown": "Amsterdam"},
    {"company_name": "Zorggroep Alliade", "website": "https://www.alliade.nl", "industry": "Healthcare / Care", "ownership": "foundation", "hometown": "Heerenveen"},
    {"company_name": "Pluryn", "website": "https://www.pluryn.nl", "industry": "Care / Education", "ownership": "foundation", "hometown": "Nijmegen"},
    {"company_name": "Meander Medisch Centrum", "website": "https://www.meandermc.nl", "industry": "Hospital / Healthcare", "ownership": "foundation", "hometown": "Amersfoort"},
    {"company_name": "Rijnstate Ziekenhuis", "website": "https://www.rijnstate.nl", "industry": "Hospital / Healthcare", "ownership": "foundation", "hometown": "Arnhem"},
    {"company_name": "OPG Groep", "website": "https://www.opg.nl", "industry": "Pharmacy / Healthcare", "ownership": "cooperative", "hometown": "Utrecht"},
    {"company_name": "Brocacef", "website": "https://www.brocacef.nl", "industry": "Pharmaceutical Distribution", "ownership": "cooperative", "hometown": "Maarssen"},

    # --- Agriculture & Horticulture (cooperative / family) ---
    {"company_name": "Koppert Biological Systems", "website": "https://www.koppert.nl", "industry": "Biological Crop Protection", "ownership": "family (Koppert)", "hometown": "Berkel en Rodenrijs"},
    {"company_name": "Priva", "website": "https://www.priva.com", "industry": "Greenhouse Technology", "ownership": "family", "hometown": "De Lier"},
    {"company_name": "Ridder Group", "website": "https://www.ridder.com", "industry": "Horticulture Technology", "ownership": "family", "hometown": "Rockanje"},
    {"company_name": "Gebr. Smits", "website": "https://www.gebrsmits.nl", "industry": "Horticulture / Flowers", "ownership": "family", "hometown": ""},
    {"company_name": "Nunhems Netherlands", "website": "https://www.nunhems.com", "industry": "Vegetable Seeds", "ownership": "BASF subsidiary", "hometown": ""},
    {"company_name": "Rijk Zwaan", "website": "https://www.rijkzwaan.nl", "industry": "Vegetable Seeds", "ownership": "family", "hometown": "De Lier"},
    {"company_name": "Enza Zaden", "website": "https://www.enzazaden.nl", "industry": "Vegetable Seeds", "ownership": "family", "hometown": "Enkhuizen"},
    {"company_name": "HilverdaFlorist", "website": "https://www.hilverdaflorist.com", "industry": "Flowers / Horticulture", "ownership": "family", "hometown": "Hilversum"},

    # --- Manufacturing & Industrial (family-owned Dutch makers) ---
    {"company_name": "Aalberts Industries", "website": "https://www.aalberts.com", "industry": "Industrial Manufacturing", "ownership": "founder roots / listed", "hometown": ""},
    {"company_name": "Damen Shipyards", "website": "https://www.damen.com", "industry": "Shipbuilding", "ownership": "family (Damen)", "hometown": "Gorinchem"},
    {"company_name": "Roto Smeets Group", "website": "https://www.rsg.nl", "industry": "Print / Packaging", "ownership": "family", "hometown": "Utrecht"},
    {"company_name": "Feenstra", "website": "https://www.feenstra.com", "industry": "HVAC / Technical Services", "ownership": "private", "hometown": ""},
    {"company_name": "Batenburg Techniek", "website": "https://www.batenburg.nl", "industry": "Technical Services", "ownership": "family", "hometown": "Rotterdam"},
    {"company_name": "Nedvang", "website": "https://www.nedvang.nl", "industry": "Packaging Waste / Recycling", "ownership": "industry collective", "hometown": ""},
    {"company_name": "Van Gansewinkel", "website": "https://www.vangansewinkel.nl", "industry": "Waste Management", "ownership": "private", "hometown": ""},
    {"company_name": "Prezero Netherlands", "website": "https://www.prezero.com/nl", "industry": "Waste / Recycling", "ownership": "Schwarz Group", "hometown": ""},
    {"company_name": "Suez Netherlands", "website": "https://www.suez.nl", "industry": "Waste / Water", "ownership": "private", "hometown": ""},
    {"company_name": "Omrin", "website": "https://www.omrin.nl", "industry": "Waste Management", "ownership": "municipal cooperative", "hometown": "Leeuwarden"},

    # --- Logistics & Distribution (family logistics) ---
    {"company_name": "Rhenus Logistics Netherlands", "website": "https://www.rhenus.com/nl-nl", "industry": "Logistics", "ownership": "family (Rethmann)", "hometown": ""},
    {"company_name": "Bakker Logistiek", "website": "https://www.bakkerlogistiek.nl", "industry": "Fresh Logistics", "ownership": "family", "hometown": "Zeewolde"},
    {"company_name": "Nabuurs Transport", "website": "https://www.nabuurs.com", "industry": "Transport / Logistics", "ownership": "family", "hometown": "Veghel"},
    {"company_name": "Kuehne+Nagel Netherlands", "website": "https://nl.kuehne-nagel.com", "industry": "Freight / Logistics", "ownership": "family (Kuehne)", "hometown": ""},
    {"company_name": "Van der Helm Logistiek", "website": "https://www.vanderhelm.nl", "industry": "Logistics / Waste", "ownership": "family", "hometown": ""},
    {"company_name": "Ewals Cargo Care", "website": "https://www.ewals.com", "industry": "Logistics", "ownership": "family", "hometown": "Tegelen"},

    # --- Technology & Software (Dutch founder-led) ---
    {"company_name": "AFAS Software", "website": "https://www.afas.nl", "industry": "Business Software", "ownership": "founder-led", "hometown": "Leusden"},
    {"company_name": "Coda Group", "website": "https://www.coda.nl", "industry": "Financial Software", "ownership": "founder", "hometown": ""},
    {"company_name": "Ctac", "website": "https://www.ctac.nl", "industry": "IT Consulting", "ownership": "founder roots", "hometown": ""},
    {"company_name": "Cegeka Netherlands", "website": "https://www.cegeka.com/nl", "industry": "IT Services", "ownership": "family (Cegeka)", "hometown": ""},
    {"company_name": "Atos Benelux (local ops)", "website": "https://atos.net/nl", "industry": "IT Services", "ownership": "private", "hometown": ""},
    {"company_name": "Thales Netherlands", "website": "https://www.thalesgroup.com/nl", "industry": "Defence / Technology", "ownership": "listed but local roots", "hometown": "Hengelo"},
    {"company_name": "Topicus", "website": "https://www.topicus.com", "industry": "Software / Healthcare IT", "ownership": "founder-led", "hometown": "Deventer"},
    {"company_name": "Yellowbrick", "website": "https://www.yellowbrick.nl", "industry": "Parking / Mobility Tech", "ownership": "founder-led", "hometown": "Amsterdam"},

    # --- Education & Training (aligned with SoR mission) ---
    {"company_name": "Learnbeat", "website": "https://www.learnbeat.nl", "industry": "EdTech", "ownership": "founder-led", "hometown": ""},
    {"company_name": "Studytube", "website": "https://www.studytube.nl", "industry": "Corporate Learning", "ownership": "founder-led", "hometown": "Amsterdam"},
    {"company_name": "GoodHabitz", "website": "https://www.goodhabitz.com", "industry": "Corporate E-learning", "ownership": "founder-led", "hometown": "Eindhoven"},
    {"company_name": "Springest", "website": "https://www.springest.nl", "industry": "Training Platform", "ownership": "founder (Recruit Holdings)", "hometown": "Amsterdam"},

    # --- Sustainability / Circular Economy pioneers ---
    {"company_name": "Auping", "website": "https://www.auping.nl", "industry": "Sustainable Furniture / Beds", "ownership": "family", "hometown": "Deventer"},
    {"company_name": "Tony's Chocolonely", "website": "https://tonyschocolonely.com/nl", "industry": "FMCG / Social Enterprise", "ownership": "founder-led", "hometown": "Amsterdam"},
    {"company_name": "Dopper", "website": "https://www.dopper.com", "industry": "Sustainable Consumer Goods", "ownership": "founder-led", "hometown": "Haarlem"},
    {"company_name": "Berkel en Rodenrijs (GreenPack)", "website": "https://www.greenpack.eu", "industry": "Reusable Packaging", "ownership": "founder-led", "hometown": "Berkel en Rodenrijs"},
    {"company_name": "Seepje", "website": "https://www.seepje.com", "industry": "Natural Cleaning / FMCG", "ownership": "founder-led", "hometown": ""},
    {"company_name": "Moyee Coffee", "website": "https://www.moyeecoffee.com", "industry": "Fair Trade / FMCG", "ownership": "founder-led", "hometown": "Amsterdam"},
    {"company_name": "Fairphone", "website": "https://www.fairphone.com", "industry": "Sustainable Electronics", "ownership": "social enterprise", "hometown": "Amsterdam"},
    {"company_name": "Circularise", "website": "https://www.circularise.com", "industry": "Circular Economy Tech", "ownership": "founder-led", "hometown": "Den Haag"},
    {"company_name": "Renewlogy Europe", "website": "https://www.renewlogy.com", "industry": "Plastic-to-Fuel / Recycling", "ownership": "founder-led", "hometown": ""},
    {"company_name": "The Ocean Cleanup NL", "website": "https://theoceancleanup.com", "industry": "Ocean Plastic / NGO", "ownership": "foundation (Boyan Slat)", "hometown": "Rotterdam"},

    # --- Events, Hospitality & Tourism (SME with local brand pride) ---
    {"company_name": "Sunweb Group", "website": "https://www.sunwebgroup.com", "industry": "Travel / Tourism", "ownership": "private", "hometown": "Rotterdam"},
    {"company_name": "Corendon Hotels", "website": "https://www.corendon.nl", "industry": "Hospitality / Travel", "ownership": "founder-led", "hometown": ""},
    {"company_name": "Fletcher Hotels", "website": "https://www.fletcher.nl", "industry": "Hospitality", "ownership": "family", "hometown": ""},
    {"company_name": "Landal GreenParks", "website": "https://www.landal.nl", "industry": "Holiday Parks", "ownership": "Wyndham / NL roots", "hometown": ""},
    {"company_name": "Roompot", "website": "https://www.roompot.nl", "industry": "Holiday Parks", "ownership": "private equity / NL founded", "hometown": "Goes"},

    # --- Finance & Insurance (cooperative / foundation) ---
    {"company_name": "Triodos Bank", "website": "https://www.triodos.nl", "industry": "Sustainable Banking", "ownership": "foundation", "hometown": "Zeist"},
    {"company_name": "ASN Bank", "website": "https://www.asnbank.nl", "industry": "Sustainable Banking", "ownership": "foundation (de Volksbank)", "hometown": "Den Haag"},
    {"company_name": "Achmea", "website": "https://www.achmea.nl", "industry": "Insurance / Cooperative", "ownership": "cooperative", "hometown": "Zeist"},
    {"company_name": "OHRA", "website": "https://www.ohra.nl", "industry": "Insurance", "ownership": "Achmea / cooperative roots", "hometown": "Arnhem"},
    {"company_name": "VGZ Zorgverzekeraar", "website": "https://www.vgz.nl", "industry": "Health Insurance", "ownership": "cooperative", "hometown": "Arnhem"},

    # --- Regionally-rooted Dutch family companies — local community focus ---
    # Strong local identity, sponsor sports clubs, aware of production waste, want to give back locally
    {"company_name": "Grolsch Brewery", "website": "https://www.grolsch.nl", "industry": "Brewery / Beverages", "ownership": "family roots (now Asahi)", "hometown": "Enschede"},
    {"company_name": "Hak", "website": "https://www.hak.nl", "industry": "Food / Vegetable Preserves", "ownership": "family", "hometown": "Giessen"},
    {"company_name": "Nijhof-Wassink", "website": "https://www.nijhof-wassink.nl", "industry": "Transport / Logistics", "ownership": "family", "hometown": "Twello"},
    {"company_name": "Ter Beke Netherlands", "website": "https://www.terbeke.com", "industry": "Food / Charcuterie", "ownership": "family (Ter Beke)", "hometown": "Almelo"},
    {"company_name": "Ten Cate", "website": "https://www.tencate.com", "industry": "Industrial Textiles / Advanced Materials", "ownership": "family (175+ years)", "hometown": "Almelo"},
    {"company_name": "Muller", "website": "https://www.mullerlogistics.nl", "industry": "Logistics / Transport", "ownership": "family", "hometown": "Hoofddorp"},
    {"company_name": "Eosta", "website": "https://www.eosta.com", "industry": "Organic Food Distribution", "ownership": "founder-led", "hometown": "Waddinxveen"},
    {"company_name": "Bakker Bart", "website": "https://www.bakkerbart.nl", "industry": "Bakery Chain / FMCG", "ownership": "family", "hometown": ""},
    {"company_name": "De Graafstroom", "website": "https://www.degraafstroom.nl", "industry": "Dairy / Cooperative", "ownership": "cooperative", "hometown": "Bleskensgraaf"},
    {"company_name": "Coop Supermarkten", "website": "https://www.coop.nl", "industry": "Retail / Grocery", "ownership": "cooperative", "hometown": "Ede"},
    {"company_name": "Jan Linders", "website": "https://www.janlinders.nl", "industry": "Retail / Grocery", "ownership": "family", "hometown": "Venlo"},
    {"company_name": "Poiesz Supermarkten", "website": "https://www.poiesz-supermarkten.nl", "industry": "Retail / Grocery", "ownership": "family", "hometown": "Sneek"},
    {"company_name": "Deen Supermarkten", "website": "https://www.deen.nl", "industry": "Retail / Grocery", "ownership": "family (ref: acquired by Jumbo)", "hometown": "Hoorn"},
    {"company_name": "Hoogvliet", "website": "https://www.hoogvliet.com", "industry": "Retail / Grocery", "ownership": "family", "hometown": "Gouda"},
    {"company_name": "Vomar", "website": "https://www.vomar.nl", "industry": "Retail / Grocery", "ownership": "family", "hometown": "Alkmaar"},
    {"company_name": "PLUS Retail", "website": "https://www.plus.nl", "industry": "Retail / Grocery", "ownership": "cooperative", "hometown": "Amstelveen"},
    {"company_name": "Pets Place", "website": "https://www.petsplace.nl", "industry": "Pet Retail", "ownership": "family", "hometown": "Barneveld"},
    {"company_name": "Brouwer Transport", "website": "https://www.brouwertransport.nl", "industry": "Transport / Logistics", "ownership": "family", "hometown": "Genemuiden"},
    {"company_name": "Wolters Noordhoff", "website": "https://www.woltersnoordhoff.nl", "industry": "Educational Publishing", "ownership": "family roots", "hometown": "Groningen"},
    {"company_name": "Nedap", "website": "https://www.nedap.com", "industry": "Technology / Electronics", "ownership": "founder culture", "hometown": "Groenlo"},
    {"company_name": "Wavin", "website": "https://www.wavin.com", "industry": "Plastic Pipe / Water Management", "ownership": "private (Mexichem)", "hometown": "Zwolle"},
    {"company_name": "Refresco", "website": "https://www.refresco.com", "industry": "Beverage Packaging / Production", "ownership": "family roots", "hometown": "Rotterdam"},
    {"company_name": "Aviko", "website": "https://www.aviko.nl", "industry": "Potato Products / Food", "ownership": "cooperative (Royal Cosun)", "hometown": "Steenderen"},
    {"company_name": "Farm Frites", "website": "https://www.farmfrites.com", "industry": "Potato / Food Processing", "ownership": "family", "hometown": "Oudenhoorn"},
    {"company_name": "Zwietering Groep", "website": "https://www.zwietering.nl", "industry": "Food / Regional", "ownership": "family", "hometown": "Almelo"},
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
