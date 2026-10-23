# How `src/preprocessmanual.py` works

`src/preprocessmanual.py` turns the raw election CSVs into `data/elections_clean.csv`: one row
per single-union RC election, with a 0/1 target `union_won`. For what each check found, and
why some rows are fixed or dropped, see [data/data_checks.md](data/data_checks.md).

Run it from the project root:

```
python src/preprocessmanual.py
```

## Petition date and `days_petition_to_election`

This is the main feature of interest: **`days_petition_to_election`** = election date minus
petition date. Filing the petition is what opens an RC case, so the petition date and the
report's "Case File Date" are the same event.

- The petition date comes from the docket (the earliest signed petition) for 14,452 rows. Most
  reports don't have a file date column, so the docket is the main source.
- For 153 rows with no petition in the docket, it falls back to the report's file date. Only
  the early reports have one.
- Where both exist, they're the same day 86% of the time and within 3 days 99% of the time, so
  combining them is safe.
- It's filled in for 14,601 of 15,312 elections (median 31 days).
- **707 have no petition date.** Their dockets are complete but never list the petition; the
  public docket doesn't show every document. They stay missing (`data/data_checks.md`,
  check 19).
- **4 more have a petition dated after the vote.** That's a docket upload date, so the gap is
  set to missing (check 17).

The first docket scrape only got the 10 newest documents per case, so long, contested cases
lost their petition. Those 2,081 cases were re-scraped in full
(`data/docket_activity_merged.csv`), which added about 1,000 petition dates.

## What "checks" means

Checks are lines the script **prints** so you can see whether the data, or a step, looks
right. For example:

- "union_won vs majority of votes" is a crosstab of the label against the tally.
- "more votes than eligible voters: 145" flags typos in the NLRB's own vote counts.

Most checks only print. A few lead to a fix, and the script prints how many rows each one
changed:

| fix | rows | step |
|---|---|---|
| label corrected from the docket | 33 | 6 |
| multi-unit / both WON and LOSS rows that contradict their own tally, dropped | 19 | 6 |
| `eligible` = 0 set to missing | all zeros | 7 |
| `eligible` set to missing where there are more votes than eligible voters | 145 | 7 |
| impossible election dates set to missing | 3 | 7 |
| missing election date filled from the docket tally | 97 | 7 |
| no election date and no tally, dropped | 26 | 7 |
| petition dated after the vote, gap set to missing | 4 | 10 |

## The whole process, in order

**Starting point:** the raw CSVs from `pdfs_to_csv.py`, one per monthly report, with headers
spelled differently from year to year.

**Step 1: Load each fiscal year** (`load_year`)
- Read everything as text so values like "01" aren't changed.
- Skip 3 report files that only repeat rows from other files (`DROP_FILES`).
- Drop columns that are never used (`DROP_COLUMNS`):
  - junk: FY2012 summary counts, an empty spacer column, `extraction_method`
  - the proposition votes, only filled in for UD (deauthorization) cases
  - votes for unions 2 and 3 (multi-union elections are dropped in step 4)
  - the election method (stipulated / consent / directed)
  - `source_pdf`, only needed to skip the repeat reports
- Rename every header spelling to one name. For example, "Num Eligible Voters" and "Number of
  Eligible Voters" both become `eligible`. The old one-column "Union (Win / Loss)" header and
  the newer "Cert of Rep (Win)" header both become `reported_win`.
- Stop with an error if a report has a header that isn't in `HEADERS`.
- Add any column a year is missing as blank. **Add** `fiscal_year`.
- Clean the text (curly quotes, "AFL- CIO" → "AFL-CIO"), and convert numbers and dates.
- Stack all years: **18,995 rows, 22 columns.**

The 22 raw columns:

| group | columns |
|---|---|
| bookkeeping | `page`, `fiscal_year` |
| case info | `case_number`, `case_id`, `case_name`, `case_type`, `region_offices`, `file_date` |
| location | `city`, `state` |
| election | `election_date`, `eligible` |
| ballot | `union_name`, `union_name_2`, `union_name_3` |
| vote counts | `votes_for`, `votes_against` |
| outcome | `closed_date`, `closed_reason`, `reported_win`, `reported_loss`, `union_to_certify` |

**Step 2: Keep RC cases only**
- `case_type` comes straight from the report's "Case Type" column. It's filled in on every row
  and always matches the case number.
- **Add** `region` (e.g. 01), the first two digits of `case_number` (`01-RC-216927`).
- Keep RC only: **16,123 rows**. RD, RM and UD cases ask a different question.
- **Drop** 4 raw columns nothing later uses: `page`, `region_offices`, `case_id`, and
  `union_to_certify` (that one is only known after the vote).

**Step 3: Remove repeats**
- Count two temporary helpers before anything is dropped, so every listing is seen:
  - `n_unions`: how many unions were on the ballot (used in step 4).
  - `n_units`: how many different votes one case has on one election date, i.e. separate
    voting units (used in step 6).
- Drop 112 repeat listings of the same vote, mostly from reports that overlap. A reopened case
  keeps its final closing.

