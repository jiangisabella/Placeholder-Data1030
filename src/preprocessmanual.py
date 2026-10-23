"""
Cleans the raw election CSVs from src/pdfs_to_csv.py and merges them into
one table, one row per election.

Only RC cases (a union asking to be certified) are kept.
Only Single Union cases are kept.

    python src/preprocessmanual.py

Reads data/raw_csvs/<FY>/*.csv and writes data/elections_clean.csv.
Each fiscal year is loaded and tidied on its own (load_year), then the years
are stacked and the steps that need all years at once (duplicates, target,
features) run on the merged table.

"""

# %%
from pathlib import Path

import numpy as np
import pandas as pd

CSV_DIR = Path("data/raw_csvs")
PARTICIPANTS_CSV = Path("data/case_participants_raw.csv")   # from src/docket_scraping.py
DOCKET_CSV = Path("data/docket_activity_merged.csv")   # from src/docket_scraping.py, full dockets
OUT_CSV = Path("data/elections_clean.csv")

"""
reports that only repeat rows from other files:
 - the FY2013/FY2014 annual reports repeat that year's monthly rows
 - FY2012/october2012 is the same PDF as FY2013/october2012 (it's an FY2013 report)
"""
DROP_FILES = {
    "FY2013/fy2013-election-report.pdf",
    "FY2014/fy-2014.pdf",
    "FY2012/october2012.pdf",
}

#Standardizing the header names
HEADERS = {
    "Region": "region_offices",
    "Case Number": "case_number",
    "Case ID": "case_id",
    "Case Link": "case_id",
    "Case Name": "case_name",
    "Case Type": "case_type",
    "Case File Date": "file_date",
    "City": "city",
    "Dispute Unit City": "city",
    "State": "state",
    "Dispute Unit State": "state",
    "Election Held Date": "election_date",
    "Number of Eligible Voters": "eligible",
    "Num Eligible Voters": "eligible",
    "Valid Votes Against": "votes_against",
    "Votes for Labor Org 1": "votes_for",
    "Labor Org 1 Name": "union_name",
    "Labor Org 2 Name": "union_name_2",
    "Labor Org 3 Name": "union_name_3",
    "Labor Org3": "union_name_3",
    "Union (Win / Loss)": "reported_win",
    "Union (Won / Loss)": "reported_win",
    "Cert of Rep (Win)": "reported_win",
    "Certification of Representative Date (Win)": "reported_win",
    "Cerification of Representative Date (Win)": "reported_win",
    "Cert of Results (Loss)": "reported_loss",
    "Certification of Results Date (Loss)": "reported_loss",
    "Certificaiton of Results Date (Loss)": "reported_loss",
    "Union To Certify": "union_to_certify",
    "Case Closed Date": "closed_date",
    "Closed Date": "closed_date",
    "Closed Reason": "closed_reason",
}

"""
columns dropped as soon as a report is read:
 - FY2012 summary columns that got repeated on each row
 - an empty spacer column in two 2025/2026 reports
 - the proposition votes, only filled in for UD (deauthorization) cases
 - votes for unions 2 and 3, multi-union elections are dropped anyway
 - election method (stipulated / consent / directed), not used
 - source_pdf, only needed to skip the DROP_FILES reports
"""
DROP_COLUMNS = ["Cert Rep Win Count", "% Won by Union", "Unnamed: 20", "extraction_method",
                "For Proposition", "For Propostion", "Against Proposition",
                "Votes for Labor Org 2", "Votes for Labor Org 3", "Votes For Labor Org3",
                "Stipulated / Consent / Directed", "Stiplulated / Consent / Directed",
                "source_pdf"]

NUMBER_COLS = ["eligible", "votes_against", "votes_for"]
DATE_COLS = ["file_date", "election_date", "closed_date"]



