"""
AI use: Claude (Anthropic) was used as a reference while writing this file
and src/pdfs_to_csv.py. We planned the approach ourselves and asked for
feedback and small examples along the way. Here it helped with:
- checking case numbers with a regex and fullmatch (load_case_numbers)
- finding the Participants table by its header (scrape_participants)
- reading the docket table out of the Drupal AJAX response (scrape_docket)
- making the batch run resumable with status files (completed_cases,
  check_unfinished_rows, batch_scrape)
Sections that used it are marked "Reference: (Anthropic, 2026)".

Anthropic. (2026). Claude [Large language model].
    https://claude.ai/share/bec36dfd-47c2-4ac1-887e-329860fa5cac
"""

import csv
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


INPUT_DIR = Path("data/raw_csvs")
OUTPUT_DIR = Path("data/raw_csvs/scraper_csvs")
MAX_CASES = None
REQUEST_DELAY = 0.5

CASE_RE = re.compile(r"\d{2}-R[CDM]-\d{5,6}")

DOCKET_COLUMNS = [
    "case_number", "activity_date", "document", "issued_filed_by"
]
PARTICIPANT_COLUMNS = ["case_number", "participant_raw_text"]
STATUS_COLUMNS = ["case_number", "status", "error"]

DOCKET_URL = (
    "https://www.nlrb.gov/sort-case-decisions-cp/{}/"
    "ds_activity+desc/case-docket-activity/ds-activity-date/1"
)


"""
Reference: (Anthropic, 2026). Removing spaces/en-dashes before matching,
and using fullmatch so the whole case number has to fit the pattern.
"""
def load_case_numbers(folder):
    csv_files = sorted(folder.glob("FY*/*.csv"))
    print(f"Found {len(csv_files)} CSV files")

    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {folder}")

    cases = set()

    for path in csv_files:
        with path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                raw = row.get("Case Number") or row.get("case_number") or ""
                case = re.sub(r"\s+", "", raw).upper().replace("–", "-")

                if CASE_RE.fullmatch(case):
                    cases.add(case)

    print(f"Collected {len(cases)} unique cases")
    return sorted(cases)


"""
Reference: (Anthropic, 2026). Finding the Participants table by its first
header and keeping the cell's lines with get_text("\\n", strip=True).
"""
def scrape_participants(session, case_number):
    url = f"https://www.nlrb.gov/case/{case_number}"
    response = session.get(url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    for table in soup.find_all("table"):
        headers = [
            th.get_text(" ", strip=True)
            for th in table.find_all("th")
        ]

        if headers and headers[0].lower() == "participant":
            records = []

            for row in table.find_all("tr"):
                cells = row.find_all("td")

                if cells:
                    # Keep line breaks between roles, names, and organizations.
                    text = cells[0].get_text("\n", strip=True)

                    if text:
                        records.append({
                            "case_number": case_number,
                            "participant_raw_text": text,
                        })

            return records

    raise ValueError(f"{case_number}: Participants table not found")


"""
Reference: (Anthropic, 2026). Picking the "insert" command out of the Drupal
AJAX JSON, parsing its HTML with BeautifulSoup, and checking that
items_per_page is set to All. We found the AJAX URL ourselves in the
browser's Network tab.
"""
def scrape_docket(session, case_number):
    response = session.get(
        DOCKET_URL.format(case_number),
        params={"_wrapper_format": "drupal_ajax"},
        timeout=30,
    )
    response.raise_for_status()

    commands = response.json()

    if not isinstance(commands, list):
        raise ValueError(f"{case_number}: Unexpected AJAX response")

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
        raise ValueError(f"{case_number}: Docket HTML not found")

    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.docket-activity-table")

    if table is None:
        raise ValueError(f"{case_number}: Docket table not found")

    # The regular case page only shows 10 entries; we need all of them.
    selected = soup.select_one(
        'select[name="items_per_page"] option[selected]'
    )

    if selected is None or selected.get_text(" ", strip=True).lower() != "all":
        raise ValueError(f"{case_number}: Docket page size is not All")

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

    return records


"""
Reference: (Anthropic, 2026). Resuming from status files: write the data
before the "ok" status, record failures too, and check for rows left
without a status so an interrupted run doesn't duplicate them.
Used for completed_cases, append_rows, check_unfinished_rows, batch_scrape.
"""
def completed_cases(path):
    done = set()

    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["status"] == "ok":
                    done.add(row["case_number"])

    return done


def append_rows(path, columns, records):
    new_file = not path.exists()

    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)

        if new_file:
            writer.writeheader()

        writer.writerows(records)


def check_unfinished_rows(data_file, status_file):
    # Avoid duplicating rows if a previous run was interrupted.
    if not data_file.exists():
        return

    done = completed_cases(status_file)

    with data_file.open(newline="", encoding="utf-8") as f:
        unfinished = {
            row["case_number"] for row in csv.DictReader(f)
        } - done

    if unfinished:
        sample = ", ".join(sorted(unfinished)[:5])

        raise RuntimeError(
            f"{data_file} has rows without an ok status ({sample}). "
            "Check those cases before resuming, or use a fresh output folder."
        )


def batch_scrape(case_numbers):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Track docket and participants separately.
    jobs = [
        (
            "docket",
            scrape_docket,
            OUTPUT_DIR / "docket_activity.csv",
            OUTPUT_DIR / "docket_scraping_status.csv",
            DOCKET_COLUMNS,
        ),
        (
            "participants",
            scrape_participants,
            OUTPUT_DIR / "case_participants_raw.csv",
            OUTPUT_DIR / "participant_scraping_status.csv",
            PARTICIPANT_COLUMNS,
        ),
    ]

    for _, _, data_file, status_file, _ in jobs:
        check_unfinished_rows(data_file, status_file)

    done = {
        name: completed_cases(status_file)
        for name, _, _, status_file, _ in jobs
    }

    remaining = [
        case for case in case_numbers
        if any(case not in done[name] for name, *_ in jobs)
    ]

    if MAX_CASES is not None:
        remaining = remaining[:MAX_CASES]

    print(f"Cases this run: {len(remaining)}")

    with requests.Session() as session:
        for index, case_number in enumerate(remaining, start=1):
            print(f"[{index}/{len(remaining)}] {case_number}")

            for name, scraper, data_file, status_file, columns in jobs:
                if case_number in done[name]:
                    continue

                try:
                    records = scraper(session, case_number)

                    # Save records before marking the case as complete.
                    append_rows(data_file, columns, records)

                    append_rows(status_file, STATUS_COLUMNS, [{
                        "case_number": case_number,
                        "status": "ok",
                        "error": "",
                    }])

                    done[name].add(case_number)
                    print(f"  {name}: {len(records)} rows")

                except (
                    requests.RequestException,
                    ValueError,
                    TypeError,
                    KeyError,
                ) as error:
                    append_rows(status_file, STATUS_COLUMNS, [{
                        "case_number": case_number,
                        "status": "failed",
                        "error": str(error),
                    }])

                    print(f"  {name}: failed ({error})")

                    if (
                        isinstance(error, requests.HTTPError)
                        and error.response is not None
                    ):
                        status = error.response.status_code

                        if status in (403, 429) or status >= 500:
                            print("Stopping due to a possible website error.")
                            return

                time.sleep(REQUEST_DELAY)


if __name__ == "__main__":
    cases = load_case_numbers(INPUT_DIR)
    batch_scrape(cases)
