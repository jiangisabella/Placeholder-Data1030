# Data checks: what they test, what they found, what to do

`src/preprocess.py` prints a set of **checks** as it runs. A check is a count that tests
whether the data, or a cleaning step, makes sense. It doesn't change the data unless
noted. This file explains each check, looks into the rows it flags, and recommends
whether to keep, fix or drop them.

The numbers are from the 15,357 single-union RC elections left after step 4 of the
pipeline.

## How the anomalies were verified

Each suspicious row was traced back through up to five sources:

1. **Raw CSV** (`data/raw_csvs/`): what the extraction produced.
2. **Source PDF** (`data/raw_pdfs/`): what the NLRB report actually prints.
3. **NLRB xlsx** (`data/tmp_website_files/`, FY2020 onward): the same report as a spreadsheet.
4. **nlrb.gov case page**: the NLRB's current record for the case, including the tally.
5. **Docket certification** (`data/docket_activity_merged.csv`): whether the case has a
   *Certification of Representative* (win) or *Certification of Results* (loss) document.
   These are full dockets. The first scrape only got the 10 newest documents per case, and
   the 2,081 cases that hit that limit were re-scraped in full and merged in.

These are **two kinds of NLRB record, not five independent sources**:

- **The monthly election report (1–3).** A snapshot, published once, of the cases that closed
  that month. The CSV, PDF and xlsx are the same report, and for every case cited below the
  CSV matches what the PDF prints, so the extraction isn't the problem.
- **The live case record (4–5).** The case page on nlrb.gov shows the NLRB's case-management
  system as it stands now, including any later corrections. The docket file was scraped from
  these same pages (`src/docket_scraping.py`), so the docket and "nlrb.gov" count as one source.

When the two disagree, it's either a field mis-typed in the report or a correction made after
the report was published. The docket certification is the strongest single piece of evidence
because it's the legal document that was actually issued. Where it exists and the report's
tally agrees with the label, it matches `union_won` for 14,057 of 14,067 rows (99.9%).

## Summary of decisions

| # | Check | Rows | Cause | Recommendation |
|---|---|---|---|---|
| 1 | Unparseable numbers / dates | 0 | none | nothing to do |
| 2 | Case type ≠ case number | 0 | none | nothing to do |
| 3 | Exact repeats | 112 | overlapping reports, reopened cases | already dropped (keep one row each) |
| 4 | Same vote under two case types | 0 | none with RC only | nothing to do |
| 5 | Target from WON/LOSS fallback | 204 | 2 FY2012 reports have no closed reason | **keep**, docket agrees 173/173 |
| 6 | No target | 0 | none | nothing to do |
| 7a | Closed reason contradicted by the docket **and** the tally or WON/LOSS column | 33 | wrong closed reason in the report | **fix** `union_won` to the docket's value |
| 7b | Multi-unit case where a unit's label contradicts its own tally | 19 | case-level outcome repeated on each unit's row | **drop** |
| 8 | `union_won` ≠ vote majority (other rows) | ~200 | wrong vote counts in the NLRB's own data | **keep**, the label is confirmed |
| 9 | More votes than eligible voters | 146 | `eligible` = 0 (71) or typos in counts / `eligible` | **keep**, see check 11 |
| 10 | No valid votes (0–0) | 48 | tally never entered | **keep** |
| 11 | Eligible = 0 or missing | 85 | NLRB records "not entered" as 0 | **set 0 to missing**, keep rows |
| 12 | Election date after case closed | 3 | date typos | already set to missing, see 13 |
| 13 | No election date | 123 | blank in the PDF | **fill from docket tally date** (97), **drop** the rest (26) |
| 14 | Closed > 1 year after the vote | 185 | real litigation (objections, Board review) | **keep** |
| 15 | State with no census region | 0 | none | nothing to do |
| 16 | No participants / no docket | 4 | not found by the scraper | **keep**, features stay missing |
| 17 | Petition dated after the vote | 4 | docket upload date, not filing date | already set to missing |
| 18 | Petition → election > 1 year | 113 | litigated or blocked cases | **keep** |
| 19 | No petition date | 707 | the public docket doesn't list the petition | **keep**, feature stays missing |

