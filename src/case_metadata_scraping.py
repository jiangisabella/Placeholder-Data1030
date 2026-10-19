"""
Case scraper (case details + tallies only).

For each case number found in the input CSVs, scrapes
https://www.nlrb.gov/case/<case_number> and writes:

  1. case_details.csv  - one row per case:
        Date Filed, Status, Location, Region Assigned, Reason Closed
  2. case_tallies.csv  - one row per tally (a case may have several):
        Tally Issued Date, Status, No. of Eligible Voters, Void Ballots,
        Total Ballots Counted, Challenged Ballots, Votes for Labor Union,
        Votes Against, Tally Type, Ballot Type, Unit ID, Challenges Determinative

"Status" appears twice on a page (case status, then tally status). The first
one (before any tally block starts) is the case status; once a tally begins,
it is the tally status.
"""

import csv
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


INPUT_DIR = Path("../data/raw_csvs")
OUTPUT_DIR = Path("data")

MAX_CASES = 20000
REQUEST_DELAY = 0.5
CASE_PATTERN = re.compile(r"^\d{2}-R[CDM]-\d{5,6}$")

CASE_DETAILS_FILE = OUTPUT_DIR / "case_details.csv"
TALLY_FILE = OUTPUT_DIR / "case_tallies.csv"
STATUS_FILE = OUTPUT_DIR / "details_scraping_status.csv"

# Page label -> output column name
CASE_FIELDS = {
    "Date Filed": "date_filed",
    "Status": "case_status",
    "Location": "location",
    "Region Assigned": "region_assigned",
    "Reason Closed": "reason_closed",
}

TALLY_FIELDS = {
    "Tally Issued Date": "tally_issued_date",
    "Status": "tally_status",
    "No. of Eligible Voters": "eligible_voters",
    "Void Ballots": "void_ballots",
    "Total Ballots Counted": "total_ballots_counted",
    "Challenged Ballots": "challenged_ballots",
    "Votes for Labor Union": "votes_for_union",
    "Votes Against": "votes_against",
    "Tally Type": "tally_type",
    "Ballot Type": "ballot_type",
    "Unit ID": "unit_id",
    "Challenges Determinative": "challenges_determinative",
}

CASE_DETAILS_COLUMNS = ["case_number"] + list(CASE_FIELDS.values())
TALLY_COLUMNS = ["case_number"] + list(TALLY_FIELDS.values())
STATUS_COLUMNS = ["case_number", "status", "tally_count", "error"]

ALL_LABELS = set(CASE_FIELDS) | set(TALLY_FIELDS)

HEADERS = {"User-Agent": "DATA1030 student research project"}


def load_case_numbers(folder):
    cases = set()
    yearly_counts = {}

    csv_files = sorted(folder.glob("FY*/*.csv"))
    print(f"Found {len(csv_files)} CSV files")

    for csv_path in csv_files:
        fiscal_year = csv_path.parent.name
        yearly_counts.setdefault(fiscal_year, set())

        with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            for row in reader:
                case_number = (
                    row.get("Case Number")
                    or row.get("case_number")
                    or ""
                ).strip().upper()

                if CASE_PATTERN.fullmatch(case_number):
                    cases.add(case_number)
                    yearly_counts[fiscal_year].add(case_number)

    print("\nUnique cases by fiscal year:")
    for year in sorted(yearly_counts):
        print(f"{year}: {len(yearly_counts[year])}")

    return sorted(cases)


def extract_label_value_pairs(soup):
    """Turn visible page text into (label, value) pairs for known labels only.

    Handles both "Label: value" on one line and "Label:" followed by the
    value on the next line.
    """
    lines = [
        line.strip()
        for line in soup.get_text("\n", strip=True).split("\n")
        if line.strip()
    ]

    pairs = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Case 1: "Label: value" on a single line
        if ":" in line:
            label, _, value = line.partition(":")
            label = label.strip()
            if label in ALL_LABELS and value.strip():
                pairs.append((label, value.strip()))
                i += 1
                continue

        # Case 2: "Label:" with the value on the next line
        label = line.rstrip(":").strip()
        if label in ALL_LABELS and i + 1 < len(lines):
            next_line = lines[i + 1]
            if next_line.rstrip(":").strip() in ALL_LABELS:
                # label with an empty value
                pairs.append((label, ""))
                i += 1
            else:
                pairs.append((label, next_line))
                i += 2
            continue

        i += 1

    return pairs


def extract_case_details_and_tallies(soup, case_number):
    """Split the label/value pairs into one case-details row and N tally rows."""
    details = {"case_number": case_number}
    tallies = []
    current_tally = None

    for label, value in extract_label_value_pairs(soup):
        # "Tally Issued Date" starts a new tally block
        if label == "Tally Issued Date":
            current_tally = {"case_number": case_number}
            tallies.append(current_tally)

        if current_tally is not None and label in TALLY_FIELDS:
            current_tally[TALLY_FIELDS[label]] = value
        elif label in CASE_FIELDS:
            # first occurrence wins
            details.setdefault(CASE_FIELDS[label], value)

    return details, tallies


def scrape_case(case_number, session):
    url = f"https://www.nlrb.gov/case/{case_number}"
    response = session.get(url, headers=HEADERS, timeout=30)

    if response.status_code in (403, 429):
        raise RuntimeError(f"Access restricted: HTTP {response.status_code}")

    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    details, tallies = extract_case_details_and_tallies(soup, case_number)

    if len(details) == 1:  # only case_number, nothing was parsed
        raise ValueError("No case details found on page")

    return details, tallies


def append_rows(path, rows, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)

        if write_header:
            writer.writeheader()

        writer.writerows(rows)


def load_completed_cases(path):
    completed = set()

    if not path.exists():
        return completed

    with path.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("status") == "success":
                completed.add(row["case_number"])

    return completed


def batch_scrape(case_numbers):
    completed = load_completed_cases(STATUS_FILE)

    remaining = [case for case in case_numbers if case not in completed]

    if MAX_CASES is not None:
        remaining = remaining[:MAX_CASES]

    print(f"\nTotal cases: {len(case_numbers)}")
    print(f"Already completed: {len(completed)}")
    print(f"Cases to scrape this run: {len(remaining)}")

    with requests.Session() as session:
        for index, case_number in enumerate(remaining, start=1):
            print(f"\n[{index}/{len(remaining)}] Scraping {case_number}")

            try:
                details, tallies = scrape_case(case_number, session)

                append_rows(CASE_DETAILS_FILE, [details], CASE_DETAILS_COLUMNS)
                if tallies:
                    append_rows(TALLY_FILE, tallies, TALLY_COLUMNS)

                append_rows(STATUS_FILE, [{
                    "case_number": case_number,
                    "status": "success",
                    "tally_count": len(tallies),
                    "error": "",
                }], STATUS_COLUMNS)
                completed.add(case_number)

                print(f"Success: {len(tallies)} tallies")

            except RuntimeError as error:
                print(f"Stopped: {error}")
                break

            except (requests.RequestException, ValueError) as error:
                print(f"Failed: {error}")

            if index < len(remaining):
                time.sleep(REQUEST_DELAY)


if __name__ == "__main__":
    cases = load_case_numbers(INPUT_DIR)
    print(f"\nTotal unique cases: {len(cases)}")

    if not cases:
        print("No cases found. Check the CSV directory.")
    else:
        batch_scrape(cases)