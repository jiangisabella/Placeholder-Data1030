"""
Downloads the NLRB monthly election reports (FY2012 to now) and turns each
PDF into a CSV. Nothing gets cleaned here, values are copied as-is from the
PDF. Cleaning is a separate step (see the TODO list at the bottom).

    pip install -r requirements.txt
    python src/pdfs_to_csv.py

A few reports (most of 2023, April 2019) have no real text in them, so those
get OCR'd. 

PDFs are saved to data/raw_pdfs/<FY>/ and CSVs to data/raw_csvs/<FY>/.
Anything already on disk is skipped, so it's fine to stop it and rerun.
"""

import csv
import difflib
import itertools
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup
from PIL import ImageOps

INDEX_URL = "https://www.nlrb.gov/reports/agency-performance/election-reports"
FIRST_FY = 2012   # FY2012 = Oct 2011 to Sep 2012

PDF_DIR = Path("data/raw_pdfs")
CSV_DIR = Path("data/raw_csvs")
UNPARSED_DIR = Path("data/unparsed_lines")

REDO_CSVS = False   # True = rebuild CSVs that already exist

WAIT = 1   # seconds between requests 
REQUEST_HEADERS = {"User-Agent": "DATA1030 class project"}

# only needed if tesseract isn't on your PATH. falls back to the default
# Windows install location if that exists
TESSERACT = None
if Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").exists():
    TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
OCR_DPI = 450   # 300 misreads some digits (55 -> 35), 450 got them right


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


def cell_grid(page):
    # For OCR: every table cell is drawn as its own filled box
    # (white or grey), so the boxes tell us where the rows and columns are.
    # pdfplumber's own table finder lumps several rows together on these
    # (some pages also have one big frame box around everything, skip that)
    boxes = [r for r in page.rects
             if r["width"] > 3 and r["height"] > 3 and r["width"] < page.width * 0.6]
    boxes.sort(key=lambda r: (r["top"], r["x0"]))

    rows = []
    for b in boxes:
        # same row if it starts at (about) the same height
        if rows and abs(b["top"] - rows[-1][0]["top"]) < 2:
            # skip a box drawn twice in the same spot
            if all(abs(b["x0"] - other["x0"]) > 1 for other in rows[-1]):
                rows[-1].append(b)
        else:
            rows.append([b])

    return [[(b["x0"], b["top"], b["x1"], b["bottom"])
             for b in sorted(row, key=lambda r: r["x0"])] for row in rows]


#OCR adjustments
def fix_case_no(text):
    '''    
    OCR mixes up O and 0 (and a few others) in case numbers and sometimes
    adds spaces, e.g. "O5-RC-316129", "07-RCO-318048", "12-RC-31 1053"
    '''
    m = re.search(r"([0-9OoIlS]{2})-([A-Z0-9]{2,3})-([0-9OoIlS]{5,6})", text.replace(" ", ""))
    if not m:
        return text
    digits = str.maketrans("OoIlS", "00115")
    kind = m.group(2)[:2].replace("0", "O")
    return f"{m.group(1).translate(digits)}-{kind}-{m.group(3).translate(digits)}"


# column names from the reports that do have text, by number of columns
# (1 union, 2 unions, 3 unions, UD votes). OCR'd headers come out garbled
# now and then, so for those we go by how many columns the table has
_start = ["Region", "Case Number", "Case ID", "Case Name", "Case Type",
          "Dispute Unit City", "Dispute Unit State", "Election Held Date",
          "Num Eligible Voters"]
