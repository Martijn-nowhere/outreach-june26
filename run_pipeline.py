"""
Main pipeline orchestrator for Dutch B2B outreach automation.

Usage:
  python run_pipeline.py --stage all          # run everything
  python run_pipeline.py --stage discover     # just company discovery
  python run_pipeline.py --stage scan         # just CSR scanning
  python run_pipeline.py --stage contacts     # just contact finding
  python run_pipeline.py --stage draft        # just draft generation
  python run_pipeline.py --stage export       # just export
  python run_pipeline.py --limit 10           # test with 10 companies
  python run_pipeline.py --dry-run            # don't call APIs, use mock data
  python run_pipeline.py --stage all --limit 5 --dry-run  # full dry-run test
"""
import argparse
import importlib.util
import json
import logging
import sys
import time
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))
import config


def _load_stage_module(filename: str):
    """Load a pipeline stage module by filename (handles numeric prefixes)."""
    module_path = PROJECT_ROOT / "pipeline" / filename
    spec = importlib.util.spec_from_file_location(filename.replace(".py", ""), module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

STAGES = ["discover", "scan", "contacts", "draft", "export"]
STAGE_ALIASES = {
    "discover": "discover",
    "1": "discover",
    "scan": "scan",
    "2": "scan",
    "contacts": "contacts",
    "3": "contacts",
    "draft": "draft",
    "4": "draft",
    "export": "export",
    "5": "export",
    "all": "all",
}


def print_banner():
    print("\n" + "=" * 60)
    print("  Dutch B2B Outreach Automation Pipeline")
    print("  Target: NL CSR/MVO leads | Education & Sustainability")
    print("=" * 60 + "\n")


def print_config_warnings(dry_run: bool):
    issues = config.validate_config()
    if issues and not dry_run:
        log.warning("Configuration warnings:")
        for issue in issues:
            log.warning("  - %s", issue)
        print()


def run_discover(limit: int | None, dry_run: bool) -> bool:
    log.info("--- STAGE 1: Company Discovery ---")
    try:
        stage = _load_stage_module("1_discover_companies.py")
        result = stage.run(limit=limit, dry_run=dry_run)
        return bool(result)
    except Exception as e:
        log.error("Stage 1 failed: %s", e)
        import traceback
        traceback.print_exc()
        return False


def run_scan(limit: int | None, dry_run: bool) -> bool:
    log.info("--- STAGE 2: CSR Report Scanning ---")
    try:
        stage = _load_stage_module("2_scan_csr.py")
        stage.run(limit=limit, dry_run=dry_run)
        return True
    except Exception as e:
        log.error("Stage 2 failed: %s", e)
        import traceback
        traceback.print_exc()
        return False


def run_contacts(limit: int | None, dry_run: bool) -> bool:
    log.info("--- STAGE 3: Contact Finding ---")
    try:
        stage = _load_stage_module("3_find_contacts.py")
        stage.run(limit=limit, dry_run=dry_run)
        return True
    except Exception as e:
        log.error("Stage 3 failed: %s", e)
        import traceback
        traceback.print_exc()
        return False


def run_draft(limit: int | None, dry_run: bool) -> bool:
    log.info("--- STAGE 4: Draft Generation ---")
    try:
        stage = _load_stage_module("4_draft_outreach.py")
        stage.run(limit=limit, dry_run=dry_run)
        return True
    except Exception as e:
        log.error("Stage 4 failed: %s", e)
        import traceback
        traceback.print_exc()
        return False


def run_export(limit: int | None, dry_run: bool) -> bool:
    log.info("--- STAGE 5: Export ---")
    try:
        stage = _load_stage_module("5_export.py")
        result = stage.run(limit=limit, dry_run=dry_run)
        if result:
            log.info("Export results:")
            for k, v in result.items():
                log.info("  %s: %s", k, v)
        return True
    except Exception as e:
        log.error("Stage 5 failed: %s", e)
        import traceback
        traceback.print_exc()
        return False


def print_progress_summary():
    """Print a summary of current pipeline state."""
    if not config.PROGRESS_JSON.exists():
        return
    with open(config.PROGRESS_JSON) as f:
        progress = json.load(f)

    stages_count = {}
    for company, data in progress.items():
        stage = data.get("stage", "unknown")
        stages_count[stage] = stages_count.get(stage, 0) + 1

    print("\n--- Pipeline Progress Summary ---")
    for stage, count in sorted(stages_count.items()):
        print(f"  {stage}: {count} companies")

    if config.CSR_ANALYSIS_CSV.exists():
        import csv
        with open(config.CSR_ANALYSIS_CSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        relevant = [r for r in rows if int(r.get("relevance_score", 0)) >= config.RELEVANCE_THRESHOLD]
        print(f"\n  CSR scanned: {len(rows)} companies")
        print(f"  Relevant (score >= {config.RELEVANCE_THRESHOLD}): {len(relevant)}")

    if config.CONTACTS_CSV.exists():
        import csv
        with open(config.CONTACTS_CSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        with_email = [r for r in rows if r.get("contact_email")]
        print(f"  Contacts found: {len(rows)} total, {len(with_email)} with email")

    if config.OUTREACH_DRAFTS_CSV.exists():
        import csv
        with open(config.OUTREACH_DRAFTS_CSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        print(f"  Drafts generated: {len(rows)}")

    apollo_path = config.DATA_DIR / "export_apollo.csv"
    if apollo_path.exists():
        import csv
        with open(apollo_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        print(f"  Apollo export rows: {len(rows)}")

    print()


def main():
    print_banner()

    parser = argparse.ArgumentParser(
        description="Dutch B2B Outreach Automation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--stage",
        default="all",
        choices=list(STAGE_ALIASES.keys()),
        help="Which stage to run (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of companies to process (useful for testing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use mock data, skip real API calls and web requests",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print pipeline progress summary and exit",
    )
    args = parser.parse_args()

    if args.status:
        print_progress_summary()
        return

    stage = STAGE_ALIASES.get(args.stage, args.stage)
    limit = args.limit
    dry_run = args.dry_run

    if dry_run:
        log.info("DRY-RUN mode enabled - using mock data, no real API calls")

    if limit:
        log.info("Limit set to %d companies", limit)

    print_config_warnings(dry_run)

    # Determine which stages to run
    run_all = stage == "all"

    start_time = time.time()
    success = True

    if run_all or stage == "discover":
        ok = run_discover(limit, dry_run)
        if not ok:
            log.error("Stage 1 (discover) failed - stopping")
            sys.exit(1)

    if run_all or stage == "scan":
        ok = run_scan(limit, dry_run)
        if not ok:
            log.error("Stage 2 (scan) failed - stopping")
            sys.exit(1)

    if run_all or stage == "contacts":
        ok = run_contacts(limit, dry_run)
        if not ok:
            log.error("Stage 3 (contacts) failed - stopping")
            sys.exit(1)

    if run_all or stage == "draft":
        ok = run_draft(limit, dry_run)
        if not ok:
            log.error("Stage 4 (draft) failed - stopping")
            sys.exit(1)

    if run_all or stage == "export":
        ok = run_export(limit, dry_run)
        if not ok:
            log.error("Stage 5 (export) failed")
            success = False

    elapsed = time.time() - start_time
    print_progress_summary()
    log.info("Pipeline completed in %.1f seconds. Success=%s", elapsed, success)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
