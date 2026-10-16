"""
Download the NLRB monthly election report PDFs (fiscal years 2009-2026) and
convert each one into a CSV file. No data cleaning is done here; every value
is kept as the raw text that appears in the PDF. See NOTES FOR LATER at the
bottom of this file.

Step 1  Download: data/raw_pdfs/<year>/<file>.pdf
Step 2  Convert:  data/raw_csvs/<year>/<file>.csv   (one CSV per PDF)

How a PDF is converted:
  * "table" method (preferred). Most reports are drawn as a real table with
    cell borders. pdfplumber reads that grid directly, so every row gets all
    of the report's own columns (e.g. "Closed Date", "Closed Reason") with
    the report's own column names, and long names that wrap onto several
    lines are kept whole.
  * "text" method (fallback). If a PDF has no readable table, its text is
    read line by line and split into columns with patterns. These rows use
    our own column names (case_number, union_name, ...).
  The extraction_method column says which one was used for each row.

Both steps skip files that already exist, so re-running only does new work.
Set REDO_EXISTING_CSVS = True to rebuild every CSV (e.g. after changing this
script).

Load all the CSVs at once later with:

    import pandas as pd
    from pathlib import Path
    files = sorted(Path("data/raw_csvs").glob("*/*.csv"))
    df = pd.concat([pd.read_csv(f, dtype=str) for f in files], ignore_index=True)

Column names differ a little between years (the reports changed over time),
so expect to rename/merge some columns later. See NOTES FOR LATER.

Usage:
    pip install requests beautifulsoup4 pdfplumber
    python src/pdfs_to_csv.py
"""

import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
INDEX_URL = "https://www.nlrb.gov/reports/agency-performance/election-reports"
FIRST_FISCAL_YEAR = 2009         # the pages before this are calendar years
PDF_DIR = Path("data/raw_pdfs")
CSV_DIR = Path("data/raw_csvs")
UNPARSED_DIR = Path("data/unparsed_lines")   # one .txt per PDF with problems
REDO_EXISTING_CSVS = True

SECONDS_BETWEEN_REQUESTS = 1.0   # be polite to the NLRB server
HEADERS = {"User-Agent": "DATA1030 student project (union election research)"}

# Pattern for a case number in a table cell, e.g. 01-RD-386098. This also
# accepts other case types such as UD, so those rows are kept too.
ANY_CASE_NUMBER = r"\d{2}-[A-Z]{2}-\d{5,6}"

# Columns used by the "text" fallback method. (The "table" method uses the
# column names printed in the PDF instead.) Not every layout fills every
# column; the ones a layout doesn't have are left blank.
TEXT_COLUMNS = [
    "source_pdf",          # e.g. FY2026/cases-closed-august-2026.pdf
    "extraction_method",   # "text"
    "layout",              # which PDF layout the row came from (see below)
    "parse_note",          # blank, or a warning about this row (see below)
    "section",             # table heading the row was under (newer layouts)
    "case_number",         # e.g. 01-RD-386098
    "case_id",             # internal ID like 1-Y1LQL4 (some years only)
    "case_name",           # employer (in the oldest layout also the city)
    "case_type",           # RC / RD / RM  (newer layouts only)
    "city",
    "state",
    "file_date",           # petition filed  ("with file date" layout only)
    "closed_reason",       # ("with file date" layout only)
    "closed_date",         # ("with file date" layout only)
    "election_date",       # (newer layouts only)
    "eligible",            # number of eligible voters
    "votes_for",           # votes for the union
    "votes_against",       # votes against the union
    "vote_numbers",        # raw numbers when there weren't exactly three
    "union_name",
    "election_method",     # Stipulated / Consent / Directed (newer layouts)
    "election_type_code",  # one-letter code (oldest layout only)
    "industry_code",       # (oldest layout only)
    "unit_type",           # (oldest layout only)
    "result",              # WON / LOSS (newer) or WON / LOST (oldest)
    "after_result",        # leftover text after the result (closed date etc.)
    "raw_line",            # the original text, for checking by hand
]

