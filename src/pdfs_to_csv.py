"""
Download the NLRB monthly election reports (FY2012 onward) and turn each PDF
into a raw CSV, values copied as-is. Anything already on disk is skipped, so
it's safe to stop and rerun. Reports with no tables (image-only PDFs) or no
elections are listed in data/skipped_pdfs.txt.
"""

"""
AI use: Claude (Anthropic) was used as a reference while writing this file
and src/docket_scraping.py. We planned the approach ourselves and asked for
feedback and small examples along the way. Here it helped with:
- collecting the FY pages and PDF links with BeautifulSoup and urljoin
  (find_links, download_all)
- how pdfplumber's extract_tables() returns rows, and flattening wrapped
  cell text (clean_cell)
- handling tables that continue across page breaks (add_leftover, read_tables)
- keeping each PDF's own columns plus source_pdf/page, and logging PDFs
  with no tables (write_csv, skipped_pdfs.txt)
Sections that used it are marked "Reference: (Anthropic, 2026)".

Anthropic. (2026). Claude [Large language model].
    https://claude.ai/share/bec36dfd-47c2-4ac1-887e-329860fa5cac
"""

import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup


PDF_DIR = Path("data/raw_pdfs")
CSV_DIR = Path("data/raw_csvs")
SKIPPED_FILE = Path("data/skipped_pdfs.txt")
DOWNLOAD = True    # False = only convert PDFs already on disk
REDO_CSVS = False  # True = rebuild CSVs that already exist
REQUEST_DELAY = 1

INDEX_URL = "https://www.nlrb.gov/reports/agency-performance/election-reports"
FIRST_FY = 2012  # FY2012 = Oct 2011 to Sep 2012

CASE_RE = re.compile(r"\d{2}-[A-Z]{2}-\d{5,6}")  # e.g. 01-RD-386098


def get_page(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return response


"""
Reference: (Anthropic, 2026). Collecting the year pages from the index's own
links (their URLs don't follow one pattern), urljoin for relative links,
and skipping the yearly total/summary files.
"""
def find_links(session, url):
    # every link on the page as a full url, in page order, no repeats
    soup = BeautifulSoup(get_page(session, url).text, "html.parser")

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
        index_links = find_links(session, INDEX_URL)

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

        for fy, page_url in year_pages:
            for pdf_url in find_links(session, page_url):
                name = pdf_url.split("/")[-1]
                path = PDF_DIR / fy / name

                # skip non-PDFs, the yearly totals, and anything already downloaded
                if not is_pdf(pdf_url) or pdf_url in summary_pdfs:
                    continue
                if "total" in name.lower() or path.exists():
                    continue

                try:
                    content = get_page(session, pdf_url).content
                except requests.RequestException as error:
                    print(f"  {fy}/{name}: failed ({error}), run again to retry")

                    if (
                        isinstance(error, requests.HTTPError)
                        and error.response is not None
                    ):
                        status = error.response.status_code

                        if status in (403, 429) or status >= 500:
                            print("Stopping due to a possible website error.")
                            return
                    continue

                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                print(f"  got {fy}/{name}")


"""
Reference: (Anthropic, 2026). How extract_tables() returns cells (None when
empty, newlines when text wraps), and the rules for page breaks: a row with
a case number starts a new record, a row without one is the rest of the
previous record, and a split header is merged column by column.
Used for clean_cell, add_leftover, read_tables.
"""
def clean_cell(cell):
    # text that wraps inside a cell comes back with newlines, flatten it
    return " ".join(cell.split()) if cell else ""


def add_leftover(row, leftover):
    # glue the tail of a row that a page break cut off back onto the row.
    # section titles like "NLRB Elections with 3 Labor Organizations" are ignored
    for col, text in leftover.items():
        if text and col != "Case Number" and not text.startswith("NLRB Elections"):
            row[col] = f"{row.get(col, '')} {text}".strip()


def read_tables(pdf, source):
    # Page breaks cause three problems, handled below:
    #  - a table continuing on a new page doesn't always repeat its header
    #    -> reuse the previous table's header
    #  - the header itself can be split across the break
    #    -> merge the two halves (cut_header)
    #  - a row can be split across the break; the leftover at the top of the
    #    next page has no case number -> glue it onto the last row read.
    #    Tables don't always come out in page order, so the last row is
    #    looked up by table width (number of columns).
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
                has_case_no = CASE_RE.search(" ".join(cells))

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
                if CASE_RE.fullmatch(row.get("Case Number", "")):  # .get: a merged header can rename it
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


"""
Reference: (Anthropic, 2026). One CSV per PDF with that PDF's own columns,
plus source_pdf and page so each row can be traced back to its file.
"""
def write_csv(rows, path):
    # keep the PDF's own columns, in the order they first appear
    columns = ["source_pdf", "extraction_method", "page"]
    for row in rows:
        for col in row:
            if col not in columns:
                columns.append(col)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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

        with pdfplumber.open(pdf_path) as pdf:
            first_page = " ".join((pdf.pages[0].extract_text() or "").split())
            rows = read_tables(pdf, source)

        if not rows:
            # either the report really has no elections (Oct 2025), or the PDF
            # is just pictures of a table with no text (most of 2023, Apr 2019)
            if "produced no results" in first_page:
                why = "report has no elections"
            else:
                why = "no readable text in the PDF"
            skipped.append(f"{source}: {why}")
            print(f"  {source}: skipped ({why})")
            continue

        write_csv(rows, csv_path)
        print(f"  {source}: {len(rows)} rows")

    if skipped:
        SKIPPED_FILE.parent.mkdir(parents=True, exist_ok=True)
        SKIPPED_FILE.write_text("\n".join(skipped) + "\n", encoding="utf-8")
        print(f"\n{len(skipped)} PDFs skipped, see {SKIPPED_FILE}")


if __name__ == "__main__":
    if DOWNLOAD:
        download_all()
    convert_all()
