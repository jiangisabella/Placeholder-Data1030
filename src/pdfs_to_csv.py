"""
Downloads the NLRB monthly election reports (FY2012 to now) and turns each
PDF into a CSV. Nothing gets cleaned here, values are copied as-is from the
PDF. Cleaning is a separate step (see the TODO list at the bottom).

    pip install -r requirements.txt
    python src/pdfs_to_csv.py

PDFs are saved to data/raw_pdfs/<FY>/ and CSVs to data/raw_csvs/<FY>/.
Anything already on disk is skipped, so it's fine to stop it and rerun.

A few reports (most of 2023, April 2019) have no readable text in them, just
pictures of a table, so they're skipped. They get listed in
data/skipped_pdfs.txt.
"""

import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup

INDEX_URL = "https://www.nlrb.gov/reports/agency-performance/election-reports"
FIRST_FY = 2012   # FY2012 = Oct 2011 to Sep 2012

PDF_DIR = Path("data/raw_pdfs")
CSV_DIR = Path("data/raw_csvs")
UNPARSED_DIR = Path("data/unparsed_lines")
SKIPPED_TXT = Path("data/skipped_pdfs.txt")

REDO_CSVS = False   # True = rebuild CSVs that already exist

WAIT = 1   # seconds between requests
REQUEST_HEADERS = {"User-Agent": "DATA1030 class project"}


"""Using beatiful soup to download PDFS"""

def get_soup(url):
    time.sleep(WAIT)
    r = requests.get(url, headers=REQUEST_HEADERS, timeout=60)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def pdf_links(soup, page_url):
    links = []
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"])
        if url.lower().endswith(".pdf") and url not in links:
            links.append(url)
    return links


def year_pages():
    # the index links one page per fiscal year, like .../election-reports-fy-2026
    soup = get_soup(INDEX_URL)
    pages = []
    for a in soup.find_all("a", href=True):
        m = re.search(r"election-reports-fy-(\d{4})", a["href"])
        if m and int(m.group(1)) >= FIRST_FY:
            page = ("FY" + m.group(1), urljoin(INDEX_URL, a["href"]))
            if page not in pages:
                pages.append(page)
    return pages


def download(url, path, tries=3):
    # Retry if connection dropped
    for i in range(1, tries + 1):
        try:
            time.sleep(WAIT)
            r = requests.get(url, headers=REQUEST_HEADERS, timeout=120)
            r.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            return True
        except requests.exceptions.RequestException as e:
            print(f"    try {i} failed: {e}")
            time.sleep(5 * i)
    return False


def download_all():
    menu_pdfs = set(pdf_links(get_soup(INDEX_URL), INDEX_URL))

    pages = year_pages()
    print(f"{len(pages)} fiscal years to check")
    failed = []

    for fy, page_url in pages:
        for pdf_url in pdf_links(get_soup(page_url), page_url):
            name = pdf_url.split("/")[-1]
            # skip the yearly "total elections" summaries
            if pdf_url in menu_pdfs or "total" in name.lower():
                continue
            path = PDF_DIR / fy / name
            if path.exists():
                continue
            if download(pdf_url, path):
                print(f"  got {fy}/{name}")
            else:
                failed.append(pdf_url)

    if failed:
        print(f"\n{len(failed)} downloads failed, run it again to retry:")
        for url in failed:
            print("  " + url)


"""For readable tables, using pdfplumber"""

CASE_NO = r"\d{2}-[A-Z]{2}-\d{5,6}"   # e.g. 01-RD-386098


def clean_cell(cell):
    # wrapped text inside a cell comes back with \n in it
    return " ".join(cell.split()) if cell else ""


