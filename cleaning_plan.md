# Cleaning plan (original)

This was the plan written before the cleaning code. It's now done in
`src/preprocessmanual.py`, and some choices changed along the way (only RC cases are kept,
`case_type` comes from the report's own column, the election method isn't used). For what the
script actually does, see [processingexplained.md](processingexplained.md).

The raw CSVs from `src/pdfs_to_csv.py` are copied as-is from the PDFs. These were the cleaning steps planned.

1. **Load everything as strings** so "01" stays "01":
   ```python
   files = sorted(Path("data/raw_csvs").glob("FY*/*.csv"))
   df = pd.concat([pd.read_csv(f, dtype=str) for f in files], ignore_index=True)
   ```
2. **Column names** change between years, and table and text files name things differently. Print each file's columns first, then rename to one set: `case_number, case_name, city, state, file_date, election_date, eligible, votes_for, votes_against, union_name, method, closed_date, closed_reason`. The 2012 reports spell it "Stiplulated".
3. **Case type** = `case_number[3:5]`. Keep RC/RD/RM. Drop UD (votes on union-security clauses, not union elections). **Region** = `case_number[:2]` (ignore the report's own Region column, it lists several office codes).
4. **Numbers:** strip commas, then `pd.to_numeric`. **Dates:** `pd.to_datetime(..., format="mixed")` (some years write 4/1/2026, others 04/01/2026). A few rows have no election date.
5. **Duplicates.** Some months are linked on two year pages (the FY2012 page also links Oct 2012), so drop repeats on `case_number` + the 3 vote columns. One vote can also be filed as both an RC and an RM case with the same date and votes (Georgia Power, Aug 2026): keep one.
6. **Target:** `union_won = 1` if `closed_reason` contains "Representative" (Certific. of Representative), `0` if it contains "Results" (Certification of Results). Don't trust the WON/LOSS columns alone: two rows in April 2026 have both filled in. For RD cases, 1 means the union survived. Compare with `votes_for > votes_against` (tie = loss) and look at whatever disagrees. Vote share = for / (for + against); drop 0/0.
7. **Multi-union elections** (Labor Org 2/3 columns filled in, or a "2 Labor"/"3 Labor" section in text files): probably just drop these.
8. **Features:**
   - state → census region; PR/GU/VI as their own group or dropped
   - `log(eligible)`; check for eligible == 0 or more votes than voters
   - union name → parent union by keyword (teamster, seiu, workers united, ufcw, ibew, ...), everything else "Other"
   - normalized employer name (lowercase, no punctuation, drop inc/llc/corp and anything after d/b/a), needed for the group split
   - year and month of the election
   - days from filing to election: only the ~2012–2014 reports have a file date; later years would need the case pages
   - flag cases that closed years after the vote
9. **Text-parsed files only:** check rows with a `parse_note`, and pull closed date + reason out of `after_result` with `r"(\d{1,2}/\d{1,2}/\d{4})\s+(.*)"`. Names can be cut off. `case_id` is just an ID, not a feature.
10. **Missing months:** `data/skipped_pdfs.txt` lists the reports we couldn't read (Feb–Sep 2023, Apr 2019), plus Oct 2025, which has no elections. Mention this in the report and expect a gap in plots over time. Reports go by the month a case *closed*, so elections held in those months can still appear in later reports.
11. **Before modeling, drop everything only known after the vote:** vote counts, closed date/reason, WON/LOSS columns, Union To Certify. Also the bookkeeping columns (`source_pdf`, `page`, `raw_line`, ...).
12. **Split** by election date (not closed date) and test on the latest years; use GroupKFold by employer inside the training years. Anything like a union's past win rate may only use earlier elections.
13. Save to `data/elections_clean.csv` and write the data documentation.