#Loading one year
def clean_text(s):
    #nans
    s = s.fillna("").astype(str)
    #quotatiuon and emdashes
    s = s.str.replace("’", "'").str.replace("‘", "'")
    s = s.str.replace("“", '"').str.replace("”", '"')
    s = s.str.replace("–", "-").str.replace("­", "")
    #removing extra spacing
    s = s.str.replace(r"(\w)- (\w)", r"\1-\2", regex=True)
    s = s.str.replace(r"(\w) -(\w)", r"\1-\2", regex=True)
    return s.str.split().str.join(" ")


def load_year(fy_dir):
    frames = []
    for f in sorted(fy_dir.glob("*.csv")):
        #reading as string is best so 0 isn't ignored in beginning of case numbers
        df = pd.read_csv(f, dtype=str, keep_default_na=False)
        if df["source_pdf"].iloc[0] in DROP_FILES:
            continue
        df = df.drop(columns=DROP_COLUMNS, errors="ignore")
        #stop if a report has a header we haven't seen before
        unknown = set(df.columns) - set(HEADERS) - {"page"}
        if unknown:
            raise ValueError(f"{f}: no rename for {sorted(unknown)}")
        frames.append(df.rename(columns=HEADERS))

    #merge all frames
    df = pd.concat(frames, ignore_index=True)
    #Adding missing columns as blank
    for col in HEADERS.values():
        if col not in df.columns:
            df[col] = ""
    #fiscal year column added
    df["fiscal_year"] = int(fy_dir.name[2:])
    for col in df.columns.drop(["page", "fiscal_year"]):
        df[col] = clean_text(df[col])
    #numbers like 1,234 -> 1234, blanks become NaN
    for col in NUMBER_COLS:
        df[col] = pd.to_numeric(df[col].str.replace(",", ""), errors="coerce")
    #some years write 4/1/2026, others 04/01/2026. %m and %d take both
    for col in DATE_COLS:
        df[col] = pd.to_datetime(df[col], format="%m/%d/%Y", errors="coerce")

    print(f"{fy_dir.name}: {len(frames)} reports, {len(df)} rows")
    return df

years = [load_year(d) for d in sorted(CSV_DIR.glob("FY*"))]
raw = pd.concat(years, ignore_index=True)
print(f"\nmerged: {raw.shape}")
# %%

#Keeping RC type cases only

df = raw.copy()

# RC = workers/union petition to have a union certified
print(f"case types: {df['case_type'].value_counts().to_dict()}, keeping RC")
df = df[df["case_type"] == "RC"].copy()

"""
Dropping columns:
- region_offices (NLRB office codes, not used)
- case id, internal data, unusable
- columns known only after vote ends like union_to_certify
"""
df = df.drop(columns=["region_offices", "case_id", "page", "union_to_certify"])


# %%
"""
Deleting Duplicates:
-duplicates exist if:
-a case listed in two monthly reports
-The case exists in both the 1-union and 2-union tables. 
-a case can also have several real rows (separate voting groups, same day, different counts), so match
on the votes too
"""

VOTE_KEY = ["case_number", "election_date", "eligible", "votes_for", "votes_against"]

"""
Since we want single unions only, will count for the numbner of unions
n_unions = how many unions were on the ballot (1-3), used in the
multi-union cell below to keep only 1-union elections. 
this goes by case + date, not the
full VOTE_KEY, because a few multi-union elections also have a 1-union row
with different counts (a runoff, or one union's votes split out, e.g.
29-RC-216339, 05-RC-323238). 
"""
df["n_unions"] = (df[["union_name", "union_name_2", "union_name_3"]] != "").sum(axis=1)
df["n_unions"] = df.groupby(["case_number", "election_date"], dropna=False)["n_unions"].transform("max")

"""
Cases can have different votes on an election date. 
n_units = how many different votes (eligible + counts) one case has on one
election date, across every report. 
"""
units = df[["case_number", "election_date", "eligible", "votes_for", "votes_against"]].drop_duplicates()
n_units = units.groupby(["case_number", "election_date"]).size().rename("n_units")
df = df.merge(n_units, on=["case_number", "election_date"], how="left")