**Status: all recommendations are applied in `src/preprocess.py`.** 45 rows dropped
(19 + 26), 33 labels corrected (19 to won, 14 to lost), every `eligible` of 0 set to missing,
and 97 election dates filled. `data/elections_clean.csv` now has **15,312 rows × 34 columns**.
The script prints each of these counts when it runs.

The numbers in the sections below are from **before** these fixes (15,357 rows), so they
describe the raw anomalies. After the fixes, `union_won` and the vote majority disagree on 190
rows instead of 240. Those are the wrong-tally rows in check 8, which are kept.

---

## 1. Unparseable numbers and dates

**What it checks:** while loading each year, any cell in a number column (`eligible`, vote
counts) that isn't a number, or any date that doesn't parse as `m/d/yyyy`.

**Result:** 0. Every value converted cleanly.

## 2. Case type column vs case number

**What it checks:** the case number contains the case type (`01-RC-216927` → RC). The
reports also have a separate "Case Type" column. The check counts rows where the two disagree.

**Result:** 0. The case number is a safe source for `case_type` and `region`.

## 3. Exact repeats

**What it checks:** the same vote (case number, election date, eligible, votes for, votes
against) listed more than once.

**Result:** 112 dropped: 4 within one report, 108 across reports. Most come from
reports that overlap (Sep/Oct 2022, Dec 2018/Jan 2019). 29 are reopened cases listed again
when they closed a second time; the latest closing is kept. Only one of those changed
outcome: **29-RC-198745** (36–0 vote) closed as a loss in June 2017 and was corrected to a
win in August 2017.

**Decision:** already handled.

## 4. Same vote filed under two case types

**What it checks:** one election filed as both an RC and an RM or RD case (same place, date and
counts, different case number).

**Result:** 0. Only RC cases are kept, so the RM/RD copy is already gone.

## 5. Target taken from the WON/LOSS column

**What it checks:** `union_won` normally comes from the closed reason. Two reports
(`FY2012/march2012.pdf`: 109 rows, `FY2012/november20111.pdf`: 95 rows) have no closed-reason
column, so the report's own WON/LOSS column is used instead.

**Investigation:** compared against the docket certification and the vote majority:

| `union_won` | docket: lost | docket: won | docket: none |
|---|---|---|---|
| 0 | 66 | 0 | 12 |
| 1 | 0 | 107 | 19 |

Every row with a docket certification agrees (173/173), and the vote majority agrees for
202 of 204.

**Decision:** keep. The fallback is reliable.

## 6. No target at all

**What it checks:** rows with neither a closed reason nor a WON/LOSS value.

**Result:** 0.

## 7. `union_won` vs the report's WON/LOSS column

**What it checks:** whether the label from the closed reason agrees with the WON/LOSS
column printed in the same report.

**Result:** 10,824 won/won and 4,469 lost/lost agree. The conflicts are:

- **55 rows with both WON and LOSS filled in.** Some reports have separate "Cert of Rep (Win)"
  and "Cert of Results (Loss)" columns, and these rows have both, so the report contradicts
  itself.
- **9 rows (FY2012–13) where the closed reason says win but the WON/LOSS column says LOSS.**

These turned out to be two different problems.

### 7a. Wrong closed reason in the report (33 rows): fix the label

**Rule:** the label comes from the closed reason. It is overruled only when the docket's
certification (the live case record) disagrees **and** at least one other field on the same
report row (the tally majority or the WON/LOSS column) agrees with the docket. The report's
closed reason is then contradicted both by the live record and by another field in the report
itself.

For 31-RC-140133, the docket has a Certification of Representative issued 12/16/2014, the
same date the report gives as the closing date. So the document actually issued that day was
a win certification.

33 rows meet the rule: 27 of the "both" rows and 6 of the FY2012–13 rows. 19 become wins and
14 become losses. None of them is a multi-unit case (7b).

Verified on nlrb.gov:

| case | tally | report's closed reason | docket | nlrb.gov |
|---|---|---|---|---|
| 31-RC-140133 | 32–15 | Results (loss) | Representative | one unit, closed Representative |
| 19-RC-150769 | 28–45 | Representative (win) | Results | closed Results |

The FY2012–13 rows look like this; the WON/LOSS column, the docket and (mostly) the tally all
say loss:

| case | tally | closed reason | WON/LOSS | docket |
|---|---|---|---|---|
| 05-RC-068340 | 3–7 | Representative | LOSS | Results |
| 05-RC-071347 | 40–58 | Representative | LOSS | Results |
| 32-RC-076341 | 11–15 | Representative | LOSS | Results |
| 01-RC-061968 | 6–14 | Representative | LOSS | Results |
| 31-RC-087528 | 34–33 | Representative | LOSS | Results |

**Decision:** **set `union_won` to the docket's value** for these 33. The other 9 rows where
the docket disagrees with the label have no second source backing the docket, so the label
stays.

### 7b. Multi-unit cases (19 rows): drop

This is different from multi-**union**. Step 4 removed elections with more than one **union**
on the ballot. A **unit** is a group of employees who vote separately, e.g. professionals and
non-professionals at the same hospital, or two separate facilities. One case can hold
several unit votes, each on a ballot with the same single union. They're all single-union, so
step 4 keeps them.

The report gives the closing (and the WON/LOSS flags) per **case**, then repeats it on every
unit's row. If one unit won and another lost, the losing unit's row can still say
"Certific. of Representative".

Example: **22-RC-153083** (`FY2015/june-2015.pdf`), Teamsters Local 282 on both ballots.
One unit voted 5–3 for the union, the other 4–12 against. Both rows say "Certific. of
Representative".

A case counts as multi-unit if the reports list it with different counts on the same election
date, or the docket has **both** a Certification of Representative and a Certification of
Results. That's 90 rows. 18 of them contradict their own tally; the label probably belongs to
another unit.

One more "both" row, **32-RC-297724** (3–11 tally, closed Representative, no docket), can't be
settled either way.

**Decision:** **drop these 19.** Keep the other 72 multi-unit rows, where the case-level label
and the unit's own tally agree.

Note on the "both" rows: I first read all 34 "both" rows that contradict their tally as
multi-unit. Only 6 have any evidence of a second unit. The other 27 are the wrong-closed-reason
rows in 7a, and 1 is unresolved.

## 8. `union_won` vs the vote majority

**What it checks:** whether the label agrees with the tally (votes for > votes against;
a tie is a loss).

**Result:** 240 disagree: 170 "lost with a majority for" and 70 "won without a majority". 34 of
these are the "both" rows covered in check 7. The other 206 (a handful of them also get
relabelled by the 7a rule):

### Lost even though the tally shows a majority for (150 rows)

- The docket has a *Certification of Results* for **135**, confirming the loss. Only 1 has a
  win certification.
- The pattern points to **votes for and against entered in the wrong fields**: 69 have 0 votes
  against, and in 34 every eligible voter voted "for".
- It's concentrated by NLRB office, which suggests a data-entry habit rather than random error:

  | region | mismatches | share of the region's losses |
  |---|---|---|
  | 20 (San Francisco) | 46 | 28% |
  | 27 (Denver) | 34 | 23% |
  | 07 (Detroit) | 17 | 8% |
  | every other region | ≤ 9 | mostly 1–2% |

- Example: **20-RC-361553** shows 15 eligible, 10 for, 0 against, closed as a loss. The PDF,
  the NLRB xlsx and the nlrb.gov case page all say the same thing, so this is in the NLRB's
  records, not our extraction.

### Won even though the tally shows no majority (56 rows)

The docket confirms the win for 44. By type:

| type | rows | docket: won | likely cause |
|---|---|---|---|
| 0–0 tally | 22 | 20 | tally never entered |
| fewer for than against | 21 | 12 | counts wrong or swapped, or a later revised tally |
| tie | 7 | 6 | challenged ballots opened after the initial tally broke the tie |
| counts missing | 6 | 6 | not entered |