_end = ["Stipulated / Consent / Directed", "Cert of Rep (Win)", "Cert of Results (Loss)"]
HEADERS = {
    17: _start + ["Valid Votes Against", "Votes for Labor Org 1", "Labor Org 1 Name"]
         + _end + ["Closed Date", "Closed Reason"],
    20: _start + ["Valid Votes Against", "Votes for Labor Org 1", "Labor Org 1 Name",
                  "Votes for Labor Org 2", "Labor Org 2 Name"]
         + _end + ["Union To Certify", "Closed Date", "Closed Reason"],
    22: _start + ["Valid Votes Against", "Votes for Labor Org 1", "Labor Org 1 Name",
                  "Votes for Labor Org 2", "Labor Org 2 Name", "Votes For Labor Org3", "Labor Org3"]
         + _end + ["Union To Certify", "Closed Date", "Closed Reason"],
    15: _start + ["For Proposition", "Against Proposition", "Labor Org 1 Name",
                  "Stipulated / Consent / Directed", "Closed Date", "Closed Reason"],
}
ALL_HEADERS = sorted({h for names in HEADERS.values() for h in names})


def words_in(words, box):
    # the OCR'd words whose middle falls inside box, read line by line
    x0, top, x1, bottom = box
    inside = sorted((w for w in words if x0 <= w[0] <= x1 and top <= w[1] <= bottom),
                    key=lambda w: w[1])
    lines = []
    for w in inside:
        if lines and w[1] - lines[-1][0][1] < 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    text = " ".join(t for line in lines for _, _, t in sorted(line))
    # table borders sometimes get read as | or —
    return " ".join(text.replace("|", " ").replace("—", " ").split())


def ocr_cell(img, box, digits=False, scale=None, bw=False):
    # read one cell on its own. on a full page tesseract skips short or lone
    # bits like "RC", "CA", "LOSS" or a single digit, and reads some digits
    # wrong. one cell at a time (and digits only, for numbers) is a lot better
    import pytesseract
    k = OCR_DPI / 72
    x0, top, x1, bottom = box
    pad = int(k)   # stay about 1pt inside the cell so the border isn't read as a 1
    crop = img.crop((int(x0 * k) + pad, int(top * k) + pad, int(x1 * k) - pad, int(bottom * k) - pad))
    crop = crop.convert("L")
    config = "--psm 6"
    if digits:
        config += " -c tessedit_char_whitelist=0123456789"
    if scale is None:
        scale = 1 if digits else 2
    if scale != 1:
        crop = crop.resize((crop.width * scale, crop.height * scale))
    if bw:
        crop = crop.point(lambda v: 255 if v > 180 else 0)
    crop = ImageOps.expand(crop, border=20, fill=255)
    text = pytesseract.image_to_string(crop, config=config)
    return " ".join(text.replace("|", " ").split())


def reread_numbers(img, row, cell_boxes):
    # a row that fails ocr_check gets its numbers read a few other ways
    # (bigger, black and white), and we keep the first combination that
    # passes. if nothing does, the row stays as it was and gets flagged
    number_cols = [c for c in cell_boxes if "Vote" in c]   # includes "Voters"
    options = []
    for col in number_cols:
        reads = [row[col]]
        for scale, bw in [(2, False), (1, True), (2, True)]:
            text = ocr_cell(img, cell_boxes[col], digits=True, scale=scale, bw=bw)
            if text not in reads:
                reads.append(text)
        options.append(reads)

    for combo in itertools.product(*options):
        trial = dict(row, **dict(zip(number_cols, combo)))
        if not ocr_check(trial):
            return trial
    return row