"""
Repeats occur because of overlapping months.
(Sep/Oct 2022, Dec 2018/Jan 2019)

For cases that are reopened/closed, keeping the latest final legal outcome.
Or the copy that has the closed reason/outcome specified.
"""
df["has_reason"] = df["closed_reason"] != ""
df = df.sort_values(["has_reason", "closed_date"], na_position="first")
dup = df.duplicated(VOTE_KEY, keep="last")
print(f"exact repeats dropped: {dup.sum()}")
df = df[~dup].copy()


# %%
"""
Dropping multi-union elections.
Elections with 2 or 3 unions on the ballot are dropped. 
votes_for only counts union 1 there, so the win/loss and vote share mean something
different.
Dropping the related columns afterward.
"""

print(f"\nunions on the ballot: {df['n_unions'].value_counts().sort_index().to_dict()}")
print(f"dropping {(df['n_unions'] > 1).sum()} elections with 2+ unions, keeping {(df['n_unions'] == 1).sum()}")
df = df[df["n_unions"] == 1].drop(columns=["n_unions", "has_reason", "union_name_2", "union_name_3"])


# %%
"""
Loading the docket activity data.
The docket is the list of documents filed in each case, scraped from the
case pages on nlrb.gov (src/docket_scraping.py). One row per document.

Columns:
- case_number: joins to the election rows
- activity_date: when the document was filed or issued
- document: what it is (e.g. "Tally of Ballots", "Certification of Results")
- issued_filed_by: who filed it (Employer, Petitioner, NLRB - GC, ...)

Cleaning:
- exact duplicate rows dropped
- the "*" nlrb.gov puts after some document names is stripped.
- activity_date turned into a datetime

Needed to cover missing data. It has the certification, and date of first tally.
Further down it also gives the petition date.
"""

docket = pd.read_csv(DOCKET_CSV, dtype=str)
docket = docket.drop_duplicates()
docket["document"] = docket["document"].str.rstrip("*").str.strip()
docket["activity_date"] = pd.to_datetime(docket["activity_date"], format="%m/%d/%Y", errors="coerce")

"""
Collecting data related to the target variable, union_won
the certification the case got and its first tally of ballots.
(usually the election close date. ballots are counted when election is closed.) 
Used to fill in for missing election dates from the monthly data.
docket_cert: 
1 = Representative,
0 = Results,
-1 = neither or both
"""
doc = docket["document"].fillna("")

# case numbers that have each kind of certification in their docket
rep_cases = set(docket.loc[doc.str.contains("Certification of Representative"), "case_number"])
res_cases = set(docket.loc[doc.str.contains("Certification of Results"), "case_number"])
both_cases = rep_cases & res_cases

# case number -> 1 or 0. cases with both or neither are left out (-1 later)
docket_cert = {}
for case in rep_cases - res_cases:
    docket_cert[case] = 1
for case in res_cases - rep_cases:
    docket_cert[case] = 0

# date of the first Tally of Ballots in each case
tallies = docket[doc.str.contains("Tally of Ballots")]
first_tally = tallies.groupby("case_number")["activity_date"].min()


# %%
"""
One column for target variable: union_won
union_won = 1 if the case closed with a Certification of Representative,
0 if Certification of Results. 
two FY2012 reports (Nov 2011, Mar 2012) have no closed reason column, so those fall back to the report's own WON/LOSS column
"""

#union_won from closed_reason 
reason = df["closed_reason"].str.lower()
is_rep = reason.str.contains("representative")
is_res = reason.str.contains("results")

df["union_won"] = -1                  # -1 = unknown for now
df.loc[is_res, "union_won"] = 0       # Certification of Results = union lost
df.loc[is_rep, "union_won"] = 1       # Certification of Representative = union won

#union_won from won/loss columns
# FY2012 / early FY2013 reports have one column with WON or LOSS (renamed to
# reported_win), later reports put WON in reported_win and LOSS in reported_loss
won_col = df["reported_win"] == "WON"
lost_col = (df["reported_win"] == "LOSS") | (df["reported_loss"] == "LOSS")