**Decision:** **keep all 206.** The label is right; the vote counts are what's wrong. The vote
counts are already dropped before modeling, so they can't affect the model. This
check is also the main reason **not** to use the vote counts as a target or to fix labels.

## 9. More votes than eligible voters

**What it checks:** votes for + votes against > `eligible`.

**Result:** 146 rows.

- **71** have `eligible` = 0, meaning eligible wasn't entered (see check 11).
- **75** have a nonzero `eligible`:
  - 21 are over by less than 10% (e.g. 28 votes, 27 eligible): small noise.
  - 34 are over by 10–50%.
  - 20 are over by more than 50%, which is clearly a typo.

The typos are in different fields, and are in the PDFs themselves:

- **05-RC-246684**: 11 eligible, **1954** votes for. The nlrb.gov page says only 1 ballot was
  counted, so the vote field is wrong.
- **07-RC-146302**: 97 eligible, **97** for, 64 against. The nlrb.gov page says 84 ballots
  counted, so the "for" field copies `eligible`.
- **01-RC-075540**: **2** eligible, 28–29 votes. The source PDF prints 2, but nlrb.gov
  now says 58, so here `eligible` is the wrong field.

**Decision:** **keep.** Without looking up each case you can't tell which field is wrong. Only
`eligible` is a feature, and at most ~75 rows (0.5%) are affected. Optional: set
`eligible` to missing for the 20 rows over by more than 50%.

## 10. No valid votes (0–0)

**What it checks:** votes for and against both 0.

**Result:** 48 rows, 42 of them with eligible voters > 0. The docket shows a certification in
both directions (21 lost, 21 won), so these elections did happen. The tally just wasn't
entered.

**Decision:** **keep.** The label comes from the closed reason, not the tally.

## 11. Eligible = 0 or missing

**What it checks:** `eligible`, a model feature, is 0 or blank.

**Result:** 85 rows (77 zero, 8 blank), spread across all years.

**Why 0 means "not recorded":** an election can't have 0 eligible voters, and 71 of the 77 zero
rows have votes cast (up to 231). The 0 is printed in the PDF, but the NLRB's own record uses
it the same way: the nlrb.gov page for **12-RC-197840** shows 0 eligible voters next to 231
ballots counted.

**Decision:** **set `eligible` = 0 to missing** and keep the rows.

- **Not keep 0:** the model would read it as a real value, the smallest possible unit, when the
  unit size is actually unknown.
- **Not drop:** the label and every other feature on these rows are fine.
- **Not fill it here:** filling from the votes cast would use post-vote information. A
  median or similar fill should be fitted on the training data only, in the modeling pipeline,
  so the test set doesn't leak into it.

## 12. Election date after the case closed

**What it checks:** an election can't happen after its case closes.

**Result:** 3 rows, already set to missing:

| case | election date | closed | docket tally date |
|---|---|---|---|
| 29-RC-012045 | 2012-01-18 | 2011-12-20 | none |
| 22-RC-081724 | 2012-09-13 | 2012-09-06 | 2012-06-30 |
| 09-RC-286639 | 2022-08-31 | 2022-08-26 | none |

**Decision:** already handled. They're included in check 13, which fills 22-RC-081724.

## 13. No election date

**What it checks:** rows with no election date. The date is needed for the time-based split,
for the pre-vote docket features and for `days_petition_to_election`.

**Result:** 123 rows (the 3 above plus 120 where the PDF cell is blank). They cluster
in a few reports (9 in `FY2020/july-2020-pdf.pdf`). They all have vote counts, so the
election happened.

**Investigation:** the docket has a *Tally of Ballots* document for 97 of them. The tally date
is a good stand-in for the election date: across all elections with one tally, the median gap
is 0 days and 74% are within a week (mail-ballot elections are counted later).

**Decision:** **fill the election date from the earliest Tally of Ballots** (97 rows) and
**drop the remaining 26.** Those can't be placed in time or given docket features.

## 14. Closed more than a year after the vote

**What it checks:** cases where closing took over a year (median 533 days, max 4,075).

