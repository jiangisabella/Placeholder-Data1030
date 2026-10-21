
import csv
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# Configuration

DATA_DIR = Path("data")

OLD_DOCKET_FILE = DATA_DIR / "docket_activity.csv"
NEW_DOCKET_FILE = DATA_DIR / "docket_activity_full.csv"
MERGED_DOCKET_FILE = DATA_DIR / "docket_activity_merged.csv"
CHECKPOINT_FILE = DATA_DIR / "docket_full_checkpoint.sqlite"

MAX_CASES = None
REQUEST_DELAY = 0.5

DOCKET_COLUMNS = [
    "case_number",
    "activity_date",
    "document",
    "issued_filed_by",
]

HEADERS = {
    "User-Agent": "DATA1030 student research project"
}


# 1. Find cases with exactly 10 docket records

def load_truncated_cases():
    with OLD_DOCKET_FILE.open(
        "r", newline="", encoding="utf-8"
    ) as f:
        counts = Counter(
            row["case_number"]
            for row in csv.DictReader(f)
        )

    cases = sorted(
        case for case, count in counts.items()
        if count == 10
    )

    print(f"Cases with exactly 10 activities: {len(cases)}")
    return cases


# 2. Scrape the full docket using NLRB's AJAX endpoint

def scrape_full_docket(case_number, session):
    url = (
        f"https://www.nlrb.gov/sort-case-decisions-cp/{case_number}"
        "/ds_activity+desc/case-docket-activity/ds-activity-date/1"
    )

    response = session.get(
        url,
        params={"_wrapper_format": "drupal_ajax"},
        headers=HEADERS,
        timeout=30,
    )

    if response.status_code in (403, 429):
        raise RuntimeError(
            f"Access restricted: HTTP {response.status_code}"
        )

    if response.status_code >= 500:
        raise RuntimeError(
            f"NLRB server error: HTTP {response.status_code}"
        )

    response.raise_for_status()
    commands = response.json()

    if not isinstance(commands, list):
        raise ValueError("Unexpected AJAX response")

    html = None

    for item in commands:
        if (
            isinstance(item, dict)
            and item.get("command") == "insert"
            and item.get("selector") == "#case_docket_activity_data"
        ):
            html = item.get("data")
            break

    if not html:
        raise ValueError("Docket HTML not found")

    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.docket-activity-table")

    if table is None:
        raise ValueError("Docket table not found")

    # Check that the AJAX response is showing "All".
    page_size = soup.select_one(
        'select[name="items_per_page"] option[selected]'
    )

    if page_size is None or page_size.get_text(
        " ", strip=True
    ).lower() != "all":
        raise ValueError("AJAX response is not showing All entries")

    records = []

    for row in table.select("tbody tr"):
        cells = row.find_all("td")

        if len(cells) < 3:
            continue

        records.append({
            "case_number": case_number,
            "activity_date": cells[0].get_text(" ", strip=True),
            "document": cells[1].get_text(" ", strip=True),
            "issued_filed_by": cells[2].get_text(" ", strip=True),
        })

    # These cases previously had 10 records.
    # Fewer than 10 suggests a scraping problem.
    if len(records) < 10:
        raise ValueError(
            f"Expected at least 10 activities, got {len(records)}"
        )

    return records


# 3. Store completed cases safely

def open_checkpoint():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(CHECKPOINT_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS completed_cases (
            case_number TEXT PRIMARY KEY,
            docket_json TEXT NOT NULL
        )
    """)

    conn.commit()
    return conn


def load_completed_cases(conn):
    rows = conn.execute(
        "SELECT case_number FROM completed_cases"
    ).fetchall()

    return {row[0] for row in rows}


def save_case(conn, case_number, records):
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO completed_cases
            (case_number, docket_json)
            VALUES (?, ?)
            """,
            (case_number, json.dumps(records)),
        )


# 4. Export the re-scraped docket data

def export_full_docket(conn):
    temporary_file = NEW_DOCKET_FILE.with_suffix(".csv.tmp")

    with temporary_file.open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=DOCKET_COLUMNS
        )
        writer.writeheader()

        for case_number, docket_json in conn.execute("""
            SELECT case_number, docket_json
            FROM completed_cases
            ORDER BY case_number
        """):
            writer.writerows(json.loads(docket_json))

    temporary_file.replace(NEW_DOCKET_FILE)


# 5. Merge new full docket with the original data

def export_merged_docket(conn):
    replaced_cases = load_completed_cases(conn)

    temporary_file = MERGED_DOCKET_FILE.with_suffix(
        ".csv.tmp"
    )

    old_rows = 0
    new_rows = 0

    with temporary_file.open(
        "w", newline="", encoding="utf-8"
    ) as output:
        writer = csv.DictWriter(
            output, fieldnames=DOCKET_COLUMNS
        )
        writer.writeheader()

        # Keep old records unless that case was re-scraped.
        with OLD_DOCKET_FILE.open(
            "r", newline="", encoding="utf-8"
        ) as original:
            for row in csv.DictReader(original):
                if row["case_number"] not in replaced_cases:
                    writer.writerow({
                        col: row[col] for col in DOCKET_COLUMNS
                    })
                    old_rows += 1

        # Add complete records for re-scraped cases.
        for case_number, docket_json in conn.execute("""
            SELECT case_number, docket_json
            FROM completed_cases
            ORDER BY case_number
        """):
            records = json.loads(docket_json)
            writer.writerows(records)
            new_rows += len(records)

    temporary_file.replace(MERGED_DOCKET_FILE)

    print(f"\nCases replaced: {len(replaced_cases)}")
    print(f"Old rows retained: {old_rows}")
    print(f"New full-docket rows: {new_rows}")
    print(f"Total merged rows: {old_rows + new_rows}")
    print(f"Merged file: {MERGED_DOCKET_FILE}")


# 6. Batch scrapi

def batch_scrape(case_numbers):
    conn = open_checkpoint()

    try:
        completed = load_completed_cases(conn)

        remaining = [
            case for case in case_numbers
            if case not in completed
        ]

        if MAX_CASES is not None:
            remaining = remaining[:MAX_CASES]

        print(f"\nTotal cases to fix: {len(case_numbers)}")
        print(f"Previously fixed: {len(completed)}")
        print(f"Cases this run: {len(remaining)}")

        with requests.Session() as session:
            for index, case_number in enumerate(
                remaining, start=1
            ):
                print(
                    f"\n[{index}/{len(remaining)}] "
                    f"Scraping {case_number}"
                )

                try:
                    records = scrape_full_docket(
                        case_number, session
                    )

                    save_case(conn, case_number, records)

                    print(
                        f"Success: {len(records)} activities"
                    )

                except RuntimeError as error:
                    print(f"Stopped: {error}")
                    break

                except (
                    requests.RequestException,
                    ValueError,
                    TypeError,
                    KeyError,
                ) as error:
                    print(f"Failed: {error}")

                if index < len(remaining):
                    time.sleep(REQUEST_DELAY)

                if index % 100 == 0:
                    print(
                        f"\nProgress: {index}/{len(remaining)}"
                    )

    finally:
        print("\nExporting CSV files...")
        export_full_docket(conn)
        export_merged_docket(conn)
        conn.close()


# 7. Main

if __name__ == "__main__":
    cases = load_truncated_cases()

    if not cases:
        print("No cases need re-scraping.")
    else:
        batch_scrape(cases)
