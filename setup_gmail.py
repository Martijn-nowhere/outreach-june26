"""
Gmail OAuth Setup Script
Walks through the step-by-step process of setting up Gmail API credentials
for creating email drafts.
"""
import json
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def check_dependencies():
    missing = []
    try:
        import google.oauth2.credentials
    except ImportError:
        missing.append("google-auth")
    try:
        import google_auth_oauthlib
    except ImportError:
        missing.append("google-auth-oauthlib")
    try:
        import googleapiclient
    except ImportError:
        missing.append("google-api-python-client")

    if missing:
        print("\nMissing dependencies. Install them with:")
        print(f"  pip install {' '.join(missing)}\n")
        return False
    return True


def print_step(n: int, title: str):
    print(f"\n{'='*50}")
    print(f"  Stap {n}: {title}")
    print(f"{'='*50}")


def setup_gmail():
    print("\n" + "=" * 60)
    print("  Gmail OAuth Setup voor Outreach Pipeline")
    print("=" * 60)
    print(
        "\nDit script helpt je Gmail API-toegang in te stellen zodat de pipeline"
        "\nautomatisch concept-emails kan aanmaken (niet versturen - alleen drafts).\n"
    )

    # Step 1: Check dependencies
    print_step(1, "Afhankelijkheden controleren")
    if not check_dependencies():
        print("Installeer ontbrekende pakketten en voer dit script opnieuw uit.")
        sys.exit(1)
    print("  Alle Google API-bibliotheken zijn geinstalleerd.")

    # Step 2: Google Cloud Console
    print_step(2, "Google Cloud Console instellen")
    print(
        """  Volg deze stappen in de Google Cloud Console:

  1. Ga naar: https://console.cloud.google.com/
  2. Maak een nieuw project aan (bijv. 'outreach-pipeline') of selecteer een bestaand project
  3. Ga naar 'APIs & Services' > 'Library'
  4. Zoek naar 'Gmail API' en klik op 'Enable'
  5. Ga naar 'APIs & Services' > 'Credentials'
  6. Klik op '+ CREATE CREDENTIALS' > 'OAuth client ID'
  7. Selecteer als application type: 'Desktop app'
  8. Geef het een naam (bijv. 'Outreach Pipeline')
  9. Klik op 'Create'
  10. Download het JSON-bestand (klik op de download-knop)
  11. Sla het op als 'credentials.json' in de projectmap:
"""
    )
    creds_path = Path(__file__).parent / "credentials.json"
    print(f"      {creds_path}\n")

    open_browser = input("  Wil je de Google Cloud Console nu openen in je browser? (j/n): ").strip().lower()
    if open_browser in ("j", "y", "ja", "yes"):
        webbrowser.open("https://console.cloud.google.com/apis/credentials")
        print("  Browser geopend.")

    # Step 3: Check credentials file
    print_step(3, "credentials.json controleren")
    if not creds_path.exists():
        print(f"  WAARSCHUWING: credentials.json niet gevonden op: {creds_path}")
        print("  Download het bestand van Google Cloud Console en sla het op als 'credentials.json'")
        wait = input("\n  Druk op Enter zodra je credentials.json hebt opgeslagen, of typ 'skip' om door te gaan: ").strip()
        if wait.lower() == "skip":
            print("  Stap overgeslagen - zorg dat je credentials.json toevoegt voor je de pipeline uitvoert.")
    else:
        # Validate the file
        try:
            with open(creds_path) as f:
                creds_data = json.load(f)
            if "installed" in creds_data or "web" in creds_data:
                print(f"  credentials.json gevonden en geldig: {creds_path}")
            else:
                print("  WAARSCHUWING: credentials.json lijkt ongeldig formaat te hebben.")
        except json.JSONDecodeError:
            print("  FOUT: credentials.json is geen geldig JSON-bestand.")

    # Step 4: Test OAuth flow
    print_step(4, "OAuth autorisatie uitvoeren")
    if not creds_path.exists():
        print("  Stap overgeslagen - credentials.json ontbreekt.")
    else:
        print(
            "  We gaan nu de OAuth-autorisatie uitvoeren."
            "\n  Er opent een browservenster waarbij je toestemming geeft."
            "\n  Je geeft alleen toegang tot het AANMAKEN van drafts (niet verzenden of lezen).\n"
        )
        proceed = input("  Doorgaan met autorisatie? (j/n): ").strip().lower()
        if proceed in ("j", "y", "ja", "yes"):
            try:
                from google_auth_oauthlib.flow import InstalledAppFlow
                from googleapiclient.discovery import build

                SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
                flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
                creds = flow.run_local_server(port=0)

                # Save token
                token_path = Path(__file__).parent / "token.json"
                with open(token_path, "w") as f:
                    f.write(creds.to_json())
                print(f"\n  Autorisatie geslaagd! Token opgeslagen: {token_path}")

                # Test the connection
                service = build("gmail", "v1", credentials=creds)
                profile = service.users().getProfile(userId="me").execute()
                email_addr = profile.get("emailAddress", "onbekend")
                print(f"  Verbonden met Gmail-account: {email_addr}")

            except Exception as e:
                print(f"\n  Fout tijdens autorisatie: {e}")
                print("  Probeer het opnieuw of controleer je credentials.json.")
        else:
            print("  Autorisatie overgeslagen.")

    # Step 5: Update .env
    print_step(5, ".env bestand bijwerken")
    env_path = Path(__file__).parent / ".env"
    print(f"  Zorg dat je .env bestand het volgende bevat:\n")
    print(f"  GMAIL_CREDENTIALS_PATH=credentials.json\n")

    if env_path.exists():
        with open(env_path) as f:
            env_content = f.read()
        if "GMAIL_CREDENTIALS_PATH" not in env_content:
            update = input("  GMAIL_CREDENTIALS_PATH staat niet in .env. Toevoegen? (j/n): ").strip().lower()
            if update in ("j", "y", "ja", "yes"):
                with open(env_path, "a") as f:
                    f.write("\nGMAIL_CREDENTIALS_PATH=credentials.json\n")
                print("  Toegevoegd aan .env")
    else:
        print(f"  .env bestand niet gevonden. Maak het aan met minimaal:")
        print(f"  ANTHROPIC_API_KEY=jouw_key\n  GMAIL_CREDENTIALS_PATH=credentials.json")

    # Final summary
    print("\n" + "=" * 60)
    print("  Setup voltooid!")
    print("=" * 60)
    print(
        "\nJe kunt nu de pipeline uitvoeren:"
        "\n  python run_pipeline.py --stage export"
        "\n  python run_pipeline.py --stage all --limit 5 --dry-run  (test)"
        "\n\nDe pipeline maakt CONCEPT-emails aan in Gmail - ze worden NIET verstuurd."
        "\nBekijk en bewerk de concepten in Gmail voordat je ze verstuurt.\n"
    )


if __name__ == "__main__":
    setup_gmail()