df["reported_result"] = ""
df.loc[lost_col, "reported_result"] = "lost"
df.loc[won_col, "reported_result"] = "won"
df.loc[won_col & lost_col, "reported_result"] = "both"

#if closed reason is missing, using the result from the won/loss cols
no_reason = df["union_won"] == -1
fill_won = no_reason & (df["reported_result"] == "won")
fill_lost = no_reason & (df["reported_result"] == "lost")
df.loc[fill_won, "union_won"] = 1
df.loc[fill_lost, "union_won"] = 0
print(f"target taken from WON/LOSS column instead of closed reason: {fill_won.sum() + fill_lost.sum()}")

#dropping rows with missing data
no_target = df["union_won"] == -1
print(f"no target at all (dropped): {no_target.sum()}")
df = df[~no_target].copy()

print("\nunion_won vs the report's WON/LOSS columns:")
print(pd.crosstab(df["union_won"], df["reported_result"]))

"""
checking the target against the docket.
the report's own tally and WON/LOSS column are the second opinion.
all three are put on the same 1 / 0 scale as union_won:
- cert: the docket's certification (-1 = none or both)
- majority_for: 1 if the union got more votes (a tie is a loss)
- reported: the WON/LOSS column (NaN if it's blank or both)
"""
cert = df["case_number"].map(docket_cert).fillna(-1)
majority_for = (df["votes_for"] > df["votes_against"]).astype(int)
reported = df["reported_result"].map({"won": 1, "lost": 0})

"""
multi-unit cases: separate groups of employees voting the same day, each
on a ballot with the same single union. the report repeats the case's
outcome on every unit's row, so a unit that lost can say won
(22-RC-153083: 5-3 and 4-12, both "Representative"). spotted by different
counts for one case + date, or a docket with both kinds of certification
"""
multi_unit = (df["n_units"] > 1) | df["case_number"].isin(both_cases)

"""
Tracking rows where report label is wrong and docket label is more accurate.
the report's closed reason is wrong when the docket disagrees and the
tally or WON/LOSS column on the same row agrees with the docket
(31-RC-140133: 32-15 vote, report says Results, docket has a
Certification of Representative on the closing date)
"""
has_cert = cert != -1
docket_disagrees = cert != df["union_won"]
votes_agree_with_docket = (majority_for == cert)
report_agrees_with_docket = (reported == cert)

fix = has_cert & docket_disagrees & (votes_agree_with_docket | report_agrees_with_docket) & ~multi_unit

"""
a unit whose label contradicts its own tally can't be sorted out, and
neither can a row marked both WON and LOSS that the docket doesn't settle.
"""
marked_both = df["reported_result"] == "both"
label_disagrees_with_votes = (df["union_won"] != majority_for)

unclear = (multi_unit | marked_both) & label_disagrees_with_votes & ~fix

# apply the docket fix
fix_to_won = fix & (cert == 1)
fix_to_lost = fix & (cert == 0)
df.loc[fix_to_won, "union_won"] = 1
df.loc[fix_to_lost, "union_won"] = 0
print(f"\nlabel corrected from the docket: {fix.sum()} "
      f"({fix_to_won.sum()} to won, {fix_to_lost.sum()} to lost)")

# drop the unclear rows, and the helper columns that are no longer needed
print(f"multi-unit or both WON and LOSS, label contradicts its own tally (dropped): {unclear.sum()}")
df = df[~unclear].copy()
df = df.drop(columns=["reported_win", "reported_loss", "reported_result", "n_units"])


# %%
"""
Sanity checks for votes
"""

# sanity check against the vote counts (tie = loss)
majority_for = (df["votes_for"] > df["votes_against"]).astype(int)
print("\nunion_won vs majority of votes:")
print(pd.crosstab(df["union_won"], majority_for, colnames=["majority_for"]))

"""
Checking if theres more votes than eligible voters
"""
total_votes = df["votes_for"] + df["votes_against"]
too_many_votes = (total_votes > df["eligible"])
no_votes = (total_votes == 0)
no_eligible = (df["eligible"] == 0) | df["eligible"].isna()
print(f"\nmore votes than eligible voters: {too_many_votes.sum()}")
print(f"no valid votes: {no_votes.sum()}")
print(f"eligible == 0 or missing: {no_eligible.sum()}")

