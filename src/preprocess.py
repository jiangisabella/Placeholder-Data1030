"""
Cleans the raw election CSVs from src/pdfs_to_csv.py and merges them into
one table, one row per election. Only RC cases (a union asking to be
certified) are kept, see CASE_TYPES.

    python src/preprocess.py

Reads data/raw_csvs/<FY>/*.csv and writes data/elections_clean.csv.
Each fiscal year is loaded and tidied on its own (load_year), then the years
are stacked and the steps that need all years at once (duplicates, target,
features) run on the merged table.

The cells (# %%) can also be run one at a time in VS Code.
"""

# %%
from pathlib import Path

import numpy as np
import pandas as pd

CSV_DIR = Path("data/raw_csvs")
PARTICIPANTS_CSV = Path("data/case_participants_raw.csv")   # from src/docket_scraping.py
DOCKET_CSV = Path("data/docket_activity.csv")
OUT_CSV = Path("data/elections_clean.csv")

# RC = workers/union petition to have a union certified (the question we
# model). RD = workers vote to decertify an existing union, RM = employer
# asks for the vote; both mean something different by a "win", so they're
# left out. UD = vote on a union-security clause, not a union election
CASE_TYPES = ["RC"]

# reports that only repeat rows from other files:
#  - the FY2013/FY2014 annual reports repeat that year's monthly rows
#  - FY2012/october2012 is the same PDF as FY2013/october2012 (it's an FY2013 report)
DROP_FILES = {
    "FY2013/fy2013-election-report.pdf",
    "FY2014/fy-2014.pdf",
    "FY2012/october2012.pdf",
}

# every header spelling seen in the PDFs -> one name. typos are the PDFs' own
RENAME = {
    "Region": "region_offices",
    "Case Number": "case_number",
    "Case ID": "case_id",
    "Case Link": "case_id",
    "Case Name": "case_name",
    "Case Type": "case_type_reported",
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
    "Votes for Labor Org 2": "votes_for_2",
    "Labor Org 2 Name": "union_name_2",
    "Votes for Labor Org 3": "votes_for_3",
    "Votes For Labor Org3": "votes_for_3",
    "Labor Org 3 Name": "union_name_3",
    "Labor Org3": "union_name_3",
    "Stipulated / Consent / Directed": "method",
    "Stiplulated / Consent / Directed": "method",
    "Union (Win / Loss)": "reported_win_loss",
    "Union (Won / Loss)": "reported_win_loss",
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
    "For Proposition": "for_proposition",
    "For Propostion": "for_proposition",
    "Against Proposition": "against_proposition",
}
# FY2012 summary columns that got repeated on each row, and an empty
# spacer column in two 2025/2026 reports
DROP_COLUMNS = ["Cert Rep Win Count", "% Won by Union", "Unnamed: 20", "extraction_method"]

NUMBER_COLS = ["eligible", "votes_against", "votes_for", "votes_for_2", "votes_for_3",
               "for_proposition", "against_proposition"]
DATE_COLS = ["file_date", "election_date", "closed_date"]
NAME_COLS = ["case_name", "union_name", "union_name_2", "union_name_3", "union_to_certify"]


# %%
# ---------- loading one fiscal year ----------

def clean_text(s):
    # undo the PDF quirks found when checking against the xlsx files:
    # wrapped cells get a space after a hyphen ("AFL- CIO"), quotes are
    # sometimes curly, and an en dash can come out as "?"
    s = s.fillna("").astype(str)
    s = s.str.replace("’", "'").str.replace("‘", "'")
    s = s.str.replace("“", '"').str.replace("”", '"')
    s = s.str.replace("–", "-").str.replace("­", "")
    s = s.str.replace(r"(\w)- (\w)", r"\1-\2", regex=True)
    s = s.str.replace(r"(\w) -(\w)", r"\1-\2", regex=True)
    return s.str.split().str.join(" ")


def to_number(s):
    return pd.to_numeric(s.str.replace(",", ""), errors="coerce")


