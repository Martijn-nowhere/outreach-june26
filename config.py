"""
Central configuration - reads from .env
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")

# --- API Keys ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
APIFY_API_KEY = os.getenv("APIFY_API_KEY", "")
GMAIL_CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")

# --- Outreach settings ---
OUTREACH_LANGUAGE = os.getenv("OUTREACH_LANGUAGE", "nl")  # "nl" or "en"
YOUR_NAME = os.getenv("YOUR_NAME", "Anne")
YOUR_COMPANY = os.getenv("YOUR_COMPANY", "YourCompany")
YOUR_PRODUCT_DESCRIPTION = os.getenv(
    "YOUR_PRODUCT_DESCRIPTION",
    "Educational platform helping companies achieve their sustainability goals through employee education programs",
)
RELEVANCE_THRESHOLD = int(os.getenv("RELEVANCE_THRESHOLD", "4"))
LINKEDIN_DAILY_LIMIT = int(os.getenv("LINKEDIN_DAILY_LIMIT", "5"))

# --- Pipeline settings ---
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

COMPANIES_CSV = DATA_DIR / "companies.csv"
CSR_ANALYSIS_CSV = DATA_DIR / "csr_analysis.csv"
CONTACTS_CSV = DATA_DIR / "contacts.csv"
OUTREACH_DRAFTS_CSV = DATA_DIR / "outreach_drafts.csv"
PROGRESS_JSON = DATA_DIR / "progress.json"

# --- Claude model ---
CLAUDE_MODEL = "claude-haiku-4-5-20251001"  # cost-efficient for bulk analysis

# --- Rate limiting ---
REQUEST_DELAY = 1.5  # seconds between web requests
API_DELAY = 0.5      # seconds between Claude API calls

# --- CSR URL patterns to try per company ---
CSR_URL_PATTERNS = [
    "{base}/sustainability",
    "{base}/duurzaamheid",
    "{base}/csr",
    "{base}/mvo",
    "{base}/maatschappelijk-verantwoord-ondernemen",
    "{base}/corporate-responsibility",
    "{base}/over-ons/duurzaamheid",
    "{base}/about/sustainability",
    "{base}/nl/duurzaamheid",
    "{base}/en/sustainability",
    "{base}/verantwoordelijkheid",
    "{base}/impact",
    # Additional patterns seen in the wild
    "{base}/over-ons/maatschappelijk-verantwoord-ondernemen",
    "{base}/over-ons/mvo",
    "{base}/over-ons/csr",
    "{base}/nl/over-ons/duurzaamheid",
    "{base}/maatschappij",
    "{base}/milieu",
    "{base}/responsible",
    "{base}/verantwoord",
    "{base}/onze-aanpak/duurzaamheid",
    "{base}/aanpak/duurzaamheid",
    "{base}/nl/sustainability",
    "{base}/about/csr",
    "{base}/about/responsibility",
    "{base}/about-us/sustainability",
    "{base}/circulair",
    "{base}/circulariteit",
    "{base}/planet",
    "{base}/people-planet",
]

# --- Gmail OAuth scopes ---
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

# --- Validation ---
def validate_config() -> list[str]:
    """Return list of missing/invalid config items."""
    issues = []
    if not ANTHROPIC_API_KEY:
        issues.append("ANTHROPIC_API_KEY is not set")
    if not YOUR_NAME or YOUR_NAME == "Anne":
        issues.append("YOUR_NAME is using default value 'Anne' - consider setting it")
    if not YOUR_COMPANY or YOUR_COMPANY == "YourCompany":
        issues.append("YOUR_COMPANY is using default value - consider setting it")
    return issues