def read_tables(pdf, source):
    rows = []
    # a table that carries on from the last page doesn't always repeat its
    # header, and sometimes the header itself gets cut in half by the page
    # break (top half at the bottom of one page, the rest on the next)
    last_header = None   # header of the previous table
    cut_header = None    # previous table was just a header with nothing under it
    last_row_of = {}     # number of columns -> last row read from a table that wide
    for page_no, page in enumerate(pdf.pages, start=1):
        # a row can get cut by a page break. the rest of it is at the top of
        # the next page, with no case number, so keep track of the last row.
        # tables don't always come out in page order, so go by table width
        prev_page_rows = dict(last_row_of)
        prev_page_last = rows[-1] if rows else None

        for table in page.extract_tables():
            header = None
            found = 0
            under_header = 0   # non-empty rows after the header
            for cells in table:
                cells = [clean_cell(c) for c in cells]

                # header row. the summary table at the top has no
                # "Case Number" column so it never gets a header and is skipped
                if "Case Number" in cells:
                    header = cells
                    continue
                if header is None and not re.search(CASE_NO, " ".join(cells)):
                    # rest of a header that got cut by the page break
                    if cut_header and len(cells) == len(cut_header):
                        cut_header = [f"{h} {c}".strip() for h, c in zip(cut_header, cells)]
                    continue
                if header is None:
                    header = cut_header or last_header
                if header is None or len(cells) != len(header):
                    continue
                if any(cells):
                    under_header += 1

                row = dict(zip(header, cells))
                if re.fullmatch(CASE_NO, row["Case Number"]):
                    row["source_pdf"] = source
                    row["extraction_method"] = "table"
                    row["page"] = page_no
                    rows.append(row)
                    last_row_of[len(cells)] = row
                    found += 1
                    continue
                last_row = prev_page_rows.get(len(cells)) or prev_page_last
                if last_row and found == 0 and not re.search(CASE_NO, " ".join(cells)):
                    # leftover bit of the last row from the previous page.
                    # skip section titles like "NLRB Elections with 3 Labor
                    # Organization" (which come with some html junk)
                    for col, text in row.items():
                        if text and col != "Case Number" and not text.startswith("NLRB Elections"):
                            last_row[col] = (last_row.get(col, "") + " " + text).strip()

            if header:
                last_header = header
            cut_header = header if header and under_header == 0 else None

    return rows


# ---------- backup: reading the text line by line ----------
# Only used when pdfplumber can't find a table in a PDF. Every election row
# has a case number, so we find those lines and split them up with regexes.
#
# What comes after the case number:
#   ~2011-2014: Name RC FileDate ClosedReason ClosedDate City ST ElectionDate
#               Eligible Against For Union Method WON/LOSS
#   later:      [CaseID] Name RC City ST ElectionDate Eligible Against For
#               Union Method WON/LOSS ClosedDate ClosedReason

ROW_CASE_NO = r"\d{2}-R[CDM]-\d{5,6}"   # RC/RD/RM only
DATE = r"\d{1,2}/\d{1,2}/\d{4}"

STATES = set("""AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD
    MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT
    VA WA WV WI WY PR GU VI AS MP""".split())

TEXT_COLUMNS = [
    "source_pdf", "extraction_method", "parse_note", "section",
    "case_number", "case_id", "case_name", "case_type", "city", "state",
    "file_date", "closed_reason", "closed_date", "election_date",
    "eligible", "votes_for", "votes_against", "vote_numbers",
    "union_name", "election_method", "result", "after_result", "raw_line",
]

_id = r"(?:(?P<case_id>1-[A-Z0-9]{4,8})\s+)?"   # some years have an ID like 1-Y1LQL4
_name = r"(?P<case_name>.*?)\s+(?P<case_type>RC|RD|RM)\s+"
_votes = r"(?P<eligible>\d+)\s+(?P<votes_against>\d+)\s+(?P<votes_for>\d+)\s+"
_rest = (r"(?P<union_name>.*?)\s*(?P<election_method>Stipulated|Consent|Directed)?\s*"
         r"(?P<result>WON|LOSS)\b(?P<after_result>.*)")

# the usual case, e.g. "Mayflower Medicinals, Inc. RD Fall River MA 08/19/2026 9 5 4 ..."
ROW = re.compile(
    _id + _name + r"(?P<middle>.*?)\s*\b(?P<state>[A-Z]{2})\s+"
    rf"(?:(?P<election_date>{DATE})\s+)?" + _votes + _rest)

# tall rows where the state got printed lower down and ended up on a later line
ROW_NO_STATE = re.compile(
    _id + _name + rf"(?P<city>.*?)\s+(?P<election_date>{DATE})\s+" + _votes + _rest)

# a handful of rows have empty vote cells in the PDF
ROW_FEW_VOTES = re.compile(
    _id + _name + r"(?P<city>.*?)\s*\b(?P<state>[A-Z]{2})\s+"
    rf"(?P<election_date>{DATE})\s+" + r"(?P<vote_numbers>(?:\d+\s+){0,2})" + _rest)