def to_date(s):
    # some years write 4/1/2026, others 04/01/2026. %m and %d take both
    return pd.to_datetime(s, format="%m/%d/%Y", errors="coerce")


def load_year(fy_dir):
    files = sorted(fy_dir.glob("*.csv"))
    frames = []
    for f in files:
        # everything as strings first so "01" stays "01"
        df = pd.read_csv(f, dtype=str, keep_default_na=False)
        if df["source_pdf"].iloc[0] in DROP_FILES:
            continue
        df = df.drop(columns=[c for c in DROP_COLUMNS if c in df.columns])
        unknown = set(df.columns) - set(RENAME) - {"source_pdf", "page"}
        if unknown:
            raise ValueError(f"{f}: no rename for {sorted(unknown)}")
        frames.append(df.rename(columns=RENAME))

    df = pd.concat(frames, ignore_index=True)
    # not every year has every column (only FY2012 has a file date, etc)
    for col in set(RENAME.values()) - set(df.columns):
        df[col] = ""

    df["fiscal_year"] = int(fy_dir.name[2:])
    for col in df.columns.drop(["page", "fiscal_year"]):
        df[col] = clean_text(df[col])
    for col in NUMBER_COLS:
        bad = (df[col] != "") & to_number(df[col]).isna()
        if bad.any():
            print(f"  {fy_dir.name}: {bad.sum()} non-numeric values in {col}: {df.loc[bad, col].unique()[:5]}")
        df[col] = to_number(df[col])
    for col in DATE_COLS:
        bad = (df[col] != "") & to_date(df[col]).isna()
        if bad.any():
            print(f"  {fy_dir.name}: {bad.sum()} unreadable dates in {col}: {df.loc[bad, col].unique()[:5]}")
        df[col] = to_date(df[col])

    print(f"{fy_dir.name}: {len(frames)} reports, {len(df)} rows")
    return df


# %%
years = [load_year(d) for d in sorted(CSV_DIR.glob("FY*"))]
raw = pd.concat(years, ignore_index=True)
print(f"\nmerged: {raw.shape}")


# %%
# ---------- case type, region, keeping RC ----------

df = raw.copy()
df["case_type"] = df["case_number"].str[3:5]
df["region"] = df["case_number"].str[:2]
mismatch = df["case_type"] != df["case_type_reported"]
print(f"case type column disagrees with case number: {mismatch.sum()}")

print(f"case types: {df['case_type'].value_counts().to_dict()}, keeping {CASE_TYPES}")
df = df[df["case_type"].isin(CASE_TYPES)].copy()


# %%
# ---------- duplicates ----------
# the same election can show up twice: a case listed in two monthly reports,
# or in both the 1-union and 2-union tables. a case can also have several
# real rows (separate voting groups, same day, different counts), so match
# on the votes too, not just the case number

VOTE_KEY = ["case_number", "election_date", "eligible", "votes_for", "votes_against"]

# n_unions = how many unions were on the ballot (1, 2 or 3), used in the
# multi-union cell below to keep only 1-union elections. it has to be
# counted here, before repeats are dropped: an election listed in both the
# 1-union and 2-union tables could keep its 1-union copy and pass as a
# 1-union election. taking the max over every row with the same case and
# election date makes every copy say 2. this goes by case + date, not the
# full VOTE_KEY, because a few multi-union elections also have a 1-union row
# with different counts (a runoff, or one union's votes split out, e.g.
# 29-RC-216339, 05-RC-323238). those aren't clean 1-union elections either
df["n_unions"] = (df[["union_name", "union_name_2", "union_name_3"]] != "").sum(axis=1)
df["n_unions"] = df.groupby(["case_number", "election_date"], dropna=False)["n_unions"].transform("max")

