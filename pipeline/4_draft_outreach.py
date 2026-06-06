"""
Stage 4: Generate personalized outreach drafts.
For each contact, uses Claude API to write a personalized cold email (Dutch/English)
referencing specific CSR goals from their report.
Also generates a LinkedIn connection note (max 300 chars).
Saves to data/outreach_drafts.csv
"""
import sys
import csv
import json
import time
import logging
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DRAFTS_FIELDS = [
    "company_name",
    "contact_name",
    "contact_title",
    "contact_email",
    "email_subject",
    "email_body",
    "linkedin_note",
    "linkedin_followup",   # message sent after connection is accepted
    "language",
    "personalization_notes",
]

MOCK_EMAIL_NL = """Onderwerp: Duurzaamheidsdoelen versnellen via medewerkersopleiding

Hallo {name},

Ik zag dat {company} ambitieuze duurzaamheidsdoelen heeft gesteld en ben benieuwd hoe jullie medewerkers hierbij betrokken worden. Ons educatief platform helpt bedrijven zoals {company} om duurzaamheidskennis structureel in te bedden via gerichte opleidingsprogramma's voor medewerkers.

Zou je openstaan voor een kort gesprek van 15 minuten om te verkennen of dit aansluit bij jullie aanpak?

Met vriendelijke groet,
{sender}"""

MOCK_LINKEDIN_NL = "Hallo {name}, ik volg de duurzaamheidsinitiatieven van {company} en zie veel raakvlakken met ons educatief platform. Graag even sparren!"
MOCK_LINKEDIN_FOLLOWUP_NL = "Hallo {name}, dank voor het connecten! Ik ben benieuwd hoe jullie medewerkers worden betrokken bij de duurzaamheidsdoelen van {company}. Heb je 15 minuten voor een kort gesprek?"

MOCK_EMAIL_EN ="""Subject: Accelerating sustainability goals through employee education

Hi {name},

I noticed {company} has set ambitious sustainability goals and I'm curious how your employees are being engaged in achieving them. Our educational platform helps companies like {company} systematically embed sustainability knowledge through targeted employee training programs.

Would you be open to a brief 15-minute conversation to explore whether this fits your approach?

Best regards,
{sender}"""

MOCK_LINKEDIN_EN = "Hi {name}, I've been following {company}'s sustainability initiatives and see strong alignment with our educational platform. Would love to connect!"
MOCK_LINKEDIN_FOLLOWUP_EN = "Hi {name}, thanks for connecting! I'm curious how {company} engages employees in its sustainability goals. Would you have 15 minutes for a brief chat?"


# ---------------------------------------------------------------------------
# Claude draft generation
# ---------------------------------------------------------------------------