# ~2011-2014 reports have the file date and close info before the city
DATES_THEN_CITY = re.compile(
    rf"(?P<file_date>{DATE})\s+(?P<closed_reason>.*?)\s+(?P<closed_date>{DATE})\s+(?P<city>.*)")


def split_row(text, try_harder=False):
    case = re.search(ROW_CASE_NO, text)
    rest = text[case.end():].strip()
    row = None

    m = ROW.search(rest)
    if m:
        row = m.groupdict()
        middle = row.pop("middle")
        dates = DATES_THEN_CITY.match(middle)
        if dates:
            row.update(dates.groupdict())
        else:
            row["city"] = middle

    elif try_harder:
        m = ROW_NO_STATE.search(rest)
        if m:
            row = m.groupdict()
            # the state is somewhere in the glued-on lines after the result
            found = [w for w in row["after_result"].split() if w in STATES]
            row["state"] = found[0] if found else ""
            row["parse_note"] = "state found in wrapped text, check it"
        else:
            m = ROW_FEW_VOTES.search(rest)
            if m:
                row = m.groupdict()
                row["vote_numbers"] = row["vote_numbers"].strip()
                row["parse_note"] = "fewer than 3 vote numbers in PDF"

    if row:
        row["case_number"] = case.group()
    return row


def next_lines(lines, i, n=4):
    # the lines after line i that are still part of the same row
    out = []
    for line in lines[i + 1:i + 1 + n]:
        line = line.strip()
        if re.search(ROW_CASE_NO, line) or line.startswith(("Region", "NLRB", "Time run")):
            break
        out.append(line)
    return out


def read_text(pdf, source):
    lines = []
    for page in pdf.pages:
        lines += (page.extract_text() or "").splitlines()

    rows, unparsed = [], []
    section = ""
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith("NLRB Elections"):   # e.g. "NLRB Elections with 2 Labor Organizations"
            section = line
        if not re.search(ROW_CASE_NO, line):
            continue

        row = split_row(line)
        if row is None:
            # long names wrap onto the next lines, so glue those on and retry
            line = " ".join([line] + next_lines(lines, i))
            row = split_row(line, try_harder=True)
        if row is None:
            unparsed.append(line)
            continue

        row.update(source_pdf=source, extraction_method="text",
                   section=section, raw_line=line)
        rows.append(row)
    return rows, unparsed


# ---------- writing the CSVs ----------

def write_csv(rows, path, columns=None):
    if columns is None:
        # table files: use whatever columns the PDF had, in the order they showed up
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


def convert_all():
    pdfs = sorted(PDF_DIR.glob("FY*/*.pdf"))
    print(f"\n{len(pdfs)} PDFs on disk")

    # PDFs skipped last time are slow to open, so don't check them again
    skipped = []
    if SKIPPED_TXT.exists() and not REDO_CSVS:
        skipped = SKIPPED_TXT.read_text(encoding="utf-8").splitlines()
    already_skipped = {line.split(":")[0] for line in skipped}

    for pdf_path in pdfs:
        fy = pdf_path.parent.name
        if int(fy[2:]) < FIRST_FY:
            continue

        csv_path = CSV_DIR / fy / (pdf_path.stem + ".csv")
        unparsed_path = UNPARSED_DIR / f"{fy}_{pdf_path.stem}.txt"
        source = f"{fy}/{pdf_path.name}"
        if (csv_path.exists() or source in already_skipped) and not REDO_CSVS:
            continue

        unparsed = []
        columns = None   # None = keep the table's own columns
        how = "table"
        with pdfplumber.open(pdf_path) as pdf:
            rows = read_tables(pdf, source)
            if not rows:
                how = "text"
                rows, unparsed = read_text(pdf, source)
                columns = TEXT_COLUMNS
            first_page = " ".join((pdf.pages[0].extract_text() or "").split())

        if not rows:
            # nothing to save. either the report really has no elections in
            # it (Oct 2025), or the PDF is just pictures of a table with no
            # readable text (most of 2023, Apr 2019). we leave those out
            if "produced no results" in first_page:
                why = "report has no elections"
            else:
                why = "no readable text in the PDF"
            skipped.append(f"{source}: {why}")
            print(f"  {source}: skipped ({why})")
            continue

        write_csv(rows, csv_path, columns)

        # lines we couldn't split go in their own file so they can be checked
        if unparsed:
            UNPARSED_DIR.mkdir(parents=True, exist_ok=True)
            unparsed_path.write_text("\n".join(unparsed), encoding="utf-8")
        elif unparsed_path.exists():
            unparsed_path.unlink()

        print(f"  {source}: {len(rows)} rows ({how}), {len(unparsed)} unparsed")

    if skipped:
        SKIPPED_TXT.parent.mkdir(parents=True, exist_ok=True)
        SKIPPED_TXT.write_text("\n".join(skipped) + "\n", encoding="utf-8")
        print(f"\n{len(skipped)} PDFs skipped, see {SKIPPED_TXT}")