# most repeats are reports that overlap (Sep/Oct 2022, Dec 2018/Jan 2019),
# but a few cases were reopened and closed again with a different outcome
# (29-RC-198745: Results in Jun 2017, Representative in Aug 2017). keep the
# latest closing, preferring a copy that has a closed reason
df["has_reason"] = df["closed_reason"] != ""
df = df.sort_values(["has_reason", "closed_date"], na_position="first")
dup = df.duplicated(VOTE_KEY, keep="last")
same_file = df.duplicated(VOTE_KEY + ["source_pdf"], keep="last")
print(f"exact repeats dropped: {dup.sum()} ({same_file.sum()} within one report, "
      f"{(dup & ~same_file).sum()} across reports)")
df = df[~dup].copy()

# one vote filed under two case types, e.g. an RC and an RM (Georgia Power,
# Aug 2026) or an RC and an RD: same place, date and counts, different case
# number. only counts if the case types differ, since separate units at one
# employer can have identical counts. keep the first. (with only RC kept
# this finds nothing, the RM/RD copy is already gone)
SAME_VOTE = ["state", "city", "election_date", "eligible", "votes_for", "votes_against", "union_name"]
n_types = df.groupby(SAME_VOTE)["case_type"].transform("nunique")
twin = (n_types > 1) & df["election_date"].notna()
print(f"same vote filed under two case types: {twin.sum()} rows")
df = df[~(twin & df.duplicated(SAME_VOTE, keep="first"))].copy()


# %%
# ---------- multi-union elections: keep 1-union only ----------
# elections with 2 or 3 unions on the ballot are dropped. votes_for only
# counts union 1 there, so the win/loss and vote share mean something
# different. from here on every row is a single-union election

print(f"\nunions on the ballot: {df['n_unions'].value_counts().sort_index().to_dict()}")
print(f"dropping {(df['n_unions'] > 1).sum()} elections with 2+ unions, keeping {(df['n_unions'] == 1).sum()}")
df = df[df["n_unions"] == 1].copy()


# %%
# ---------- target ----------
# union_won = 1 if the case closed with a Certification of Representative,
# 0 if Certification of Results. (if RD cases are added back, 1 there
# means the union stayed.) two FY2012 reports (Nov 2011, Mar 2012) have no closed reason column, so
# those fall back to the report's own WON/LOSS column

reason = df["closed_reason"].str.lower()
df["union_won"] = np.select(
    [reason.str.contains("representative"), reason.str.contains("results")],
    [1, 0], default=-1)

won_col = df["reported_win_loss"].eq("WON") | df["reported_win"].eq("WON")
lost_col = df["reported_win_loss"].eq("LOSS") | df["reported_loss"].eq("LOSS")
df["reported_result"] = np.select(
    [won_col & lost_col, won_col, lost_col], ["both", "won", "lost"], default="")
fallback = (df["union_won"] == -1) & df["reported_result"].isin(["won", "lost"])
df.loc[fallback, "union_won"] = (df.loc[fallback, "reported_result"] == "won").astype(int)
print(f"target taken from WON/LOSS column instead of closed reason: {fallback.sum()}")

print(f"no target at all (dropped): {(df['union_won'] == -1).sum()}")
df = df[df["union_won"] != -1].copy()

# sanity check against the vote counts (tie = loss)
df["majority_for"] = (df["votes_for"] > df["votes_against"]).astype(int)
print("\nunion_won vs majority of votes:")
print(pd.crosstab(df["union_won"], df["majority_for"]))
print("\nunion_won vs the report's WON/LOSS columns:")
print(pd.crosstab(df["union_won"], df["reported_result"]))


# %%
# ---------- vote checks ----------

df["total_votes"] = df["votes_for"] + df["votes_against"]
df["vote_share"] = np.where(df["total_votes"] > 0, df["votes_for"] / df["total_votes"].replace(0, np.nan), np.nan)
df["turnout"] = np.where(df["eligible"] > 0, df["total_votes"] / df["eligible"].replace(0, np.nan), np.nan)
# the PDFs really do have some of these (e.g. 2 eligible, 57 votes in Apr 2012)
df["flag_votes_gt_eligible"] = df["total_votes"] > df["eligible"]
df["flag_no_votes"] = df["total_votes"] == 0
print(f"\nmore votes than eligible voters: {df['flag_votes_gt_eligible'].sum()}")
print(f"no valid votes: {df['flag_no_votes'].sum()}")
print(f"eligible == 0 or missing: {((df['eligible'] == 0) | df['eligible'].isna()).sum()}")