**Step 4: Keep single-union elections**
- Drop 654 elections with 2 or 3 unions on the ballot: **15,357 rows.**
- **Drop** `n_unions`, `union_name_2`, `union_name_3`.

**Step 5: Load the docket** (`docket_activity_merged.csv`, scraped from the live case pages
on nlrb.gov by `src/docket_scraping.py`)
- One row per document: `case_number`, `activity_date`, `document`, `issued_filed_by`.
- Cleaning: drop exact duplicate rows, strip the `*` after some document names, turn
  `activity_date` into a date.
- These are full dockets. Cases the first scrape cut off at 10 documents were re-scraped in
  full and merged in.
- Pull out two things per case, used only for checking and filling dates, never as features
  (both come after the vote):
  - the certification the case got: `rep_cases` / `res_cases` (sets of case numbers),
    `both_cases`, and `docket_cert` (1 = Representative, 0 = Results);
  - `first_tally`: the date of its first Tally of Ballots, usually the day of the vote.

**Step 6: Build and check the target**
- **Add** `union_won`: 1 for Certification of Representative, 0 for Certification of Results.
- **Add** a temporary `reported_result` (won / lost / both / blank) from the report's own
  WON/LOSS columns.
- 204 rows from two FY2012 reports have no closed reason, so they use `reported_result`
  instead.
- **Correct 33 labels** where the report's closed reason is wrong. The rule: the docket's
  certification disagrees, **and** the tally or WON/LOSS column on the same row agrees with the
  docket (19 become wins, 14 losses).
- **Drop 19 rows** that can't be sorted out:
  - units in multi-unit cases whose case-level label contradicts their own tally;
  - one row marked both WON and LOSS that the docket doesn't settle.
- **Drop** `reported_win`, `reported_loss`, `reported_result`, `n_units`.

**Step 7: Vote and date checks, then drop post-vote columns**
- Print the checks: label vs. vote majority, votes > eligible, no votes, eligible 0 or missing.
- **Set `eligible` to missing** where it's 0 (it means "not entered", not a unit of 0) or
  where there are more votes than eligible voters (the count is wrong, and `eligible` is a
  feature).
- Set the 3 impossible election dates (after the case closed) to missing.
- **Fill 97 missing election dates** from the docket's first Tally of Ballots.
- **Drop the 26 rows** with no election date and no tally: **15,312 rows.**
- **Drop** `votes_for`, `votes_against`, `closed_date`, `closed_reason`. These are only known
  after the vote, and `union_won` already holds what's needed from them.

**Step 8: Features from the report columns**
- **Add** `census_region`: `state` mapped to Northeast, Midwest, South, West or Territory.
- **Add** `parent_union`: `union_name` matched to its parent union, e.g. "Teamsters Local
  340" → Teamsters.
- **Add** `employer`: `case_name` cleaned (lowercase, no "Inc"/"LLC", nothing after d/b/a).
  It's not a feature, it's the grouping key for GroupKFold, so the same employer (e.g. the
  726 Starbucks elections) doesn't end up in both training and validation folds.

**Step 9: Participants** (`case_participants_raw.csv`, one row per participant)
- The raw text is split into `party` (which side), `ptype` (their role) and `org` (their
  organization, e.g. the lawyer's law firm).
- Counted per case, then merged onto the elections. **Add** 7 columns: `n_participants`,
  `employer_has_lawyer`, `n_employer_lawyers`, `employer_law_firms`, `petitioner_has_lawyer`,
  `n_petitioner_lawyers`, `has_intervenor`.
- `employer_law_firms` is every firm the employer's lawyers come from, joined with `"; "`
  (split it back with `.str.split("; ")`). Sorting firms into big / small is left for the
  analysis.

**Step 10: Petition date** (from the docket)
- **Add** `petition_date`: the earliest signed petition in the docket. If the docket has none,
  it falls back to the report's file date (early reports only).
- **Add** `days_petition_to_election`. A petition dated after the vote is set to missing.
- No other docket features are built for now.

**Step 11: Save**
- Put the columns in order, check none were missed, sort by election date, and write
  `data/elections_clean.csv`.

**End result: 15,312 rows × 24 columns** (70.8% won).

| | count | columns |
|---|---|---|
| kept from raw | 10 | `fiscal_year`, `case_number`, `case_type`, `case_name`, `city`, `state`, `union_name`, `eligible`, `file_date`, `election_date` |
| added | 14 | `region`, `census_region`, `parent_union`, `employer`, the 7 participant columns, `petition_date`, `days_petition_to_election`, `union_won` |
| dropped from raw | 12 | the 4 in step 2, the 2 in step 4, the 2 in step 6, the 4 in step 7 |

Every column except `union_won` is known before the vote. Before modeling, take out the
bookkeeping columns (`case_number`, `case_name`, `employer`) as features, and keep `employer`
aside as the GroupKFold group.

Row counts through the pipeline:

| after | rows |
|---|---|
| loading | 18,995 |
| RC only | 16,123 |
| removing repeats | 16,011 |
| single-union only | 15,357 |
| dropping unclear labels | 15,338 |
| dropping rows with no election date | 15,312 |
