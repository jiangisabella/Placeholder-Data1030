## 1. Election date minus file date

Yes, that's **`days_petition_to_election`**: election date minus petition date. Filing the petition is what opens an RC case, so the petition date and the "Case File Date" are the same event.

- The petition date comes from the docket (the earliest signed petition) for 13,398 rows. Most reports don't have a file date column, so the docket is the main source.
- For 214 rows with no petition in the docket, it falls back to the report's file date. Only 1,333 rows from the early reports have one.
- Where both exist, they're the same day 86% of the time and within 3 days 99% of the time, so combining them is safe.
- It's filled in for 13,500 of 15,357 elections. The other 1,857 are missing mostly because the docket scrape was cut off before the petition (see the TODO), plus 123 rows with no election date.

`days_file_to_election`, the column I removed, was the same calculation using only the report's file date, so it was a weaker copy.

## 2. What "checks" means

Checks are lines that **print** a count so you can see whether the data, or a step, looks right. They don't add columns or remove rows. For example:

- "case type column disagrees with case number: 0" confirms the case number gives the right case type.
- "union_won vs majority of votes" is a crosstab. 240 elections disagree, and those are worth looking at.
- "more votes than eligible voters: 146" and "no valid votes: 48" flag likely PDF errors.

The one check that changes data is "election date after the case closed": those 3 dates are typos, so they're set to missing.

## 3. The whole process, in order

**Starting point:** the raw CSVs from `pdfs_to_csv.py`, one per monthly report, with headers spelled differently from year to year.

**Step 1: Load each fiscal year** (`load_year`)
- Read everything as text so values like "01" aren't changed.
- Skip 3 report files that only repeat rows from other files.
- Drop 4 junk columns: FY2012 summary counts, an empty spacer column and `extraction_method`.
- Rename every header spelling to one name. For example, "Num Eligible Voters" and "Number of Eligible Voters" both become `eligible`.
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
- **Add** `case_type` (e.g. RC) and `region` (e.g. 01), both read from `case_number` (`01-RC-216927`).
- Check against `case_type_reported`: 0 disagree.
- Keep RC only: **16,123 rows**. RD, RM and UD cases ask a different question.
- **Drop** 9 raw columns nothing later uses: `page`, `region_offices`, `case_id`, `case_type_reported`, `votes_for_2`, `votes_for_3`, `for_proposition`, `against_proposition`, and `union_to_certify` (that one is only known after the vote).

**Step 3: Remove repeats**
- Count the unions on each election's ballot. This is a temporary helper, removed in step 4.
- Drop 112 repeat listings of the same vote, mostly from reports that overlap. A reopened case keeps its final closing.
- Check for one vote filed under two case types: finds 0.

**Step 4: Keep single-union elections**
- Drop 654 elections with 2 or 3 unions on the ballot: **15,357 rows.**
- **Drop** the helpers and `union_name_2`, `union_name_3`.

**Step 5: Build the target**
- **Add** `union_won`: 1 for Certification of Representative, 0 for Certification of Results.
- 204 rows from two FY2012 reports have no closed reason, so they use the report's WON/LOSS column instead.
- Check against the WON/LOSS columns.
- **Drop** `reported_win_loss`, `reported_win`, `reported_loss`.

**Step 6: Vote checks, then drop post-vote columns**
- Check against the vote majority, votes > eligible, and no votes.
- Set the 3 impossible election dates to missing.
- **Drop** `votes_for`, `votes_against`, `closed_date`, `closed_reason`. These are only known after the vote, and `union_won` already holds what's needed from them.

**Step 7: Features from the report columns**
- **Add** `census_region`: `state` mapped to Northeast, Midwest, South, West or Territory.
- **Add** `parent_union`: `union_name` matched to its parent union, e.g. "Teamsters Local 340" → Teamsters.
- **Add** `employer`: `case_name` cleaned (lowercase, no "Inc"/"LLC", nothing after d/b/a). It's the grouping key for GroupKFold.

**Step 8: Participants** (from `case_participants_raw.csv`, one row per case)
- **Add** 8 columns: `n_participants`, `employer_has_lawyer`, `n_employer_lawyers`, `employer_mgmt_firm` (a big management-side law firm), `employer_law_firm`, `petitioner_has_lawyer`, `n_petitioner_lawyers`, `has_intervenor`.

**Step 9: Docket** (from `docket_activity.csv`, documents filed before the vote only)
- **Add** 10 columns: `petition_date`, `days_petition_to_election`, `n_docs_before_vote`, `pre_hearing`, `pre_postponed`, `pre_ulp_blocking`, `pre_request_review`, `n_employer_filings_before_vote`, `has_docket`, `docket_truncated`.

**Step 10: Save**
- Put the columns in order, check none were missed, sort by election date, and write `data/elections_clean.csv`.

**End result: 15,357 rows × 35 columns.**

| | count | columns |
|---|---|---|
| kept from raw | 11 | `source_pdf`, `fiscal_year`, `case_number`, `case_name`, `city`, `state`, `union_name`, `method`, `eligible`, `file_date`, `election_date` |
| added | 24 | `case_type`, `region`, `census_region`, `parent_union`, `employer`, the 8 participant columns, the 10 docket columns, `union_won` |
| dropped from raw | 18 | the 9 in step 2, the 2 in step 4, the 3 in step 5, the 4 in step 6 |

Every column except `union_won` is known before the vote.