# All two-letter state and territory codes, used to spot a state that has
# been pushed away from the city (see STATE_ELSEWHERE_ROW below).
STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY", "PR", "GU", "VI", "AS", "MP",
}

# ----------------------------------------------------------------------------
# Patterns for finding election rows
# ----------------------------------------------------------------------------
# The PDFs use several layouts over the years (years are approximate):
#   "grouped"         FY2009 - early 2011. Rows are grouped under a union name
#                     with a WON/LOST heading. Votes are For, then Against.
#                     Employer City ST Case# Industry Unit Type Eligible For Against
#   "with file date"  2011 - ~2014.
#                     Case# Name Type FileDate ClosedReason ClosedDate City ST
#                     ElectionDate Eligible Against For Union Method WON/LOSS
#   "with case ID"    ~2015 - 2018. Like "standard" but with an internal ID
#                     (e.g. 1-Y1LQL4) after the case number.
#   "standard"        ~2018 - 2026.
#                     Case# Name Type City ST ElectionDate Eligible Against For
#                     Union Method WON/LOSS ClosedDate ClosedReason
# Every election row contains a case number, so we use it to find rows.
CASE_NUMBER = r"\d{2}-R[CDM]-\d{5,6}"
DATE = r"\d{1,2}/\d{1,2}/\d{4}"

# Pieces shared by the newer patterns.
CASE_ID = r"(?:(?P<case_id>1-[A-Z0-9]{4,8})\s+)?"     # optional internal ID
NAME_AND_TYPE = r"(?P<case_name>.*?)\s+(?P<case_type>RC|RD|RM)\s+"
UNION_TO_END = (
    r"(?P<union_name>.*?)\s*"
    r"(?P<election_method>Stipulated|Consent|Directed)?\s*"
    r"(?P<result>WON|LOSS|LOST)\b"
    r"(?P<after_result>.*)"
)

# Oldest "grouped" layout. Rows start with:
#   Employer City ST Case# IndustryCode Unit ElectionType
# The state is optional because a few rows have a blank city and state, and
# the election type is sometimes blank too.
OLD_ROW_START = (
    rf"(?P<case_name>.*?)\s*\b(?:(?P<state>[A-Z]{{2}})\s+)?"
    rf"(?P<case_number>{CASE_NUMBER})\s+"
    r"(?P<industry_code>\d{3,6})\s+"
    r"(?P<unit_type>[A-Za-z]+)\s+"
    r"(?:(?P<election_type_code>[A-Za-z])\s+)?"
)

# Single-union elections end with Eligible, For, Against. Example:
#   CUSA ASL, L.L.C. Phoenix AZ 28-RD-00929 485 Craft S 92 10 66
OLD_ROW = re.compile(
    OLD_ROW_START +
    r"(?P<eligible>\d+)\s+(?P<votes_for>\d+)\s+(?P<votes_against>\d+)"
)

# Multi-union elections end with just Eligible and Against (votes against
# all the unions). Each union's own votes are on its own line ABOVE the row:
#   International Brotherhood of Teamsters (IBT) WON I 26
#   American Nurses' Association (ANA) LOST P 24
#   South Peninsula Hospital, Inc. Anchorage AK 19-RC-15134 622 Prof R 67 0
OLD_MULTI_UNION_ROW = re.compile(
    OLD_ROW_START + r"(?P<eligible>\d+)\s+(?P<votes_against>\d+)\s*$"
)

# WON or LOST in the "grouped" layout's union lines. (One PDF prints "LOS T",
# so a space is allowed.)
WON_OR_LOST = r"\b(WON|LOS ?T)\b"

# The newer patterns are applied to the text AFTER the case number, and are
# tried in this order until one matches:

# 1) The normal case: "City ST Date Eligible Against For". Example:
#    Mayflower Medicinals, Inc. RD Fall River MA 08/19/2026 9 5 4
#    United Food and Commercial Workers Stipulated LOSS 08/27/2026 ...
#    In the "with file date" layout, "middle" also holds the filing date,
#    closed reason and closed date before the city.
NEW_ROW = re.compile(
    CASE_ID + NAME_AND_TYPE +
    r"(?P<middle>.*?)\s*\b(?P<state>[A-Z]{2})\s+"
    rf"(?:(?P<election_date>{DATE})\s+)?"
    r"(?P<eligible>\d+)\s+(?P<votes_against>\d+)\s+(?P<votes_for>\d+)\s+"
    + UNION_TO_END
)

# 2) The state is NOT next to the city. This happens in rows where a long
#    name wraps onto several lines: the state is printed in the middle of the
#    tall row, so it ends up on a later line of text. Example (glued lines):
#    GREEN HILL, INC. RC West Orange 9/27/2017 20 6 9 DISTRICT 1199J,
#    NATIONAL Stipulated WON 10/5/2017 Certific. of UNION OF ... NJ
#    We read the row without the state, then look for a state code in the
#    leftover text.
STATE_ELSEWHERE_ROW = re.compile(
    CASE_ID + NAME_AND_TYPE +
    rf"(?P<city>.*?)\s+(?P<election_date>{DATE})\s+"
    r"(?P<eligible>\d+)\s+(?P<votes_against>\d+)\s+(?P<votes_for>\d+)\s+"
    + UNION_TO_END
)

# 3) Fewer than three vote numbers. A few rows have blank vote cells in the
#    PDF, so we can't tell which number is which. Example:
#    Schwarz Partners RC Lakeland FL 08/06/2019 UNITED STEEL, PAPER ...
#    The numbers found (0, 1 or 2 of them) go in vote_numbers.
MISSING_VOTES_ROW = re.compile(
    CASE_ID + NAME_AND_TYPE +
    r"(?P<city>.*?)\s*\b(?P<state>[A-Z]{2})\s+"
    rf"(?P<election_date>{DATE})\s+"
    r"(?P<vote_numbers>(?:\d+\s+){0,2})"
    + UNION_TO_END
)

# In the "with file date" layout the middle part is
# "FileDate ClosedReason ClosedDate City". Otherwise it is just the city.
MIDDLE_WITH_DATES = re.compile(
    rf"(?P<file_date>{DATE})\s+(?P<closed_reason>.*?)\s+"
    rf"(?P<closed_date>{DATE})\s+(?P<city>.*)"
)