# %%
# ---------- features ----------

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

df["log_eligible"] = np.log1p(df["eligible"])

# parent union by keyword. first match wins, so the order matters
# (Workers United is SEIU-affiliated but kept on its own)
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

# employer name for grouping (GroupKFold): lowercase, nothing after d/b/a,
# no punctuation, no inc/llc/corp
SUFFIXES = r"\b(inc|incorporated|llc|l l c|corp|corporation|co|company|ltd|lp|llp|pc|pllc|the)\b"
emp = df["case_name"].str.lower()
emp = emp.str.split(r"\s+(?:d/b/a|dba|d\.b\.a\.|t/a)\s+", regex=True).str[0]
emp = emp.str.replace(r"[^a-z0-9 ]", " ", regex=True).str.replace(SUFFIXES, " ", regex=True)
df["employer"] = emp.str.split().str.join(" ")

# an election can't happen after its case closed. these are typos in the
# PDF (17-RD-076109 has 05/08/2102), so treat them as missing
bad = df["election_date"] > df["closed_date"]
print(f"\nelection date after the case closed (set to missing): {bad.sum()} "
      f"{df.loc[bad, 'election_date'].dt.date.tolist()}")
df.loc[bad, "election_date"] = pd.NaT

df["election_year"] = df["election_date"].dt.year
df["election_month"] = df["election_date"].dt.month
# only FY2012 reports have a file date
df["days_file_to_election"] = (df["election_date"] - df["file_date"]).dt.days
df["days_election_to_close"] = (df["closed_date"] - df["election_date"]).dt.days
df["flag_closed_years_later"] = df["days_election_to_close"] > 365
print(f"\nno election date: {df['election_date'].isna().sum()}")
print(f"closed more than a year after the vote: {df['flag_closed_years_later'].sum()}")


# %%
# ---------- case participants (one row per case) ----------
# each row of participant_raw_text is one party, split over lines:
#   party \n type \n name \n organization
#   e.g. "Employer\nLegal Representative\nBLOOM, HOWARD\nJackson Lewis, P.C."
# some rows are shorter ("Employer\nEmployer\nDELTA PROJECTS, INC.")
# the case pages show who is on the case now, so a lawyer added late (for
# objections, say) shows up too. mostly these are known before the vote

parts = pd.read_csv(PARTICIPANTS_CSV, dtype=str).drop_duplicates()
lines = parts["participant_raw_text"].fillna("").str.split("\n")
n_lines = lines.str.len()
parts["party"] = lines.str[0]
# 2-line rows are either "Petitioner\nIndividual" or a party plus a name
PTYPES = ["Legal Representative", "Union", "Employer", "Additional Service",
          "Individual", "Notification", "Union as an Employer"]
parts["ptype"] = lines.str[1].where(lines.str[1].isin(PTYPES), "")
parts["org"] = np.where(n_lines >= 4, lines.str[3], "").astype(str)

# big management-side labor law firms (starting list, add more)
MGMT_FIRMS = (r"jackson lewis|littler|ogletree|seyfarth|fisher (?:&|and )?phillips|"
              r"morgan,? lewis|proskauer|constangy|ford ?harrison|epstein becker|"
              r"baker ?hostetler|barnes (?:&|and) thornburg|polsinelli")

