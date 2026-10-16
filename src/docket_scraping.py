
import csv
from pathlib import Path

import requests
from bs4 import BeautifulSoup


def scrape_docket(case_number):
    """
    Scrape docket activities for one NLRB case.

    Returns a list of dictionaries.
    """
    url = f"https://www.nlrb.gov/case/{case_number}"

    response = requests.get(url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Locate the docket activity table
    docket_table = None

    for table in soup.find_all("table"):
        headers = [
            th.get_text(" ", strip=True)
            for th in table.find_all("th")
        ]

        if "Date" in headers and "Document" in headers:
            docket_table = table
            break

    if docket_table is None:
        print(f"No docket table found: {case_number}")
        return []

    # Extract docket activity rows
    records = []

    for row in docket_table.find_all("tr")[1:]:
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


def save_to_csv(records, filename):
    """Save extracted docket activities to a CSV file."""
    output_path = Path(filename)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "case_number",
        "activity_date",
        "document",
        "issued_filed_by",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f"Saved {len(records)} rows to {output_path}")


if __name__ == "__main__":
    test_cases = [
        "15-RC-388654",
    ]

    all_activities = []

    for case_number in test_cases:
        print(f"\nScraping {case_number}...")

        activities = scrape_docket(case_number)

        print(f"Found {len(activities)} activities")
        all_activities.extend(activities)

    save_to_csv(
        all_activities,
        "data/docket_activity_sample.csv"
    )

