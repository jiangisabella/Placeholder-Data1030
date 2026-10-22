# How `src/preprocess.py` works

`src/preprocess.py` turns the raw election CSVs into `data/elections_clean.csv`: one row per
single-union RC election, with a 0/1 target `union_won`. For what each check found, and why
some rows are fixed or dropped, see [data/data_checks.md](data/data_checks.md).

## Election date minus file date

This is **`days_petition_to_election`**: election date minus petition date. Filing the petition
is what opens an RC case, so the petition date and the report's "Case File Date" are the same
event.

- The petition date comes from the docket (the earliest signed petition) for 14,453 rows. Most
  reports don't have a file date column, so the docket is the main source.
- For 152 rows with no petition in the docket, it falls back to the report's file date. Only
  1,328 rows, all from the early reports, have one.
- Where both exist, they're the same day 86% of the time and within 3 days 99% of the time, so
  combining them is safe.
- It's filled in for 14,601 of 15,312 elections (median 31 days).
- **707 have no petition date.** Their dockets are complete but never list the petition; the
  public docket doesn't show every document. They stay missing (`data/data_checks.md`,
  check 19).
- **4 more have a petition dated after the vote.** That's a docket upload date, so the gap is
  set to missing (check 17).

This used to be much worse. The first docket scrape only got the 10 newest documents per case,
so long, contested cases lost their petition. Those 2,081 cases were re-scraped in full
(`data/docket_activity_merged.csv`), which added about 1,000 petition dates.

## What "checks" means

Checks are lines the script **prints** so you can see whether the data, or a step, looks
right. For example:

- "case type column disagrees with case number: 0" confirms the case number gives the right
  case type.
- "union_won vs majority of votes" is a crosstab of the label against the tally.
- "more votes than eligible voters: 145" flags typos in the NLRB's own vote counts.

Most checks only print. A few lead to a fix, and the script prints how many rows each one
changed:

| fix | rows | step |
|---|---|---|
| label corrected from the docket | 33 | 6 |
| multi-unit / both WON and LOSS rows that contradict their own tally, dropped | 19 | 6 |
| `eligible` = 0 set to missing | all zeros | 7 |
| impossible election dates set to missing | 3 | 7 |
| missing election date filled from the docket tally | 97 | 7 |
| no election date and no tally, dropped | 26 | 7 |
| petition dated after the vote, gap set to missing | 4 | 10 |

## The whole process, in order

**Starting point:** the raw CSVs from `pdfs_to_csv.py`, one per monthly report, with headers
spelled differently from year to year.

**Step 1: Load each fiscal year** (`load_year`)
- Read everything as text so values like "01" aren't changed.
- Skip 3 report files that only repeat rows from other files.
- Drop 4 junk columns: FY2012 summary counts, an empty spacer column and `extraction_method`.
- Rename every header spelling to one name. For example, "Num Eligible Voters" and "Number of
  Eligible Voters" both become `eligible`.
- Add any column a year is missing as blank. **Add** `fiscal_year`.
- Clean the text (curly quotes, "AFL- CIO" → "AFL-CIO"), and convert numbers and dates.
- Stack all years: **18,995 rows, 29 columns.**

The 29 raw columns:

| group | columns |
|---|---|
| bookkeeping | `source_pdf`, `page`, `fiscal_year` |
| case info | `case_number`, `case_id`, `case_name`, `case_type_reported`, `region_offices`, `file_date` |
| location | `city`, `state` |
| election | `election_date`, `eligible`, `method` |
| ballot | `union_name`, `union_name_2`, `union_name_3` |
| vote counts | `votes_for`, `votes_against`, `votes_for_2`, `votes_for_3`, `for_proposition`, `against_proposition` |
| outcome | `closed_date`, `closed_reason`, `reported_win_loss`, `reported_win`, `reported_loss`, `union_to_certify` |

**Step 2: Keep RC cases only**
- **Add** `case_type` (e.g. RC) and `region` (e.g. 01), both read from `case_number`
  (`01-RC-216927`).
- Check against `case_type_reported`: 0 disagree.
- Keep RC only: **16,123 rows**. RD, RM and UD cases ask a different question.
- **Drop** 9 raw columns nothing later uses: `page`, `region_offices`, `case_id`,
  `case_type_reported`, `votes_for_2`, `votes_for_3`, `for_proposition`, `against_proposition`,
  and `union_to_certify` (that one is only known after the vote).

**Step 3: Remove repeats**
- Count two temporary helpers before anything is dropped, so every listing is seen:
  - `n_unions`: how many unions were on the ballot (used in step 4).
  - `n_units`: how many different votes one case has on one election date, i.e. separate
    voting units (used in step 6).