def ocr_tables(page, state):
    # some reports have no actual text in them (the letters are just drawn
    # shapes, or a font that comes out as "(cid:2)(cid:3)..."), so every cell
    # reads as empty. for those we OCR the whole page once and put each word
    # in the cell it sits in. returns the same shape as page.extract_tables().
    # state carries the header over to the next page, since some of these
    # reports only print it on the first page
    import pytesseract
    if TESSERACT:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT

    grid = cell_grid(page)
    if not grid:
        return []

    img = page.to_image(resolution=OCR_DPI).original
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    k = 72 / OCR_DPI   # image pixels -> PDF points
    words = []         # (x, y, text) at the middle of each word
    for text, left, top, w, h in zip(data["text"], data["left"], data["top"],
                                     data["width"], data["height"]):
        if text.strip():
            words.append(((left + w / 2) * k, (top + h / 2) * k, text))

    header, columns = state.get("header"), state.get("columns")
    table = [header] if header else []
    for boxes in grid:
        texts = [words_in(words, b) for b in boxes]

        if any(t.replace(" ", "").lower() == "casenumber" for t in texts):
            # header row: remember where each column is
            if len(texts) in HEADERS:
                header = HEADERS[len(texts)]
            else:   # some other table shape, match each name to the closest known one
                header = []
                for t in texts:
                    match = difflib.get_close_matches(t, ALL_HEADERS, n=1, cutoff=0.6)
                    header.append(match[0] if match else t)
            columns = [(b[0], b[2]) for b in boxes]
            table.append(header)
            continue
        if columns is None:   # stuff above the first header, e.g. the summary
            table.append(texts)
            continue

        # empty cells have no box drawn, so use the header's columns instead
        # of this row's boxes. the row goes from its highest to lowest box
        top = min(b[1] for b in boxes)
        bottom = max(b[3] for b in boxes)
        row, cell_boxes = {}, {}
        for (x0, x1), name in zip(columns, header):
            box = (x0, top, x1, bottom)
            cell_boxes[name] = box
            if name == "Case ID":
                text = ""   # just a little picture in these PDFs, no text
            elif "Vote" in name or "Proposition" in name:
                text = ocr_cell(img, box, digits=True)
            else:
                text = words_in(words, box) or ocr_cell(img, box)
            if name == "Case Number":
                text = fix_case_no(text)
                if not re.fullmatch(CASE_NO, text):   # try the cell on its own
                    text = fix_case_no(ocr_cell(img, box))
                # sometimes the "09-" at the front goes missing. it's always
                # the same as the first number in the Region column, so add it back
                part = re.fullmatch(r"-?([A-Z]{2}-\d{5,6})", text)
                region = re.match(r"\d{2}", row.get("Region", ""))
                if part and region:
                    text = f"{region.group()}-{part.group(1)}"
            # an empty WON/LOSS cell sometimes comes back as junk like "Hi"
            if name == "Cert of Rep (Win)":
                text = "WON" if "WON" in text.upper() else ""
            if name == "Cert of Results (Loss)":
                text = "LOSS" if "LOS" in text.upper() else ""
            row[name] = text

        # only real election rows, not the leftover bits of a split row
        if re.fullmatch(CASE_NO, row.get("Case Number", "")) and ocr_check(row):
            row = reread_numbers(img, row, cell_boxes)
        table.append([row[name] for name in header])

    state["header"], state["columns"] = header, columns

    return [table]   # one "table" per page, the header rows sort it out


def ocr_check(row):
    # OCR gets maybe 1 number in 100 wrong or blank. these checks catch a lot
    # of those, so the rows worth looking up on the case page are easy to find
    if row.get("Case Type") == "UD" or row["Case Number"][3:5] == "UD":
        return ""

    def num(col):
        value = row.get(col, "")
        return int(value) if value.isdigit() else None

    eligible = num("Num Eligible Voters")
    against = num("Valid Votes Against")
    votes_for = num("Votes for Labor Org 1")
    if None in (eligible, against, votes_for):
        return "missing number"

    others = sum(num(c) or 0 for c in ("Votes for Labor Org 2", "Votes For Labor Org3"))
    if votes_for + against + others > eligible:
        return "more votes than voters"

    won = bool(row.get("Cert of Rep (Win)"))
    lost = bool(row.get("Cert of Results (Loss)"))
    if others == 0 and won and not lost and votes_for <= against:
        return "says WON but votes say lost"
    if others == 0 and lost and not won and votes_for > against:
        return "says LOSS but votes say won"
    return ""