is_lawyer = parts["ptype"] == "Legal Representative"
emp_lawyer = is_lawyer & (parts["party"] == "Employer")
pet_lawyer = is_lawyer & (parts["party"] == "Petitioner")
parts["emp_lawyer"] = emp_lawyer
parts["pet_lawyer"] = pet_lawyer
parts["emp_mgmt_firm"] = emp_lawyer & parts["org"].str.lower().str.contains(MGMT_FIRMS, regex=True)
parts["intervenor"] = parts["party"] == "Intervenor"
# (an "Individual" petitioner is the same as case_type == RD, so no column for it)
parts["emp_firm"] = parts["org"].where(emp_lawyer, "")

by_case = parts.groupby("case_number")
case_parts = pd.DataFrame({
    "n_participants": by_case.size(),
    "n_employer_lawyers": by_case["emp_lawyer"].sum(),
    "n_petitioner_lawyers": by_case["pet_lawyer"].sum(),
    "employer_mgmt_firm": by_case["emp_mgmt_firm"].any().astype(int),
    "has_intervenor": by_case["intervenor"].any().astype(int),
    # the firm most of the employer's lawyers come from ("" = no lawyer listed)
    "employer_law_firm": by_case["emp_firm"].agg(lambda s: s[s != ""].mode().iloc[0] if (s != "").any() else ""),
}).reset_index()
case_parts["employer_has_lawyer"] = (case_parts["n_employer_lawyers"] > 0).astype(int)
case_parts["petitioner_has_lawyer"] = (case_parts["n_petitioner_lawyers"] > 0).astype(int)

df = df.merge(case_parts, on="case_number", how="left", validate="many_to_one")
print(f"\nparticipants found for {df['n_participants'].notna().sum()} of {len(df)} elections")
print(df[["employer_has_lawyer", "employer_mgmt_firm", "petitioner_has_lawyer",
          "has_intervenor"]].mean().round(3).to_dict())


# %%
# ---------- docket activity (one row per case + election date) ----------
# the documents filed in each case. only what was filed BEFORE the vote can
# be a feature; tallies, certifications and objections come after it.
# grouped by case + election date since a rerun election keeps the same
# case number
#
# heads up: the scraper only read the first page of each docket, which is
# the 10 newest documents. long (usually contested) cases lose their oldest
# documents, often the petition. docket_truncated marks those

docket = pd.read_csv(DOCKET_CSV, dtype=str)
# count before dropping repeats: a first page of 10 can hold the same document twice
n_docs = docket.groupby("case_number").size()
truncated = n_docs[n_docs >= 10].index
docket = docket.drop_duplicates()
docket["document"] = docket["document"].str.rstrip("*").str.strip()
docket["activity_date"] = to_date(docket["activity_date"])

# petition date = earliest signed petition (amended or not)
is_petition = docket["document"].str.match(r"Signed (?:Amended )?R[CDM] Petition")
petition_date = docket[is_petition].groupby("case_number")["activity_date"].min().rename("petition_date")

# match each document to the elections in its case, then split before/after
elections = df[["case_number", "election_date"]].drop_duplicates().dropna()
docs = docket.merge(elections, on="case_number")
before = docs[docs["activity_date"] < docs["election_date"]].copy()
after = docs[docs["activity_date"] >= docs["election_date"]].copy()

doc = before["document"].str.lower()
before["hearing"] = doc.str.contains(r"hearing officer|post-hearing brief|pre-hearing brief|decision and direction")
before["postponed"] = doc.str.contains(r"reschedule hearing|postpone")
before["blocking"] = doc.str.contains(r"ulp|blocking|request to proceed")
before["review"] = doc.str.contains(r"request for review|rfr ")
before["by_employer"] = before["issued_filed_by"] == "Employer"
doc = after["document"].str.lower()
after["objections"] = doc.str.contains(r"objections to an election")
after["board"] = after["issued_filed_by"] == "NLRB - Board"