"""
if eligible votes = 0, treated as missing data.
same if there are more votes than eligible voters, the eligible count
is wrong, and eligible is kept as a feature
"""
df.loc[df["eligible"] == 0, "eligible"] = np.nan
df.loc[too_many_votes, "eligible"] = np.nan

"""
an election can't happen after its case closed. these are typos in the
PDF (one has 05/08/2102), so treat them as missing dates, corrected by docket data
"""
after_close = df["election_date"] > df["closed_date"]
bad_dates = df.loc[after_close, "election_date"].dt.date.tolist()
print(f"\nelection date after the case closed (set to missing): {after_close.sum()} {bad_dates}")
df.loc[after_close, "election_date"] = pd.NaT

"""
missing election dates (mostly blank in the PDF): use the first Tally of
Ballots in the docket, which for most elections is the day of the vote
(median 0 days). rows with no tally can't be placed in time, so dropped
"""
no_date = df["election_date"].isna()
tally = df["case_number"].map(first_tally)
fill = no_date & tally.notna()
df.loc[fill, "election_date"] = tally[fill]

still_no_date = df["election_date"].isna()
print(f"no election date: {no_date.sum()}, filled from the docket tally: {fill.sum()}, "
      f"no tally (dropped): {still_no_date.sum()}")
df = df[~still_no_date].copy()

"""
the vote counts and how the case closed come after the vote, and
union_won already holds what we need from them
"""
df = df.drop(columns=["votes_for", "votes_against", "closed_date", "closed_reason"])


""" 
Adding new features 
- census_region: state is mapped to regions
- parent_union: union name is matched to its parent
- employer : case_name  cleaned
"""

CENSUS_REGION = {}
for region, states in {
    "Northeast": "CT ME MA NH RI VT NJ NY PA",
    "Midwest": "IL IN MI OH WI IA KS MN MO NE ND SD",
    "South": "DE DC FL GA MD NC SC VA WV AL KY MS TN AR LA OK TX",
    "West": "AZ CO ID MT NV NM UT WY AK CA HI OR WA",
    "Territory": "PR GU VI AS MP",
}.items():
    for st in states.split():
        CENSUS_REGION[st] = region

df["state"] = df["state"].str.upper()
df["census_region"] = df["state"].map(CENSUS_REGION).fillna("Unknown")
print(f"\nstates with no census region: {df.loc[df['census_region'] == 'Unknown', 'state'].value_counts().to_dict()}")

"""
parent union by keyword.
(Workers United is SEIU-affiliated but kept on its own)
"""
PARENT_UNION = [
    ("Workers United", r"workers united"),
    ("Teamsters", r"teamster|\bibt\b"),
    ("SEIU", r"\bseiu\b|service employees|1199"),
    ("UFCW", r"\bufcw\b|food (?:and|&) commercial"),
    ("IBEW", r"\bibew\b|electrical workers"),
    ("IAM", r"machinists|\biam\b"),
    ("USW", r"steelworkers|steel, paper|\busw\b"),
    ("UAW", r"\buaw\b|automobile"),
    ("CWA", r"communications? workers|\bcwa\b"),
    ("IUOE", r"operating engineers|\biuoe\b"),
    ("LIUNA", r"laborers|\bliuna\b"),
    ("AFSCME", r"afscme|state,? county"),
    ("SPFPA", r"spfpa|security,? police"),
    ("UNITE HERE", r"unite here"),
    ("AFT", r"\baft\b|federation of teachers"),
    ("Nurses", r"nurse"),
    ("Carpenters", r"carpenters"),
    ("IUPAT", r"painters"),
]
union_lower = df["union_name"].str.lower()
df["parent_union"] = "Other"
for name, pattern in reversed(PARENT_UNION):   # reversed so the first match is written last
    df.loc[union_lower.str.contains(pattern, regex=True), "parent_union"] = name
