"""
Stage 5: Export contacts and drafts.
Two outputs:
  1. Apollo/Lemlist-compatible CSV for import
  2. Gmail drafts via Gmail API (OAuth)
"""
import sys
import csv
import json
import base64
import logging
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Apollo / Lemlist export
# ---------------------------------------------------------------------------

# Apollo CSV column mapping
APOLLO_FIELDS = [
    "First Name",
    "Last Name",
    "Title",
    "Company",
    "Email",
    "Website",
    "LinkedIn URL",
    "Email Subject",
    "Email Body",
    "LinkedIn Note",
    "CSR Relevance Score",
    "Personalization Notes",
    "Country",
]

# Lemlist CSV column mapping (overlapping, slightly different names)
LEMLIST_FIELDS = [
    "firstName",
    "lastName",
    "email",
    "companyName",
    "website",
    "linkedinUrl",
    "jobTitle",
    "emailSubject",
    "emailBody",
    "linkedinNote",
    "csrRelevanceScore",
    "personalizationNotes",
    "country",
]


def split_name(full_name: str) -> tuple[str, str]:
    """Split full name into first and last."""
    parts = full_name.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def load_contacts() -> list[dict]:
    if not config.CONTACTS_CSV.exists():
        log.error("contacts.csv not found")
        return []
    with open(config.CONTACTS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_drafts() -> dict:
    """Return dict keyed by (company_name, contact_email)."""
    if not config.OUTREACH_DRAFTS_CSV.exists():
        return {}
    with open(config.OUTREACH_DRAFTS_CSV, newline="", encoding="utf-8") as f:
        return {
            (r["company_name"], r["contact_email"]): dict(r)
            for r in csv.DictReader(f)
        }


def export_apollo_csv(contacts: list[dict], drafts: dict, output_path: Path) -> int:
    """Export Apollo-format CSV. Returns number of rows written."""
    rows = []
    for contact in contacts:
        first, last = split_name(contact.get("contact_name", ""))
        key = (contact.get("company_name", ""), contact.get("contact_email", ""))
        draft = drafts.get(key, {})
        row = {
            "First Name": first,
            "Last Name": last,
            "Title": contact.get("contact_title", ""),
            "Company": contact.get("company_name", ""),
            "Email": contact.get("contact_email", ""),
            "Website": contact.get("website", ""),
            "LinkedIn URL": contact.get("linkedin_url", ""),
            "Email Subject": draft.get("email_subject", ""),
            "Email Body": draft.get("email_body", "").replace("\n", "\\n"),
            "LinkedIn Note": draft.get("linkedin_note", ""),
            "CSR Relevance Score": contact.get("relevance_score", ""),
            "Personalization Notes": draft.get("personalization_notes", ""),
            "Country": "NL",
        }
        rows.append(row)

    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=APOLLO_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    log.info("Apollo CSV exported: %d rows to %s", len(rows), output_path)
    return len(rows)


def export_lemlist_csv(contacts: list[dict], drafts: dict, output_path: Path) -> int:
    """Export Lemlist-format CSV. Returns number of rows written."""
    rows = []
    for contact in contacts:
        first, last = split_name(contact.get("contact_name", ""))
        key = (contact.get("company_name", ""), contact.get("contact_email", ""))
        draft = drafts.get(key, {})
        row = {
            "firstName": first,
            "lastName": last,
            "email": contact.get("contact_email", ""),
            "companyName": contact.get("company_name", ""),
            "website": contact.get("website", ""),
            "linkedinUrl": contact.get("linkedin_url", ""),
            "jobTitle": contact.get("contact_title", ""),
            "emailSubject": draft.get("email_subject", ""),
            "emailBody": draft.get("email_body", "").replace("\n", "\\n"),
            "linkedinNote": draft.get("linkedin_note", ""),
            "csrRelevanceScore": contact.get("relevance_score", ""),
            "personalizationNotes": draft.get("personalization_notes", ""),
            "country": "NL",
        }
        rows.append(row)

    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LEMLIST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    log.info("Lemlist CSV exported: %d rows to %s", len(rows), output_path)
    return len(rows)


# ---------------------------------------------------------------------------
# Gmail draft creation
# ---------------------------------------------------------------------------

def get_gmail_service():
    """Authenticate and return Gmail API service."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        log.error(
            "Google API libraries not installed. "
            "Run: pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client"
        )
        return None

    creds = None
    token_path = Path("token.json")
    credentials_path = Path(config.GMAIL_CREDENTIALS_PATH)

    if not credentials_path.exists():
        log.error(
            "Gmail credentials file not found at: %s\n"
            "Run setup_gmail.py to set up OAuth credentials.",
            credentials_path,
        )
        return None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), config.GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), config.GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def create_gmail_draft(service, to_email: str, subject: str, body: str) -> str | None:
    """Create a Gmail draft. Returns draft ID or None."""
    try:
        msg = MIMEMultipart("alternative")
        msg["To"] = to_email
        msg["Subject"] = subject
        # Plain text part
        msg.attach(MIMEText(body, "plain", "utf-8"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        draft = service.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw}},
        ).execute()
        return draft.get("id")
    except Exception as e:
        log.warning("  Gmail draft creation failed: %s", e)
        return None


def export_gmail_drafts(
    contacts: list[dict],
    drafts: dict,
    dry_run: bool = False,
) -> int:
    """Create Gmail drafts for all contacts. Returns number created."""
    if dry_run:
        log.info("[DRY-RUN] Would create %d Gmail drafts (skipping actual API calls)", len(contacts))
        return len(contacts)

    service = get_gmail_service()
    if not service:
        log.error("Gmail service unavailable - skipping Gmail drafts")
        return 0

    created = 0
    for contact in contacts:
        email = contact.get("contact_email", "")
        if not email or "@" not in email:
            log.warning("  Skipping contact with no valid email: %s", contact.get("contact_name", ""))
            continue

        key = (contact.get("company_name", ""), email)
        draft = drafts.get(key, {})
        subject = draft.get("email_subject", f"Re: {contact.get('company_name', '')}")
        body = draft.get("email_body", "")

        if not body:
            log.warning("  No email body for %s / %s", contact.get("company_name"), email)
            continue

        draft_id = create_gmail_draft(service, email, subject, body)
        if draft_id:
            log.info("  Created Gmail draft for %s (%s) - draft ID: %s", contact.get("company_name"), email, draft_id)
            created += 1
        else:
            log.warning("  Failed to create draft for %s", email)

        time.sleep(0.3)  # respect Gmail API rate limits

    log.info("Gmail: %d drafts created", created)
    return created


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run(limit: int | None = None, dry_run: bool = False) -> dict:
    log.info("Stage 5: Exporting data")

    contacts = load_contacts()
    drafts = load_drafts()

    if not contacts:
        log.error("No contacts found - run stages 1-3 first")
        return {}

    if limit:
        contacts = contacts[:limit]

    log.info("Exporting %d contacts (%d with drafts)", len(contacts), len(drafts))

    # 1. Apollo CSV
    apollo_path = config.DATA_DIR / "export_apollo.csv"
    apollo_count = export_apollo_csv(contacts, drafts, apollo_path)

    # 2. Lemlist CSV
    lemlist_path = config.DATA_DIR / "export_lemlist.csv"
    lemlist_count = export_lemlist_csv(contacts, drafts, lemlist_path)

    # 3. Gmail Drafts
    gmail_count = export_gmail_drafts(contacts, drafts, dry_run=dry_run)

    results = {
        "apollo_csv": str(apollo_path),
        "apollo_rows": apollo_count,
        "lemlist_csv": str(lemlist_path),
        "lemlist_rows": lemlist_count,
        "gmail_drafts_created": gmail_count,
    }

    log.info(
        "Stage 5 complete: Apollo=%d rows, Lemlist=%d rows, Gmail=%d drafts",
        apollo_count, lemlist_count, gmail_count,
    )
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Stage 5: Export to Apollo/Lemlist/Gmail")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)