# ----------------------------------------------------------------------------
# Step 1: download the PDFs
# ----------------------------------------------------------------------------
def get_page(url):
    """Download a web page and return it as a BeautifulSoup object."""
    time.sleep(SECONDS_BETWEEN_REQUESTS)
    response = requests.get(url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def find_pdf_links(soup, page_url):
    """Return {pdf_url: link_text} for every PDF link on a page."""
    links = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"])
        if url.lower().endswith(".pdf"):
            links[url] = a.get_text(strip=True)
    return links


def get_year_pages():
    """Return a list of (year_label, url) for each fiscal year page."""
    soup = get_page(INDEX_URL)
    year_pages = []
    for a in soup.find_all("a", href=True):
        # Fiscal year pages look like .../election-reports-fy-2026.
        # (Calendar year pages, ...-cy-2005, are skipped.)
        match = re.search(r"election-reports-fy-(\d{4})", a["href"])
        if match and int(match.group(1)) >= FIRST_FISCAL_YEAR:
            label = f"FY{match.group(1)}"
            year_pages.append((label, urljoin(INDEX_URL, a["href"])))
    return year_pages


def download_pdf(pdf_url, out_path, attempts=3):
    """Download one PDF, retrying if the connection drops partway through.

    The file is only saved once the full download succeeds, so a broken
    download never leaves a half-written PDF behind. Returns True on success.
    """
    for attempt in range(1, attempts + 1):
        try:
            time.sleep(SECONDS_BETWEEN_REQUESTS)
            response = requests.get(pdf_url, headers=HEADERS, timeout=120)
            response.raise_for_status()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(response.content)
            return True
        except requests.exceptions.RequestException as error:
            print(f"    attempt {attempt}/{attempts} failed: {error}")
            time.sleep(5 * attempt)   # wait a bit longer after each failure
    return False


def download_all_pdfs():
    """Download every monthly PDF. Files already on disk are skipped."""
    # The site menu (on every page) links to a few unrelated PDFs, such as a
    # fact sheet. Collect the menu's PDFs from the index page so we can
    # ignore them on the year pages.
    menu_pdfs = set(find_pdf_links(get_page(INDEX_URL), INDEX_URL))

    year_pages = get_year_pages()
    print(f"Found {len(year_pages)} fiscal year pages")
    failed = []

    for year_label, year_url in year_pages:
        pdf_links = find_pdf_links(get_page(year_url), year_url)

        for pdf_url, link_text in pdf_links.items():
            filename = pdf_url.split("/")[-1]
            if pdf_url in menu_pdfs or "total" in filename.lower():
                continue   # skip menu links and yearly summary files

            out_path = PDF_DIR / year_label / filename
            if out_path.exists():
                continue

            if download_pdf(pdf_url, out_path):
                print(f"  downloaded {year_label}/{filename}  ({link_text})")
            else:
                failed.append(pdf_url)

    if failed:
        print(f"\n{len(failed)} PDFs failed to download. Run the script again "
              "to retry them (finished files are skipped):")
        for url in failed:
            print("  " + url)


# ----------------------------------------------------------------------------
# Step 2a: the "table" method
# ----------------------------------------------------------------------------
def cell_text(cell):
    """Text of one table cell. Wrapped lines inside a cell are joined with
    spaces, e.g. "Certific. of\\nRepresentative" -> "Certific. of Representative".
    """
    if cell is None:
        return ""
    return " ".join(cell.split())


def rows_from_tables(pdf, source_name):
    """Read every election row from the PDF's tables. Returns a list of dicts.

    Each table in the report starts with a header row (Region, Case Number,
    Case ID, ...). Those header cells become the column names for the rows
    below it. The summary table at the top of the report has no
    "Case Number" column, so it is skipped automatically.
    """
    rows = []
    for page_number, page in enumerate(pdf.pages, start=1):
        # A row cut in two by a page break continues at the top of the next
        # page, so remember the last row of the previous page.
        last_row_of_previous_page = rows[-1] if rows else None

        for table in page.extract_tables():
            headers = None
            rows_in_this_table = 0
            for cells in table:
                cells = [cell_text(c) for c in cells]

                # A header row: remember the column names.
                if "Case Number" in cells:
                    headers = cells
                    continue
                # Skip anything that isn't part of a readable table.
                if headers is None or len(cells) != len(headers):
                    continue

                row = dict(zip(headers, cells))
                if re.fullmatch(ANY_CASE_NUMBER, row["Case Number"]):
                    row["source_pdf"] = source_name
                    row["extraction_method"] = "table"
                    row["page"] = page_number
                    rows.append(row)
                    rows_in_this_table += 1

                # The rest of a row that was cut by a page break: no case
                # number, right under the table's header row. Add its text
                # to the last row of the previous page. (If the cells contain
                # a case number, it is a jumbled block of text, so skip it.)
                elif (last_row_of_previous_page is not None
                      and rows_in_this_table == 0
                      and not re.search(ANY_CASE_NUMBER, " ".join(cells))):
                    previous = last_row_of_previous_page
                    for column, text in row.items():
                        if text:
                            previous[column] = f"{previous.get(column, '')} {text}".strip()
    return rows


def write_csv(rows, csv_path, columns=None):
    """Write rows (a list of dicts) to a CSV file.

    If no column list is given, the columns are every key that appears in
    any row, in the order they first appear.
    """
    if columns is None:
        columns = ["source_pdf", "extraction_method", "page"]
        for row in rows:
            columns += [key for key in row if key not in columns]

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ----------------------------------------------------------------------------
# Step 2b: the "text" method (fallback for PDFs without a readable table)
# ----------------------------------------------------------------------------
def parse_old_row(line, union_lines):
    """Split a "grouped" layout row into columns. Returns dict or None.

    union_lines are the union line(s) printed above the row.
    """
    if match := OLD_ROW.search(line):
        # Single-union election: the union heading above it, e.g.
        # "Teamsters (IBT) WON", gives the union name and the result.
        row = match.groupdict()
        heading = union_lines[-1] if union_lines else ""
        result = re.search(WON_OR_LOST, heading)
        row["result"] = result.group().replace(" ", "") if result else ""
        row["union_name"] = " ".join(re.sub(WON_OR_LOST, " ", heading).split())
        row["layout"] = "grouped"

    elif match := OLD_MULTI_UNION_ROW.search(line):
        # Multi-union election: keep every union line as printed
        # ("Name WON/LOST Role Votes"), separated by " | ".
        row = match.groupdict()
        row["union_name"] = " | ".join(union_lines)
        row["layout"] = "grouped (multi-union)"

    else:
        return None
    return row


def parse_new_row(text, try_all_patterns=False):
    """Split a newer-layout row into columns. Returns dict or None.

    By default only the normal pattern (state next to the city) is tried.
    With try_all_patterns=True, the two fallback patterns are tried too;
    use that on a row that has been glued together with its wrapped lines.
    """
    case_match = re.search(CASE_NUMBER, text)
    rest = text[case_match.end():].strip()

    if match := NEW_ROW.search(rest):
        row = match.groupdict()
        middle = row.pop("middle")
        if middle_match := MIDDLE_WITH_DATES.match(middle):
            row.update(middle_match.groupdict())
        else:
            row["city"] = middle

    elif try_all_patterns and (match := STATE_ELSEWHERE_ROW.search(rest)):
        row = match.groupdict()
        # The wrapped lines were glued on after the result, so the state
        # should be somewhere in the leftover text.
        leftover_words = row["after_result"].split()
        states_found = [w for w in leftover_words if w in STATE_CODES]
        row["state"] = states_found[0] if states_found else ""
        row["parse_note"] = "state found in wrapped text, please check"

    elif try_all_patterns and (match := MISSING_VOTES_ROW.search(rest)):
        row = match.groupdict()
        row["vote_numbers"] = row["vote_numbers"].strip()
        row["parse_note"] = "fewer than 3 vote numbers in PDF"

    else:
        return None

    row["case_number"] = case_match.group()
    if row.get("file_date"):
        row["layout"] = "with file date"
    elif row.get("case_id"):
        row["layout"] = "with case ID"
    else:
        row["layout"] = "standard"
    return row


def wrapped_lines(lines, i, max_lines=4):
    """Return the lines after line i that belong to the same row.

    Stops at the next row (a line with a case number) or at a page/table
    header, so text from other rows or headers isn't glued on.
    """
    following = []
    for next_line in lines[i + 1:i + 1 + max_lines]:
        next_line = next_line.strip()
        is_header = next_line.startswith(("Region", "NLRB", "Time run"))
        if re.search(CASE_NUMBER, next_line) or is_header:
            break
        following.append(next_line)
    return following


def lines_to_rows(lines, source_name):
    """Turn the text lines of one PDF into (rows, unparsed_lines)."""
    rows, unparsed = [], []
    section = ""             # e.g. "RESULTS OF MULTI-UNION ELECTIONS"
    union_lines = []         # "grouped" layout: union line(s) above the rows
    previous_was_row = True  # was the last useful line an election row?

    for i, line in enumerate(lines):
        line = line.strip()

        # Lines without a case number are headings or page clutter.
        if not re.search(CASE_NUMBER, line):
            is_column_header = "CASE NO." in line or "WON/" in line
            if line.startswith(("NLRB Elections", "RESULTS OF")):
                section = line
                previous_was_row = True
            elif is_column_header:
                previous_was_row = True   # e.g. "... WON/ EMP IN VALID VOTES"
            elif re.search(WON_OR_LOST, line):
                # A "grouped" layout union line, e.g. "Teamsters (IBT) WON".
                # Single-union pages have one per group of rows; multi-union
                # pages have one per union, right above each election row.
                # A union line after a row starts a new list.
                if previous_was_row:
                    union_lines = []
                union_lines.append(line)
                previous_was_row = False
            elif union_lines and not previous_was_row:
                # The wrapped end of a long union name, e.g. "(UFCW)".
                union_lines[-1] += " " + line
            continue

        previous_was_row = True
        row = parse_old_row(line, union_lines)
        if row is None:
            row = parse_new_row(line)
            if row is None:
                # Long names wrap onto the following lines, and in tall rows
                # some cells (like the state) are printed on a later line.
                # Glue on the next few lines and try every pattern.
                line = " ".join([line] + wrapped_lines(lines, i))
                row = parse_new_row(line, try_all_patterns=True)

        if row is None:
            unparsed.append(line)
            continue

        row["section"] = section
        row["source_pdf"] = source_name
        row["extraction_method"] = "text"
        row["raw_line"] = line
        rows.append(row)

    return rows, unparsed


def read_pdf_lines(pdf):
    """Return all the text lines of a PDF, page by page."""
    lines = []
    for page in pdf.pages:
        lines += (page.extract_text() or "").splitlines()
    return lines


# ----------------------------------------------------------------------------
# Step 2: convert each PDF, trying the table method first
# ----------------------------------------------------------------------------
def pdf_to_rows(pdf_path):
    """Return (rows, columns, unparsed_lines) for one PDF."""
    source_name = f"{pdf_path.parent.name}/{pdf_path.name}"
    with pdfplumber.open(pdf_path) as pdf:
        rows = rows_from_tables(pdf, source_name)
        if rows:
            # Use the PDF's own column names (columns=None).
            return rows, None, []
        rows, unparsed = lines_to_rows(read_pdf_lines(pdf), source_name)
        return rows, TEXT_COLUMNS, unparsed


def convert_all_pdfs():
    """Write one CSV for every downloaded PDF that doesn't have one yet."""
    pdf_paths = sorted(PDF_DIR.glob("*/*.pdf"))
    print(f"\nFound {len(pdf_paths)} PDFs")
    total_unparsed = 0

    for pdf_path in pdf_paths:
        year = pdf_path.parent.name
        csv_path = CSV_DIR / year / (pdf_path.stem + ".csv")
        unparsed_path = UNPARSED_DIR / f"{year}_{pdf_path.stem}.txt"

        if csv_path.exists() and not REDO_EXISTING_CSVS:
            continue

        rows, columns, unparsed = pdf_to_rows(pdf_path)
        total_unparsed += len(unparsed)
        write_csv(rows, csv_path, columns)
        method = rows[0]["extraction_method"] if rows else "none"

        # Keep a list of lines we couldn't split, one file per PDF.
        if unparsed:
            UNPARSED_DIR.mkdir(parents=True, exist_ok=True)
            unparsed_path.write_text("\n".join(unparsed), encoding="utf-8")
        elif unparsed_path.exists():
            unparsed_path.unlink()   # an old list from a previous run

        print(f"  {csv_path}: {len(rows)} rows ({method} method), "
              f"{len(unparsed)} unparsed lines")

    print(f"\nDone. {total_unparsed} unparsed lines in the PDFs converted this "
          f"run (see {UNPARSED_DIR}/).")


if __name__ == "__main__":
    download_all_pdfs()   # PDFs already downloaded are skipped
    convert_all_pdfs()    # PDFs that already have a CSV are skipped


# ----------------------------------------------------------------------------
# NOTES FOR LATER (data cleaning, to do in pandas)
# ----------------------------------------------------------------------------
# General
# 1. Read every CSV with dtype=str, then convert types yourself (vote counts
#    -> numbers, dates -> dates). Keep case numbers as text. Region = first
#    two characters of the case number ("01", not 1).
#
# 2. Two kinds of CSVs: "table" method rows use the PDF's own column names
#    ("Case Number", "Closed Date", ...), "text" method rows use ours
#    ("case_number", "closed_date", ...). Column names in the PDFs also
#    changed a little over the years. Check df.columns after loading and
#    rename to one set of names, e.g.
#        "Case Number" / case_number           -> case_number
#        "Dispute Unit City" / "City" / city   -> city
#        "Num Eligible Voters" / eligible      -> eligible
#        "Valid Votes Against" / votes_against -> votes_against
#        "Votes for Labor Org 1" / votes_for   -> votes_for
#
# 3. Case type: take it from the case number, which is always there:
#    "01-RD-386098"[3:5] -> "RD".
#
# 4. UD cases (case type "UD") are a different kind of vote (on removing a
#    union-security clause, using "For/Against Proposition" columns). They
#    are not union elections, so drop them.
#
# Result columns
# 5. Table method: the result is in two columns, "Cert of Rep (Win)" = WON
#    and "Cert of Results (Loss)" = LOSS. A few rows have both filled in
#    (e.g. 25-RC-382281 in April 2026: 15 for vs 6 against, but closed with
#    a Certification of Results). Use "Closed Reason" to decide, and check
#    those rows on their case pages.
#    Text method: "result" says WON / LOSS (or WON / LOST in the oldest
#    "grouped" layout). Pick one spelling.
#
# 6. Sanity check: the union normally wins if votes for > votes against (a
#    tie is a loss). Compare with the result; challenged ballots and later
#    events can change outcomes, so trust the reported result.
#
# 7. "Closed Reason" is written two ways: "Certific. of Representative"
#    (union won) and "Certification of Results" (union lost). Standardize.
#
# Text method only
# 8. Election method: the "grouped" layout uses a one-letter code in
#    election_type_code. Map it to the newer words:
#        OLD_ELECTION_TYPES = {
#            "S": "Stipulated",   # both sides agreed on the election terms
#            "C": "Consent",      # both sides agreed, regional director has final say
#            "R": "Directed",     # ordered by the Regional Director
#            "B": "Directed",     # ordered by the Board
#            "E": "Expedited",
#        }
#    Some old rows use lowercase letters (e.g. "r"), so uppercase first.
#
# 9. "grouped" case_name holds "Employer City" together, and a leading "*"
#    marks an incumbent union. Strip the "*"; splitting off the city is hard.
#
# 10. Long names that wrap in the PDF can be cut off or mixed with pieces of
#     other columns (the table method doesn't have this problem). raw_line
#     shows the text that was read. after_result holds the closed date and
#     closed reason for the "standard" layout.
#
# 11. parse_note = "state found in wrapped text, please check": the state was
#     picked out of leftover text and can be fooled by words like "IN" or
#     "OR". parse_note = "fewer than 3 vote numbers in PDF": vote columns are
#     blank and vote_numbers holds what was there; look these up or drop.
#
# Both methods
# 12. Multi-union elections: table method rows have "Votes for Labor Org 2"
#     / "Labor Org 2 Name" filled in (and "Union To Certify"). In the newer
#     text layouts, the second union's votes are mixed into union_name.
#     In the "grouped (multi-union)" layout, result and votes_for are blank
#     and union_name holds every union line as printed, separated by " | ":
#         "Teamsters (IBT) WON I 26 | American Nurses' Association (ANA) LOST P 24"
#     i.e. name, WON/LOST, role (P = petitioner, I = intervenor, U = union
#     involved) and that union's votes. A leading "*" marks the incumbent,
#     and one PDF prints "LOS T" for LOST. Decide whether to keep them.
#
# 13. Duplicates: some months were uploaded twice (corrected re-uploads).
#     Drop repeats on case number + vote counts.
#
# 14. Leakage: "Closed Date" and "Closed Reason" are known only after the
#     election, so don't use them as model features. (Closed Reason is
#     useful for the target.)
#
# 15. Check data/unparsed_lines/ and any CSV with 0 rows, and note which
#     PDFs used the "text" method (printed while converting).