def read_tables(pdf, source, use_ocr=False):
    rows = []
    ocr_state = {}
    # a table that carries on from the last page doesn't always repeat its
    # header, and sometimes the header itself gets cut in half by the page
    # break (top half at the bottom of one page, the rest on the next)
    last_header = None   # header of the previous table
    cut_header = None    # previous table was just a header with nothing under it
    for page_no, page in enumerate(pdf.pages, start=1):
        # a row can get cut by a page break. the rest of it is at the top of
        # the next page, with no case number, so keep track of the last row
        last_row = rows[-1] if rows else None
        tables = ocr_tables(page, ocr_state) if use_ocr else page.extract_tables()

        for table in tables:
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
                    row["extraction_method"] = "ocr" if use_ocr else "table"
                    row["page"] = page_no
                    rows.append(row)
                    found += 1
                elif last_row and found == 0 and not re.search(CASE_NO, " ".join(cells)):
                    # leftover bit of the last row from the previous page
                    for col, text in row.items():
                        if text and col != "Case Number":
                            last_row[col] = (last_row.get(col, "") + " " + text).strip()

            if header:
                last_header = header
            cut_header = header if header and under_header == 0 else None

    if use_ocr:
        for row in rows:
            row["ocr_check"] = ocr_check(row)
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

    for pdf_path in pdfs:
        fy = pdf_path.parent.name
        if int(fy[2:]) < FIRST_FY:
            continue

        csv_path = CSV_DIR / fy / (pdf_path.stem + ".csv")
        unparsed_path = UNPARSED_DIR / f"{fy}_{pdf_path.stem}.txt"
        if csv_path.exists() and not REDO_CSVS:
            continue

        source = f"{fy}/{pdf_path.name}"
        unparsed = []
        columns = None   # None = keep the table's own columns
        how = "table"
        with pdfplumber.open(pdf_path) as pdf:
            rows = read_tables(pdf, source)
            if not rows:
                how = "text"
                rows, unparsed = read_text(pdf, source)
                columns = TEXT_COLUMNS
            if not rows:
                # no usable text at all, try reading the page images
                how = "ocr"
                rows = read_tables(pdf, source, use_ocr=True)
                columns = None
            if not rows:
                first_page = pdf.pages[0].extract_text() or ""
                if "produced no results" in " ".join(first_page.split()):
                    how = "report is empty"
                else:
                    how = "nothing found, check this PDF"

        write_csv(rows, csv_path, columns)

        # lines we couldn't split go in their own file so they can be checked
        if unparsed:
            UNPARSED_DIR.mkdir(parents=True, exist_ok=True)
            unparsed_path.write_text("\n".join(unparsed), encoding="utf-8")
        elif unparsed_path.exists():
            unparsed_path.unlink()

        print(f"  {source}: {len(rows)} rows ({how}), {len(unparsed)} unparsed")


if __name__ == "__main__":
    download_all()
    convert_all()


'''
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

9b. OCR files only (extraction_method == "ocr", most of 2023 and April
   2019): about 1 number in 100 is still wrong or blank. Rows with
   something in ocr_check didn't add up (missing number, more votes than
   voters, WON/LOSS doesn't match the votes). Look those up on their case
   page (nlrb.gov/case/<case number>) or drop them. Names can have small
   typos ("Califarnia"), so match employers loosely. March 2023's PDF is
   cut off on the right: no Closed Reason, and Closed Date is cut short,
   so use the WON/LOSS columns for the target there.
   October 2025's report has no elections in it at all (every section
   says "produced no results"), so expect a gap that month.

10. Before modeling, drop everything that's only known after the vote:
    vote counts, closed date/reason, the WON/LOSS columns, Union To
    Certify. Also the bookkeeping ones (source_pdf, page, raw_line, ...).

11. Split by election date (not closed date), test on the latest years,
    GroupKFold by employer inside the training years. Anything like a
    union's past win rate can only use earlier elections.

12. Save to data/elections_clean.csv and write up the data documentation.
'''