**Result:** 185 rows. Their dockets are full of litigation: Board decisions (178 documents),
objections (171), requests for review (148), hearing officer reports, and "Answer to
Complaint". That last one usually means the employer refused to bargain to test the
certification in court. These are real contested cases, not errors. Their win rate is lower
(63.8% vs 70.8% overall).

**Decision:** **keep.** `union_won` is the final outcome after all the litigation, which is
what we want. The delay itself is only known after the vote, so it's not a feature.

## 15. States with no census region

**What it checks:** a `state` value missing from the census region mapping.

**Result:** 0.

## 16. No participants / no docket

**What it checks:** elections whose case wasn't found in the scraped participants or docket
files.

**Result:** the same 4 cases are missing from both: 19-RC-067978, 02-RC-069853, 34-RC-084177,
24-RC-089605 (all 2011–2012).

**Decision:** **keep.** Their participant and docket features stay missing.

## 17. Petition dated after the vote

**What it checks:** `days_petition_to_election` < 0.

**Result:** 4 rows, already set to missing. In all four, the docket's "Signed RC Petition"
entry is dated after the Tally of Ballots. For example, 04-RC-211814 has its tally on 01/19/2018
and its petition on 03/26/2018. The docket date is when the document was uploaded, not when the
petition was filed.

**Decision:** already handled.

## 18. Very long petition → election gaps

**What it checks:** not a printed check, but `days_petition_to_election` has a long tail
(median 31 days, max 3,444).

**Result:** 113 elections took more than a year. That's up from 43 before the full docket
re-scrape: these long, contested cases are exactly the ones whose petition used to be cut off.
Most were litigated before the vote. 59% had a hearing (vs 9% overall), 32% a request for
review (vs 2%), and 12% a blocking unfair-labor-practice charge (vs 1%). Examples:

- **13-RC-069379**: petition 2011, election 2021.
- **15-RC-008394**: petition 2002, election 2011, an old case from before the 2011 numbering.

A rerun election in the same case is also measured from the original petition (12-RC-073852:
petition 2012, second election 2019).

**Decision:** **keep.** They're real. If the model is sensitive to outliers (e.g. linear
models), handle it in feature engineering (cap or log), not by dropping rows.

## 19. No petition date

**What it checks:** elections with no petition date, so `days_petition_to_election` is missing.
The script prints how many petition dates it found and where each came from.

**Result:** 14,605 of 15,312 have one: 14,453 from the docket, 152 from the report's file date
(early reports only). Where both exist, they're the same day 86% of the time and within 3 days
99%.

The other **707** all have a docket, and it's complete (none hit the old 10-document limit),
but it never lists a petition. These dockets are short (median 4 documents), and the earliest
is usually the Stipulated Election Agreement, a median 19 days before the vote. The NLRB's
public docket doesn't list every document, so re-scraping can't recover these. They're spread
across all years.

**Decision:** **keep, with `petition_date` and `days_petition_to_election` missing.** Don't
substitute the earliest document's date. It's a different, later event, and would make
these elections look faster than they were. Handle the missing values in the modeling
pipeline.

---

## Takeaways for the report

- **The label is trustworthy, the vote counts aren't.** `union_won` (from the closed reason)
  agrees with the docket certification for 14,236 of the 14,278 elections that have exactly one
  kind of certification (99.7%). 33 of the 42 disagreements are wrong closed reasons that the
  rule in 7a fixes. The vote counts, and sometimes `eligible`, have typos in the NLRB's own
  records. Some are confirmed on nlrb.gov, and some are clustered in particular regional
  offices. That supports labelling from the case closing rather than the tally, and not using
  vote counts at all.
- **Two real label problems, both small.** 33 rows have a wrong closed reason in the report;
  two other sources agree on the correct one, so it's fixed (7a). 19 rows are units in
  multi-unit cases whose case-level label contradicts the unit's own vote; there's no reliable
  way to tell which unit the label belongs to, so they're dropped (7b).
- **Everything else is kept.** Long, contested cases look odd but are real, and dropping them
  would bias the sample toward easy elections.
