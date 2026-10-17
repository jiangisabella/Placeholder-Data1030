
import csv
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


INPUT_DIR = Path("data/raw_csvs")
OUTPUT_DIR = Path("data")

MAX_CASES = None
REQUEST_DELAY = 0.5
CASE_PATTERN = re.compile(r"^\d{2}-R[CDM]-\d{5,6}$")

DOCKET_FILE = OUTPUT_DIR / "docket_activity.csv"
STATUS_FILE = OUTPUT_DIR / "docket_scraping_status.csv"
PARTICIPANT_FILE = OUTPUT_DIR / "case_participants_raw.csv"
PARTICIPANT_STATUS_FILE = OUTPUT_DIR / "participant_scraping_status.csv"

DOCKET_COLUMNS = [
    "case_number", "activity_date", "document", "issued_filed_by"
]

PARTICIPANT_COLUMNS = [
    "case_number", "participant_raw_text"
]

STATUS_COLUMNS = [
    "case_number", "status", "activity_count", "error"
]

PARTICIPANT_STATUS_COLUMNS = [
    "case_number", "status", "participant_count", "error"
]

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


def scrape_case(case_number, session):
    url = f"https://www.nlrb.gov/case/{case_number}"
    response = session.get(url, headers=HEADERS, timeout=30)

    if response.status_code in (403, 429):
        raise RuntimeError(f"Access restricted: HTTP {response.status_code}")

    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    docket_table = None
    participant_table = None

    for table in soup.find_all("table"):
        headers = [
            th.get_text(" ", strip=True).lower()
            for th in table.find_all("th")
        ]

        if "date" in headers and "document" in headers:
            docket_table = table

        if any("participant" in h for h in headers):
            participant_table = table

    if docket_table is None:
        raise ValueError("Docket Activity table not found")

    if participant_table is None:
        raise ValueError("Participants table not found")

    docket_records = []

    for row in docket_table.find_all("tr"):
        cells = row.find_all("td")

        if len(cells) < 3:
            continue

        docket_records.append({
            "case_number": case_number,
            "activity_date": cells[0].get_text(" ", strip=True),
            "document": cells[1].get_text(" ", strip=True),
            "issued_filed_by": cells[2].get_text(" ", strip=True),
        })

    participant_records = []

    for row in participant_table.find_all("tr"):
        cells = row.find_all("td")

        if not cells:
            continue

        raw_text = cells[0].get_text("\n", strip=True)

        if raw_text:
            participant_records.append({
                "case_number": case_number,
                "participant_raw_text": raw_text,
            })

    return docket_records, participant_records


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
    docket_completed = load_completed_cases(STATUS_FILE)
    participant_completed = load_completed_cases(PARTICIPANT_STATUS_FILE)

    remaining = [
        case for case in case_numbers
        if case not in docket_completed or case not in participant_completed
    ]

    if MAX_CASES is not None:
        remaining = remaining[:MAX_CASES]

    print(f"\nTotal cases: {len(case_numbers)}")
    print(f"Docket completed: {len(docket_completed)}")
    print(f"Participants completed: {len(participant_completed)}")
    print(f"Cases to scrape this run: {len(remaining)}")

    with requests.Session() as session:
        for index, case_number in enumerate(remaining, start=1):
            print(f"\n[{index}/{len(remaining)}] Scraping {case_number}")

            try:
                docket, participants = scrape_case(case_number, session)

                if case_number not in docket_completed:
                    append_rows(DOCKET_FILE, docket, DOCKET_COLUMNS)
                    append_rows(STATUS_FILE, [{
                        "case_number": case_number,
                        "status": "success",
                        "activity_count": len(docket),
                        "error": "",
                    }], STATUS_COLUMNS)
                    docket_completed.add(case_number)

                if case_number not in participant_completed:
                    append_rows(PARTICIPANT_FILE, participants, PARTICIPANT_COLUMNS)
                    append_rows(PARTICIPANT_STATUS_FILE, [{
                        "case_number": case_number,
                        "status": "success",
                        "participant_count": len(participants),
                        "error": "",
                    }], PARTICIPANT_STATUS_COLUMNS)
                    participant_completed.add(case_number)

                print(
                    f"Success: {len(docket)} activities, "
                    f"{len(participants)} participants"
                )

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