if __name__ == "__main__":
    download_all()
    convert_all()


r'''
----------------------------------------------------------------------
TODO: cleaning (separate notebook, e.g. src/preprocess.ipynb)
----------------------------------------------------------------------
1. Load everything as strings so "01" stays "01":
       files = sorted(Path("data/raw_csvs").glob("FY*/*.csv"))
       df = pd.concat([pd.read_csv(f, dtype=str) for f in files], ignore_index=True)

2. Column names change between years, and table/text files name things
   differently. Print the columns for each file first, then rename to one
   set: case_number, case_name, city, state, file_date, election_date,
   eligible, votes_for, votes_against, union_name, method, closed_date,
   closed_reason. Heads up, the 2012 reports spell it "Stiplulated".

3. case type = case_number[3:5]. Keep RC/RD/RM. Drop UD, those are votes on
   union-security clauses, not union elections. Region = case_number[:2]
   (the report's own Region column lists several office codes, ignore it).

4. Numbers: strip commas, then pd.to_numeric. Dates: pd.to_datetime with
   format="mixed" (some years write 4/1/2026, others 04/01/2026). A few
   rows have no election date.

5. Duplicates. Some months are linked on two year pages (the FY2012 page
   also links Oct 2012), so drop repeats on case_number + the 3 vote
   columns. One vote can also be filed as an RC and an RM case with the
   same date and votes (Georgia Power, Aug 2026). Keep one.

6. Target: union_won = 1 if closed_reason contains "Representative"
   (Certific. of Representative), 0 if it contains "Results"
   (Certification of Results). Don't trust the WON/LOSS columns alone,
   two rows in April 2026 have both filled in. For RD cases 1 means the
   union survived. Compare with votes_for > votes_against (tie = loss) and
   look at whatever disagrees. Vote share = for / (for + against), drop 0/0.

7. Multi-union elections (Labor Org 2/3 columns filled in, or a
   "2 Labor"/"3 Labor" section in text files). Probably just drop these.

8. Features:
   - state -> census region. PR/GU/VI as their own group or dropped
   - log(eligible). check for eligible == 0 or more votes than voters
   - union name -> parent union by keyword (teamster, seiu, workers
     united, ufcw, ibew, ...), everything else "Other"
   - normalized employer name (lowercase, no punctuation, drop inc/llc/
     corp and anything after d/b/a). needed for the group split
   - year and month of the election
   - days from filing to election: only the ~2012-2014 reports have a
     file date. for later years it'd have to come from the case pages
   - flag cases that closed years after the vote

9. Text files only: check rows with a parse_note, and pull closed date +
   reason out of after_result with r"(\d{1,2}/\d{1,2}/\d{4})\s+(.*)".
   Names can be cut off. case_id is just an ID, not a feature.

10. Missing months: data/skipped_pdfs.txt lists the reports we couldn't
    read (Feb-Sep 2023, Apr 2019) plus Oct 2025, which has no elections
    at all. Say so in the report, and expect a gap in any plot over time.
    The reports go by the month a case CLOSED, so elections held in those
    months can still show up in later reports.

11. Before modeling, drop everything that's only known after the vote:
    vote counts, closed date/reason, the WON/LOSS columns, Union To
    Certify. Also the bookkeeping ones (source_pdf, page, raw_line, ...).

12. Split by election date (not closed date), test on the latest years,
    GroupKFold by employer inside the training years. Anything like a
    union's past win rate can only use earlier elections.

13. Save to data/elections_clean.csv and write up the data documentation.
'''