- Drop 112 repeat listings of the same vote, mostly from reports that overlap. A reopened case
  keeps its final closing.
- Check for one vote filed under two case types: finds 0.

**Step 4: Keep single-union elections**
- Drop 654 elections with 2 or 3 unions on the ballot: **15,357 rows.**
- **Drop** `n_unions`, `union_name_2`, `union_name_3`.

**Step 5: Load the docket** (`docket_activity_merged.csv`, scraped from the live case pages
on nlrb.gov by `src/docket_scraping.py`)
- These are full dockets. Cases the first scrape cut off at 10 documents were re-scraped in
  full and merged in.
- Loaded here because steps 6 and 7 use it. The features from it are built in step 10.
- Pull out two things per case, used only for checking and never as features (both come
  after the vote):
  - the certification the case got (Representative, Results, or both);
  - the date of its first Tally of Ballots.

**Step 6: Build and check the target**
- **Add** `union_won`: 1 for Certification of Representative, 0 for Certification of Results.
- 204 rows from two FY2012 reports have no closed reason, so they use the report's WON/LOSS
  column instead.
- **Correct 33 labels** where the report's closed reason is wrong. The rule: the docket's
  certification disagrees, **and** the tally or WON/LOSS column on the same row agrees with the
  docket (19 become wins, 14 losses).
- **Drop 19 rows** that can't be sorted out:
  - units in multi-unit cases whose case-level label contradicts their own tally;
  - one row marked both WON and LOSS that the docket doesn't settle.
- **Drop** `reported_win_loss`, `reported_win`, `reported_loss`, `n_units`.

**Step 7: Vote and date checks, then drop post-vote columns**
- Print the checks: label vs. vote majority, votes > eligible, no votes, eligible 0 or missing.
- **Set `eligible` = 0 to missing.** It means "not entered", not a unit of 0.
- Set the 3 impossible election dates to missing.
- **Fill 97 missing election dates** from the docket's first Tally of Ballots, usually the day
  of the vote.
- **Drop the 26 rows** with no election date and no tally: **15,312 rows.**
- **Drop** `votes_for`, `votes_against`, `closed_date`, `closed_reason`. These are only known
  after the vote, and `union_won` already holds what's needed from them.

**Step 8: Features from the report columns**
- **Add** `census_region`: `state` mapped to Northeast, Midwest, South, West or Territory.
- **Add** `parent_union`: `union_name` matched to its parent union, e.g. "Teamsters Local
  340" → Teamsters.
- **Add** `employer`: `case_name` cleaned (lowercase, no "Inc"/"LLC", nothing after d/b/a).
  It's the grouping key for GroupKFold.

**Step 9: Participants** (`case_participants_raw.csv`, one row per case)
- **Add** 8 columns: `n_participants`, `employer_has_lawyer`, `n_employer_lawyers`,
  `employer_mgmt_firm` (a big management-side law firm), `employer_law_firm`,
  `petitioner_has_lawyer`, `n_petitioner_lawyers`, `has_intervenor`.

**Step 10: Docket features** (documents filed before the vote only)
- **Add** 9 columns: `petition_date`, `days_petition_to_election`, `n_docs_before_vote`,
  `pre_hearing`, `pre_postponed`, `pre_ulp_blocking`, `pre_request_review`,
  `n_employer_filings_before_vote`, `has_docket`.
- `petition_date` is the earliest signed petition in the docket. If the docket has none, it
  falls back to the report's file date (early reports only).
- `docket_truncated` was removed. It flagged dockets cut off at 10 documents, and after the
  full re-scrape none are.

**Step 11: Save**
- Put the columns in order, check none were missed, sort by election date, and write
  `data/elections_clean.csv`.

**End result: 15,312 rows × 34 columns** (70.8% won).

| | count | columns |
|---|---|---|
| kept from raw | 11 | `source_pdf`, `fiscal_year`, `case_number`, `case_name`, `city`, `state`, `union_name`, `method`, `eligible`, `file_date`, `election_date` |
| added | 23 | `case_type`, `region`, `census_region`, `parent_union`, `employer`, the 8 participant columns, the 9 docket columns, `union_won` |
| dropped from raw | 18 | the 9 in step 2, the 2 in step 4, the 3 in step 6, the 4 in step 7 |

Every column except `union_won` is known before the vote.

Row counts through the pipeline:

| after | rows |
|---|---|
| loading | 18,995 |
| RC only | 16,123 |
| removing repeats | 16,011 |
| single-union only | 15,357 |
| dropping unclear labels | 15,338 |
| dropping rows with no election date | 15,312 |
