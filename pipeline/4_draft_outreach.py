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

MOCK_EMAIL_EN = """Subject: Accelerating sustainability goals through employee education

Hi {name},

I noticed {company} has set ambitious sustainability goals and I'm curious how your employees are being engaged in achieving them. Our educational platform helps companies like {company} systematically embed sustainability knowledge through targeted employee training programs.

Would you be open to a brief 15-minute conversation to explore whether this fits your approach?

Best regards,
{sender}"""

MOCK_LINKEDIN_EN = "Hi {name}, I've been following {company}'s sustainability initiatives and see strong alignment with our educational platform. Would love to connect!"


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
        else:
            body = MOCK_EMAIL_EN.format(
                name=first_name, company=company_name, sender=sender_name
            )
            subject = f"Accelerating sustainability goals via employee education | {company_name}"
            linkedin = MOCK_LINKEDIN_EN.format(name=first_name, company=company_name)
        return {
            "email_subject": subject,
            "email_body": body,
            "linkedin_note": linkedin,
            "personalization_notes": "[DRY-RUN] Mock draft",
        }

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    if language == "nl":
        prompt = f"""Je bent een B2B sales expert die warme, persoonlijke outreach e-mails schrijft in het Nederlands.

CONTEXT:
- Afzender: {sender_name} van {sender_company}
- Product/dienst: {product_desc}
- Ontvanger: {first_name} {contact_name.split()[-1] if len(contact_name.split()) > 1 else ''}, {contact_title} bij {company_name}

CSR RAPPORT ANALYSE van {company_name}:
- Samenvatting: {analysis_summary}
- Sleutelcitaten: {key_quotes}
- Noemt onderwijs/opleidingen: {mentions_edu}
- Noemt plasticreductie: {mentions_plastic}
- Duurzaamheid als kernthema: {mentions_sus}

SCHRIJF:
1. Een korte, warme cold outreach e-mail in het Nederlands (max 4 zinnen in de bodytekst, exclusief aanhef en afsluiting)
   - Verwijs specifiek naar een duurzaamheidsdoel of initiatief van {company_name}
   - Verbind dit aan hoe ons platform kan helpen
   - Eindig met een zachte call-to-action (bijv. "Zou je openstaan voor een gesprek van 15 minuten?")
   - Geen buzz words, niet salesy, oprecht en menselijk

2. Een LinkedIn connectieverzoek bericht (MAXIMAAL 300 tekens, inclusief spaties)

Geef je antwoord in JSON:
{{
  "email_subject": "<onderwerpregel>",
  "email_body": "<volledige e-mail inclusief Hallo {first_name},\\n\\n<body>\\n\\nMet vriendelijke groet,\\n{sender_name}\\n{sender_company}>",
  "linkedin_note": "<linkedin bericht max 300 tekens>",
  "personalization_notes": "<kort notitie over wat je hebt gepersonaliseerd>"
}}"""
    else:
        prompt = f"""You are a B2B sales expert writing warm, personalized outreach emails in English.

CONTEXT:
- Sender: {sender_name} from {sender_company}
- Product/service: {product_desc}
- Recipient: {first_name} ({contact_title}) at {company_name}

CSR REPORT ANALYSIS of {company_name}:
- Summary: {analysis_summary}
- Key quotes: {key_quotes}
- Mentions education/training: {mentions_edu}
- Mentions plastic reduction: {mentions_plastic}
- Sustainability as core theme: {mentions_sus}

WRITE:
1. A short, warm cold outreach email in English (max 4 sentences in body, excluding greeting and closing)
   - Reference a specific sustainability goal or initiative from {company_name}
   - Connect it to how our platform can help
   - End with a soft call-to-action
   - No buzzwords, not salesy, genuine and human

2. A LinkedIn connection request message (MAX 300 characters including spaces)

Respond in JSON:
{{
  "email_subject": "<subject line>",
  "email_body": "<full email including Hi {first_name},\\n\\n<body>\\n\\nBest regards,\\n{sender_name}\\n{sender_company}>",
  "linkedin_note": "<linkedin message max 300 chars>",
  "personalization_notes": "<brief note on what you personalized>"
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
            # Ensure LinkedIn note is <= 300 chars
            if len(result.get("linkedin_note", "")) > 300:
                result["linkedin_note"] = result["linkedin_note"][:297] + "..."
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
