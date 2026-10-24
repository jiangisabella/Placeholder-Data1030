"""
Download the NLRB monthly election reports (FY2012 onward) and turn each PDF
into a raw CSV. Values are copied as-is; cleaning is a separate step
(see cleaning_plan.md).

    python src/pdfs_to_csv.py                 # download new PDFs, convert new ones
    python src/pdfs_to_csv.py --redo          # rebuild every CSV
    python src/pdfs_to_csv.py --no-download   # only convert PDFs already on disk

Output (anything already on disk is skipped, so it's safe to stop and rerun):
    data/raw_pdfs/<FY>/*.pdf     downloaded reports
    data/raw_csvs/<FY>/*.csv     one CSV per report
    data/skipped_pdfs.txt        reports with no tables (image-only PDFs) or no elections
"""

import argparse
import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup

INDEX_URL = "https://www.nlrb.gov/reports/agency-performance/election-reports"
FIRST_FY = 2012  # FY2012 = Oct 2011 to Sep 2012

DATA = Path("data")
PDF_DIR = DATA / "raw_pdfs"
CSV_DIR = DATA / "raw_csvs"
SKIPPED_TXT = DATA / "skipped_pdfs.txt"

WAIT = 1  # seconds between requests, so we don't hammer the NLRB site
HEADERS = {"User-Agent": "DATA1030 class project"}


#1. download

def fetch(url, tries=3):
    """GET a url, retrying a couple of times if the connection drops."""
    for attempt in range(1, tries + 1):
        time.sleep(WAIT)
        try:
            r = requests.get(url, headers=HEADERS, timeout=120)
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            print(f"    attempt {attempt} failed: {e}")
            if attempt == tries:
                raise
            time.sleep(5 * attempt)


def page_links(url):
    """All links on a page as absolute urls, without repeats, in page order."""
    soup = BeautifulSoup(fetch(url).text, "html.parser")
    return list(dict.fromkeys(urljoin(url, a["href"]) for a in soup.find_all("a", href=True)))


def is_pdf(url):
    return url.lower().endswith(".pdf")


def download_all():
    index_links = page_links(INDEX_URL)

    # PDFs linked straight from the index are the yearly summaries, not monthly reports
    summary_pdfs = {u for u in index_links if is_pdf(u)}

    # the index links one page per fiscal year, like .../election-reports-fy-2026
    year_pages = {}
    for u in index_links:
        m = re.search(r"election-reports-fy-(\d{4})", u)
        if m and int(m.group(1)) >= FIRST_FY:
            year_pages.setdefault(u, f"FY{m.group(1)}")
    print(f"{len(year_pages)} fiscal years to check")

    failed = []
    for page_url, fy in year_pages.items():
        for pdf_url in filter(is_pdf, page_links(page_url)):
            name = pdf_url.rsplit("/", 1)[-1]
            path = PDF_DIR / fy / name
            if pdf_url in summary_pdfs or "total" in name.lower() or path.exists():
                continue
            try:
                content = fetch(pdf_url).content
            except requests.exceptions.RequestException:
                failed.append(pdf_url)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            print(f"  got {fy}/{name}")

    if failed:
        print(f"\n{len(failed)} downloads failed, run again to retry:")
        print("\n".join("  " + u for u in failed))


#2. read the tables

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


#3. write the CSVs

def write_csv(rows, path):
    # keep the PDF's own columns, in the order they first appear
    columns = ["source_pdf", "extraction_method", "page"]
    columns += [c for c in dict.fromkeys(c for row in rows for c in row) if c not in columns]

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


def convert_all(redo=False):
    pdfs = sorted(PDF_DIR.glob("FY*/*.pdf"))
    print(f"\n{len(pdfs)} PDFs on disk")

    # PDFs skipped last time are slow to open, so don't retry them unless --redo
    skipped = []
    if SKIPPED_TXT.exists() and not redo:
        skipped = SKIPPED_TXT.read_text(encoding="utf-8").splitlines()
    already_skipped = {line.split(":")[0] for line in skipped}

    for pdf_path in pdfs:
        fy = pdf_path.parent.name
        if int(fy[2:]) < FIRST_FY:
            continue
        source = f"{fy}/{pdf_path.name}"
        csv_path = CSV_DIR / fy / f"{pdf_path.stem}.csv"
        if not redo and (csv_path.exists() or source in already_skipped):
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
        SKIPPED_TXT.parent.mkdir(parents=True, exist_ok=True)
        SKIPPED_TXT.write_text("\n".join(skipped) + "\n", encoding="utf-8")
        print(f"\n{len(skipped)} PDFs skipped, see {SKIPPED_TXT}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--redo", action="store_true", help="rebuild CSVs that already exist")
    parser.add_argument("--no-download", action="store_true", help="skip the download step")
    args = parser.parse_args()

    if not args.no_download:
        download_all()
    convert_all(redo=args.redo)