def generate_email_draft(
    contact: dict,
    csr_data: dict,
    language: str = "nl",
    dry_run: bool = False,
) -> dict:
    """Generate personalized email and LinkedIn note using Claude."""
    company_name = contact.get("company_name", "")
    contact_name = contact.get("contact_name", "") or "Duurzaamheidsverantwoordelijke"
    contact_title = contact.get("contact_title", "")
    first_name = contact_name.split()[0] if contact_name.strip() else "Hallo"

    analysis_summary = csr_data.get("analysis_summary", "")
    key_quotes = csr_data.get("key_quotes", "")
    mentions_edu = csr_data.get("mentions_education", "False")
    mentions_plastic = csr_data.get("mentions_plastic_waste", "False")
    mentions_sus = csr_data.get("mentions_sustainability", "False")

    sender_name = config.YOUR_NAME
    sender_company = config.YOUR_COMPANY
    product_desc = config.YOUR_PRODUCT_DESCRIPTION

    if dry_run or not HAS_ANTHROPIC or not config.ANTHROPIC_API_KEY:
        # Return mock drafts
        if language == "nl":
            body = MOCK_EMAIL_NL.format(
                name=first_name, company=company_name, sender=sender_name
            )
            subject = f"Duurzaamheidsdoelen versnellen via medewerkersopleiding | {company_name}"
            linkedin = MOCK_LINKEDIN_NL.format(name=first_name, company=company_name)
            followup = MOCK_LINKEDIN_FOLLOWUP_NL.format(name=first_name, company=company_name)
        else:
            body = MOCK_EMAIL_EN.format(
                name=first_name, company=company_name, sender=sender_name
            )
            subject = f"Accelerating sustainability goals via employee education | {company_name}"
            linkedin = MOCK_LINKEDIN_EN.format(name=first_name, company=company_name)
            followup = MOCK_LINKEDIN_FOLLOWUP_EN.format(name=first_name, company=company_name)
        return {
            "email_subject": subject,
            "email_body": body,
            "linkedin_note": linkedin,
            "linkedin_followup": followup,
            "personalization_notes": "[DRY-RUN] Mock draft",
        }

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    if language == "nl":
        prompt = f"""Je bent een B2B outreach expert voor School of Recycling (SoR), een digitaal educatieplatform over afval en recycling.

WAT WIJ BIEDEN:
- Organisatielicentie voor bedrijven: medewerkers leren hoe afvalsystemen écht werken (plastic, recycling, materiaalketens)
- Online cursussen, systeem-gebaseerd, geen greenwashing — feiten en inzicht
- Ideaal voor CSR/ESG teams, duurzaamheidsprogramma's, medewerkerseducatie
- Website: schoolofrecycling.com

AFZENDER: {sender_name}, {sender_company}

ONTVANGER: {first_name} ({contact_title}) bij {company_name}

CSR RAPPORT ANALYSE van {company_name}:
- Samenvatting: {analysis_summary}
- Sleutelcitaten: {key_quotes}
- Noemt educatie/opleidingen: {mentions_edu}
- Noemt plasticreductie/afval: {mentions_plastic}
- Duurzaamheid kernthema: {mentions_sus}

SCHRIJF (in het Nederlands):

1. Cold outreach e-mail (max 4 zinnen body, exclusief aanhef/afsluiting):
   - Verwijs naar een specifiek CSR-doel of -citaat van {company_name}
   - Verbind dit aan hoe SoR medewerkers écht afvalinzicht geeft (niet alleen bewustwording)
   - Zachte CTA: bijv. "Zou je openstaan voor een kennismaking van 15 minuten?"
   - Oprecht, geen jargon, niet salesy

2. LinkedIn connectieverzoek (EXACT max 300 tekens):
   - Persoonlijk haakje gebaseerd op hun CSR-werk
   - Eindig met uitnodiging om te verbinden

3. LinkedIn follow-up bericht (na acceptatie, max 400 tekens):
   - Dank voor verbinding
   - Korte relevante vraag over hun aanpak
   - Geen pitch, echte nieuwsgierigheid

Geef je antwoord in JSON:
{{
  "email_subject": "<onderwerpregel>",
  "email_body": "<volledige e-mail: Hallo {first_name},\\n\\n<body>\\n\\nMet vriendelijke groet,\\n{sender_name}\\nSchool of Recycling\\nschoolofrecycling.com>",
  "linkedin_note": "<max 300 tekens>",
  "linkedin_followup": "<max 400 tekens, na acceptatie>",
  "personalization_notes": "<wat is gepersonaliseerd op basis van hun CSR-rapport>"
}}"""
    else:
        prompt = f"""You are a B2B outreach expert for School of Recycling (SoR), a digital waste education platform.

WHAT WE OFFER:
- Organisation license for companies: employees learn how waste systems actually work (plastic, recycling, material flows)
- Online courses, systems-based, fact-driven — no greenwashing, no slogans
- Ideal for CSR/ESG teams, sustainability programmes, employee education
- Website: schoolofrecycling.com

SENDER: {sender_name}, {sender_company}

RECIPIENT: {first_name} ({contact_title}) at {company_name}

CSR REPORT ANALYSIS of {company_name}:
- Summary: {analysis_summary}
- Key quotes: {key_quotes}
- Mentions education/training: {mentions_edu}
- Mentions plastic reduction/waste: {mentions_plastic}
- Sustainability as core theme: {mentions_sus}

WRITE (in English):

1. Cold outreach email (max 4 sentences body, excluding greeting/closing):
   - Reference a specific CSR goal or quote from {company_name}
   - Connect it to how SoR gives employees real waste knowledge (not just awareness)
   - Soft CTA: e.g. "Would you be open to a 15-minute intro call?"
   - Genuine, no jargon, not salesy

2. LinkedIn connection request (EXACT max 300 characters):
   - Personal hook based on their CSR work
   - End with invitation to connect

3. LinkedIn follow-up message (after they accept, max 400 characters):
   - Thank them for connecting
   - Short genuine question about their approach
   - No pitch, real curiosity

Respond in JSON:
{{
  "email_subject": "<subject line>",
  "email_body": "<full email: Hi {first_name},\\n\\n<body>\\n\\nBest regards,\\n{sender_name}\\nSchool of Recycling\\nschoolofrecycling.com>",
  "linkedin_note": "<max 300 chars>",
  "linkedin_followup": "<max 400 chars, sent after they accept>",
  "personalization_notes": "<what was personalised based on their CSR report>"
}}"""

    try:
        msg = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            if len(result.get("linkedin_note", "")) > 300:
                result["linkedin_note"] = result["linkedin_note"][:297] + "..."
            if len(result.get("linkedin_followup", "")) > 400:
                result["linkedin_followup"] = result["linkedin_followup"][:397] + "..."
            return result
        log.warning("  Could not parse Claude JSON for draft of %s", company_name)
    except Exception as e:
        log.warning("  Claude draft error for %s: %s", company_name, e)

    # Fallback
    return {
        "email_subject": f"Duurzaamheidsdoelen | {company_name}",
        "email_body": f"Hallo {first_name},\n\nIk zou graag een gesprek hebben over hoe wij {company_name} kunnen ondersteunen bij jullie duurzaamheidsdoelen.\n\nMet vriendelijke groet,\n{sender_name}",
        "linkedin_note": f"Hallo {first_name}, graag verbinden om de duurzaamheidsaanpak van {company_name} te bespreken.",
        "personalization_notes": "Fallback template - Claude API unavailable",
    }


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_contacts() -> list[dict]:
    if not config.CONTACTS_CSV.exists():
        return []
    with open(config.CONTACTS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_csr_by_company() -> dict:
    if not config.CSR_ANALYSIS_CSV.exists():
        return {}
    with open(config.CSR_ANALYSIS_CSV, newline="", encoding="utf-8") as f:
        return {r["company_name"]: dict(r) for r in csv.DictReader(f)}


def load_existing_drafts() -> dict:
    if not config.OUTREACH_DRAFTS_CSV.exists():
        return {}
    with open(config.OUTREACH_DRAFTS_CSV, newline="", encoding="utf-8") as f:
        return {
            (r["company_name"], r["contact_email"]): dict(r)
            for r in csv.DictReader(f)
        }


def load_progress() -> dict:
    if config.PROGRESS_JSON.exists():
        with open(config.PROGRESS_JSON) as f:
            return json.load(f)
    return {}


def save_progress(progress: dict) -> None:
    with open(config.PROGRESS_JSON, "w") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run(limit: int | None = None, dry_run: bool = False) -> list[dict]:
    log.info("Stage 4: Generating outreach drafts")

    contacts = load_contacts()
    if not contacts:
        log.error("contacts.csv not found or empty - run stage 3 first")
        return []

    csr_map = load_csr_by_company()
    existing_drafts = load_existing_drafts()
    progress = load_progress()
    language = config.OUTREACH_LANGUAGE

    if limit:
        contacts = contacts[:limit]

    log.info("Generating drafts for %d contacts in language=%s", len(contacts), language)

    all_drafts = []

    for i, contact in enumerate(contacts, 1):
        company_name = contact.get("company_name", "")
        contact_email = contact.get("contact_email", "")
        contact_name = contact.get("contact_name", "")

        log.info("[%d/%d] Drafting for: %s / %s", i, len(contacts), company_name, contact_name or "(no name)")

        key = (company_name, contact_email)
        if key in existing_drafts:
            log.info("  Skipping (already drafted)")
            all_drafts.append(existing_drafts[key])
            continue

        csr_data = csr_map.get(company_name, {})
        draft = generate_email_draft(contact, csr_data, language=language, dry_run=dry_run)

        row = {
            "company_name": company_name,
            "contact_name": contact_name,
            "contact_title": contact.get("contact_title", ""),
            "contact_email": contact_email,
            "email_subject": draft.get("email_subject", ""),
            "email_body": draft.get("email_body", ""),
            "linkedin_note": draft.get("linkedin_note", ""),
            "linkedin_followup": draft.get("linkedin_followup", ""),
            "language": language,
            "personalization_notes": draft.get("personalization_notes", ""),
        }
        all_drafts.append(row)

        progress[company_name] = {**progress.get(company_name, {}), "stage": "drafts_generated"}
        save_progress(progress)

        log.info("  Draft generated (subject: %s)", draft.get("email_subject", "")[:60])
        time.sleep(config.API_DELAY)

    # Save drafts
    config.DATA_DIR.mkdir(exist_ok=True)
    with open(config.OUTREACH_DRAFTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DRAFTS_FIELDS)
        writer.writeheader()
        for row in all_drafts:
            writer.writerow({k: row.get(k, "") for k in DRAFTS_FIELDS})

    log.info("Stage 4 complete: %d drafts saved to %s", len(all_drafts), config.OUTREACH_DRAFTS_CSV)
    return all_drafts


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Stage 4: Draft outreach emails")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)