print(f"\n{df['parent_union'].value_counts().to_dict()}")

"""
employer name for grouping: 
lowercase, nothing after d/b/a, no punctuation, no inc/llc/corp
"""
SUFFIXES = r"\b(inc|incorporated|llc|l l c|corp|corporation|co|company|ltd|lp|llp|pc|pllc|the)\b"
emp = df["case_name"].str.lower()
emp = emp.str.split(r"\s+(?:d/b/a|dba|d\.b\.a\.|t/a)\s+", regex=True).str[0]
emp = emp.str.replace(r"[^a-z0-9 ]", " ", regex=True).str.replace(SUFFIXES, " ", regex=True)
df["employer"] = emp.str.split().str.join(" ")


# %%
"""
Case participants data.
The people and organizations listed on each case, scraped from the case
pages on nlrb.gov (src/docket_scraping.py). One row per participant.

Columns:
- case_number: joins to the election rows
- participant_raw_text: everything about the participant in one string,
  split over lines:
    party \\n type \\n name \\n organization
    e.g. "Employer\\nLegal Representative\\nBLOOM, HOWARD\\nJackson Lewis, P.C."
  some rows are shorter ("Employer\\nEmployer\\nDELTA PROJECTS, INC.")

Cleaning:
- exact duplicate rows dropped
- participant_raw_text split into its lines:
  - party: which side they're on (Employer, Petitioner, Intervenor, ...)
  - ptype: their role (Legal Representative, Union, Employer, ...)
  - org: their organization (4th line), e.g. the lawyer's law firm

Making (one row per case, then merged onto the elections):
- n_participants: how many participants the case has
- n_employer_lawyers / employer_has_lawyer: lawyers on the employer's side
- n_petitioner_lawyers / petitioner_has_lawyer: lawyers on the union's side
- employer_law_firms: every firm the employer's lawyers come from,
  separated by "; " (split it back into a list with .str.split("; "))
- has_intervenor: 1 if another party (e.g. a second union) joined the case

"""

parts = pd.read_csv(PARTICIPANTS_CSV, dtype=str).drop_duplicates()

# split the raw text into its lines (a missing line becomes "")
lines = parts["participant_raw_text"].fillna("").str.split("\n")
parts["party"] = lines.str[0]
parts["ptype"] = lines.str[1].fillna("")
parts["org"] = lines.str[3].fillna("")

# flags for each participant row
is_lawyer = parts["ptype"] == "Legal Representative"
parts["emp_lawyer"] = is_lawyer & (parts["party"] == "Employer")
parts["pet_lawyer"] = is_lawyer & (parts["party"] == "Petitioner")
parts["intervenor"] = parts["party"] == "Intervenor"

# the employer lawyer's firm, blank for everyone else
parts["emp_firm"] = ""
parts.loc[parts["emp_lawyer"], "emp_firm"] = parts["org"]

def list_firms(firms):
    # every firm the employer's lawyers come from, joined with "; " since
    # firm names have commas in them ("" = no lawyer listed)
    firms = sorted(set(firms[firms != ""]))
    return "; ".join(firms)


# one row per case
by_case = parts.groupby("case_number")
case_parts = pd.DataFrame()
case_parts["n_participants"] = by_case.size()
case_parts["n_employer_lawyers"] = by_case["emp_lawyer"].sum()
case_parts["n_petitioner_lawyers"] = by_case["pet_lawyer"].sum()
case_parts["has_intervenor"] = by_case["intervenor"].any().astype(int)
case_parts["employer_law_firms"] = by_case["emp_firm"].apply(list_firms)
case_parts["employer_has_lawyer"] = (case_parts["n_employer_lawyers"] > 0).astype(int)
case_parts["petitioner_has_lawyer"] = (case_parts["n_petitioner_lawyers"] > 0).astype(int)
case_parts = case_parts.reset_index()

