"""
Download the NLRB monthly election reports (FY2012 onward) and turn each PDF
into a raw CSV. Values are copied as-is; cleaning is a separate step
(see cleaning_plan.md).

    python src/pdfs_to_csv.py

Change DOWNLOAD / REDO_CSVS below to pick what runs. Anything already on
disk is skipped, so it's safe to stop and rerun.

Output:
    data/raw_pdfs/<FY>/*.pdf     downloaded reports
    data/raw_csvs/<FY>/*.csv     one CSV per report
    data/skipped_pdfs.txt        reports with no tables (image-only PDFs) or no elections
"""

import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup


# Configuration

DATA_DIR = Path("data")
PDF_DIR = DATA_DIR / "raw_pdfs"
CSV_DIR = DATA_DIR / "raw_csvs"
SKIPPED_FILE = DATA_DIR / "skipped_pdfs.txt"

INDEX_URL = "https://www.nlrb.gov/reports/agency-performance/election-reports"
FIRST_FY = 2012  # FY2012 = Oct 2011 to Sep 2012

DOWNLOAD = True    # False = only convert PDFs already on disk
REDO_CSVS = False  # True = rebuild CSVs that already exist
REQUEST_DELAY = 1  # seconds between requests

HEADERS = {
    "User-Agent": "DATA1030 student research project"
}


# 1. Download the PDFs

def get_page(url, session):
    response = session.get(url, headers=HEADERS, timeout=60)

    if response.status_code in (403, 429):
        raise RuntimeError(f"Access restricted: HTTP {response.status_code}")

    response.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return response