KEY = ["case_number", "election_date"]
pre = before.groupby(KEY).agg(
    n_docs_before_vote=("document", "size"),
    pre_hearing=("hearing", "any"),
    pre_postponed=("postponed", "any"),
    pre_ulp_blocking=("blocking", "any"),
    pre_request_review=("review", "any"),
    n_employer_filings_before_vote=("by_employer", "sum"),
)
post = after.groupby(KEY).agg(
    objections_filed=("objections", "any"),
    board_involved_after_vote=("board", "any"),
)
case_docket = elections.merge(pre.reset_index(), on=KEY, how="left").merge(post.reset_index(), on=KEY, how="left")
for col in case_docket.columns.drop(KEY):
    case_docket[col] = case_docket[col].fillna(0).astype(int)

df = df.merge(case_docket, on=KEY, how="left", validate="many_to_one")
df = df.merge(petition_date, on="case_number", how="left")
df["has_docket"] = df["case_number"].isin(n_docs.index).astype(int)
df["docket_truncated"] = df["case_number"].isin(truncated).astype(int)
# fall back to the report's own file date (FY2012 only) when no petition was found
df["petition_date"] = df["petition_date"].fillna(df["file_date"])
df["days_petition_to_election"] = (df["election_date"] - df["petition_date"]).dt.days
bad = df["days_petition_to_election"] < 0
print(f"\npetition after the vote (set to missing): {bad.sum()}")
df.loc[bad, "days_petition_to_election"] = np.nan

print(f"docket found for {df['has_docket'].sum()} of {len(df)} elections, "
      f"{df['docket_truncated'].sum()} cut off at 10 documents")
print(f"petition date found: {df['petition_date'].notna().sum()} "
      f"(missing in {df.loc[df['docket_truncated'] == 1, 'petition_date'].isna().sum()} of the cut-off cases)")
print(df["days_petition_to_election"].describe().round(1).to_dict())


# %%
# ---------- save ----------

KEEP = [
    # ids / bookkeeping
    "source_pdf", "fiscal_year", "case_number", "case_type", "region",
    # known before the vote
    "case_name", "employer", "city", "state", "census_region",
    "union_name", "parent_union", "method", "eligible", "log_eligible",
    "file_date", "election_date", "election_year", "election_month", "days_file_to_election",
    # known before the vote: participants
    "n_participants", "employer_has_lawyer", "n_employer_lawyers", "employer_mgmt_firm",
    "employer_law_firm", "petitioner_has_lawyer", "n_petitioner_lawyers",
    "has_intervenor",
    # known before the vote: docket
    "petition_date", "days_petition_to_election", "n_docs_before_vote", "pre_hearing",
    "pre_postponed", "pre_ulp_blocking", "pre_request_review", "n_employer_filings_before_vote",
    # only known after the vote (drop these before modeling)
    "votes_for", "votes_against", "total_votes", "vote_share", "turnout", "majority_for",
    "closed_date", "closed_reason", "days_election_to_close", "reported_result", "union_to_certify",
    "objections_filed", "board_involved_after_vote",
    # checks
    "flag_votes_gt_eligible", "flag_no_votes", "flag_closed_years_later",
    "has_docket", "docket_truncated",
    # target
    "union_won",
]
clean = df[KEEP].sort_values(["election_date", "case_number"]).reset_index(drop=True)
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
- Before modeling, drop everything listed under "only known after the vote"
  above, plus the bookkeeping columns.
- Split by election date (test on the latest years), GroupKFold by employer
  inside the training years. Anything like a union's past win rate can only
  use earlier elections.
- Look at the rows flagged above (votes > eligible, closed years later,
  union_won disagreeing with the vote majority) and decide drop vs keep.
- Docket: src/docket_scraping.py only reads the first page (10 newest
  documents). ~1,800 elections are cut off, and since the newest documents
  are the post-vote ones (objections, Board decisions), the cut-off cases
  are the contested ones and lose their pre-vote documents and petition
  date. Re-scraping all docket pages would fix it. Until then don't use
  docket_truncated as a feature (it partly reflects what happened after
  the vote), and treat the pre-vote docket counts as lower bounds.
- employer_law_firm has spelling variants ("Jackson Lewis P.C." vs
  "Jackson Lewis, P.C.", "Ogletree Deakins"); normalize before using it.
'''