# add the case features onto every election of that case
df = df.merge(case_parts, on="case_number", how="left", validate="many_to_one")
print(f"\nparticipants found for {df['n_participants'].notna().sum()} of {len(df)} elections")
print(df[["employer_has_lawyer", "petitioner_has_lawyer",
          "has_intervenor"]].mean().round(3).to_dict())



"""
Petition date from the docket.
The only docket feature for now.

Steps:
- petition date: the earliest signed petition (amended or not) in each case
- if the docket has no petition, use the report's own file date instead
- days_petition_to_election: how many days from the petition to the vote
  (a petition after the vote is a bad date, set to missing)
"""

# petition date = earliest signed petition (amended or not)
is_petition = docket["document"].str.match(r"Signed (?:Amended )?R[CDM] Petition")
petitions = docket[is_petition]
petition_date = petitions.groupby("case_number")["activity_date"].min()
petition_date = petition_date.rename("petition_date")

# add the petition date onto the elections
df = df.merge(petition_date, on="case_number", how="left")

# no petition in the docket -> use the report's own file date (early reports only)
petition_from_docket = df["petition_date"].notna()
df["petition_date"] = df["petition_date"].fillna(df["file_date"])
petition_from_report = df["petition_date"].notna() & ~petition_from_docket

# days from petition to election. a petition after the vote is a bad date
df["days_petition_to_election"] = (df["election_date"] - df["petition_date"]).dt.days
petition_after_vote = df["days_petition_to_election"] < 0
print(f"\npetition after the vote (set to missing): {petition_after_vote.sum()}")
df.loc[petition_after_vote, "days_petition_to_election"] = np.nan

print(f"petition date found: {df['petition_date'].notna().sum()} "
      f"(from the docket: {petition_from_docket.sum()}, from the report's file date: {petition_from_report.sum()})")
print(df["days_petition_to_election"].describe().round(1).to_dict())



"""
column order for the output. everything here is known before the vote
except the target 
"""
KEEP = [
    # ids / bookkeeping
    "fiscal_year", "case_number", "case_type",
    # from the reports
    "case_name", "employer", "city", "state", "census_region",
    "union_name", "parent_union", "eligible", "file_date", "election_date",
    # participants
    "n_participants", "employer_has_lawyer", "n_employer_lawyers",
    "employer_law_firms", "petitioner_has_lawyer", "n_petitioner_lawyers",
    "has_intervenor",
    # docket
    "petition_date", "days_petition_to_election",
    # target
    "union_won",
]
# make sure no column was forgotten or left over
assert set(KEEP) == set(df.columns), set(KEEP) ^ set(df.columns)

clean = df[KEEP]
clean = clean.sort_values(["election_date", "case_number"])
clean = clean.reset_index(drop=True)
clean.to_csv(OUT_CSV, index=False)
print(f"\nsaved {clean.shape} to {OUT_CSV}")
print(clean["union_won"].value_counts(normalize=True).round(3).to_dict())
print(clean.groupby("fiscal_year")["union_won"].agg(["size", "mean"]).round(3))


r'''
----------------------------------------------------------------------
TODO (still to do)
----------------------------------------------------------------------
- Missing months: Feb-Sep 2023 and Apr 2019 are image-only PDFs and were
  skipped (data/skipped_pdfs.txt); Oct 2025 had no elections. Reports go by
  the month a case CLOSED, so elections held then still show up later.
  Mention the gap in the report and expect a dip in plots over time.
- Several reports hold fewer rows than their own summary (Jun 2012,
  Sep 2020, most of FY2021, Dec 2021). The data just isn't in the PDF.
- Before modeling, drop the bookkeeping columns.
- Split by election date (test on the latest years), GroupKFold by employer
  inside the training years. Anything like a union's past win rate can only
  use earlier elections.
- The rows the checks flag were looked into in data/data_checks.md, and the
  fixes from there are applied above. Any new fix that needs the vote counts
  has to go before the vote checks cell drops them.
- employer_law_firms has spelling variants ("Jackson Lewis P.C." vs
  "Jackson Lewis, P.C.", "Ogletree Deakins"); normalize before using it.
'''