def find_links(url, session):
    # every link on the page as a full url, in page order, no repeats
    soup = BeautifulSoup(get_page(url, session).text, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        link = urljoin(url, a["href"])
        if link not in links:
            links.append(link)
    return links


def is_pdf(url):
    return url.lower().endswith(".pdf")


def download_all():
    with requests.Session() as session:
        index_links = find_links(INDEX_URL, session)

        # PDFs linked straight from the index are the yearly summaries, not monthly reports
        summary_pdfs = [link for link in index_links if is_pdf(link)]

        # the index links one page per fiscal year, like .../election-reports-fy-2026
        year_pages = []
        for link in index_links:
            m = re.search(r"election-reports-fy-(\d{4})", link)
            if m and int(m.group(1)) >= FIRST_FY:
                page = ("FY" + m.group(1), link)
                if page not in year_pages:
                    year_pages.append(page)
        print(f"{len(year_pages)} fiscal years to check")

        failed = []
        try:
            for fy, page_url in year_pages:
                for pdf_url in find_links(page_url, session):
                    name = pdf_url.split("/")[-1]
                    path = PDF_DIR / fy / name

                    # skip non-PDFs, the yearly totals, and anything already downloaded
                    if not is_pdf(pdf_url) or pdf_url in summary_pdfs:
                        continue
                    if "total" in name.lower() or path.exists():
                        continue

                    try:
                        content = get_page(pdf_url, session).content
                    except requests.RequestException as error:
                        print(f"  Failed: {fy}/{name} ({error})")
                        failed.append(pdf_url)
                        continue

                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                    print(f"  got {fy}/{name}")

        except RuntimeError as error:
            print(f"Stopped: {error}")

    if failed:
        print(f"\n{len(failed)} downloads failed, run again to retry:")
        for url in failed:
            print("  " + url)


# 2. Read the tables in each PDF

CASE_NO = r"\d{2}-[A-Z]{2}-\d{5,6}"  # e.g. 01-RD-386098


def clean_cell(cell):
    """Text that wraps inside a cell comes back with newlines; flatten it."""
    return " ".join(cell.split()) if cell else ""


def add_leftover(row, leftover):
    """Glue the tail of a row that a page break cut off back onto the row.
    Section titles like "NLRB Elections with 3 Labor Organizations" are ignored."""
    for col, text in leftover.items():
        if text and col != "Case Number" and not text.startswith("NLRB Elections"):
            row[col] = f"{row.get(col, '')} {text}".strip()


def read_tables(pdf, source):
    """read the election rows out of the PDF's tables.

    Page breaks cause three problems, handled below:
      - a table continuing on a new page doesn't always repeat its header
        -> reuse the previous table's header
      - the header itself can be split across the break
        -> merge the two halves (cut_header)
      - a row can be split across the break; the leftover at the top of the
        next page has no case number -> glue it onto the last row read.
        Tables don't always come out in page order, so the last row is
        looked up by table width (number of columns).
    """
    rows = []
    last_header = None      # header of the previous table
    cut_header = None       # previous table was only a header, with no rows
    last_row_by_width = {}  # number of columns -> last row from a table that wide

    for page_no, page in enumerate(pdf.pages, start=1):
        prev_rows_by_width = dict(last_row_by_width)
        prev_last_row = rows[-1] if rows else None

        for table in page.extract_tables():
            header = None
            found = 0         # rows with a case number in this table
            under_header = 0  # non-empty rows after the header

            for cells in table:
                cells = [clean_cell(c) for c in cells]
                has_case_no = re.search(CASE_NO, " ".join(cells))

                # the summary table at the top of each report has no "Case Number"
                # column, so it never gets a header and is skipped
                if "Case Number" in cells:
                    header = cells
                    continue
                if header is None and not has_case_no:
                    if cut_header and len(cells) == len(cut_header):  # 2nd half of a split header
                        cut_header = [f"{h} {c}".strip() for h, c in zip(cut_header, cells)]
                    continue
                if header is None:
                    header = cut_header or last_header
                if header is None or len(cells) != len(header):
                    continue
                if any(cells):
                    under_header += 1

                row = dict(zip(header, cells))
                if re.fullmatch(CASE_NO, row.get("Case Number", "")):  # .get: a merged header can rename it
                    row.update(source_pdf=source, extraction_method="table", page=page_no)
                    rows.append(row)
                    last_row_by_width[len(cells)] = row
                    found += 1
                elif found == 0 and not has_case_no:
                    prev = prev_rows_by_width.get(len(cells)) or prev_last_row
                    if prev:
                        add_leftover(prev, row)

            if header:
                last_header = header
            cut_header = header if header and under_header == 0 else None

    return rows


# 3. Write the CSVs

def write_csv(rows, path):
    # keep the PDF's own columns, in the order they first appear
    columns = ["source_pdf", "extraction_method", "page"]
    for row in rows:
        for col in row:
            if col not in columns:
                columns.append(col)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_pdf(pdf_path, source):
    """Returns the table rows and the first page's text."""
    with pdfplumber.open(pdf_path) as pdf:
        first_page = " ".join((pdf.pages[0].extract_text() or "").split())
        return read_tables(pdf, source), first_page


def convert_all():
    pdfs = sorted(PDF_DIR.glob("FY*/*.pdf"))
    print(f"\n{len(pdfs)} PDFs on disk")

    # PDFs skipped last time are slow to open, so don't retry them unless REDO_CSVS
    skipped = []
    if SKIPPED_FILE.exists() and not REDO_CSVS:
        skipped = SKIPPED_FILE.read_text(encoding="utf-8").splitlines()
    already_skipped = [line.split(":")[0] for line in skipped]

    for pdf_path in pdfs:
        fy = pdf_path.parent.name
        if int(fy[2:]) < FIRST_FY:
            continue
        source = f"{fy}/{pdf_path.name}"
        csv_path = CSV_DIR / fy / f"{pdf_path.stem}.csv"
        if not REDO_CSVS and (csv_path.exists() or source in already_skipped):
            continue

        rows, first_page = parse_pdf(pdf_path, source)

        if not rows:
            # either the report really has no elections (Oct 2025), or the PDF
            # is just pictures of a table with no text (most of 2023, Apr 2019)
            why = ("report has no elections" if "produced no results" in first_page
                   else "no readable text in the PDF")
            skipped.append(f"{source}: {why}")
            print(f"  {source}: skipped ({why})")
            continue

        write_csv(rows, csv_path)
        print(f"  {source}: {len(rows)} rows")

    if skipped:
        SKIPPED_FILE.parent.mkdir(parents=True, exist_ok=True)
        SKIPPED_FILE.write_text("\n".join(skipped) + "\n", encoding="utf-8")
        print(f"\n{len(skipped)} PDFs skipped, see {SKIPPED_FILE}")


# 4. Main

if __name__ == "__main__":
    if DOWNLOAD:
        download_all()
    convert_all()
