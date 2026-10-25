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

"""
AI use: Claude (Anthropic) was used as a reference while writing this file.
We wrote the cleaning plan ourselves and asked for feedback on its order
(e.g. which columns leak the outcome), then asked about specific pandas
steps along the way:
- looping over the FY folders, clean_text, to_numeric/to_datetime
- n_unions with groupby().transform, and dropping repeated report rows
- the docket: stripping "*", certifications, first tally date
- sanity checks on eligible voters and election dates
- the census region dictionary, parent union patterns, employer name regex
- splitting participant text into lines and per-case lawyer/firm features
- the petition date regex and filling it from file_date
Sections that used it are marked "Reference: (Anthropic, 2026)".

Anthropic. (2026). Claude [Large language model].
    https://claude.ai/share/e3a3320e-15c4-4346-b9f7-b104c5ce86f3
"""

# %%
from pathlib import Path

import numpy as np
import pandas as pd

CSV_DIR = Path("data/raw_csvs")
PARTICIPANTS_CSV = Path("data/raw_csvs/scraper_csvs/case_participants_raw.csv")   # from src/docket_scraping.py
DOCKET_CSV = Path("data/raw_csvs/scraper_csvs/docket_activity.csv")   # from src/docket_scraping.py, full dockets
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



"""
Reference: (Anthropic, 2026). Looping over the FY folders with glob, reading
as str, clean_text on a whole Series, and errors="coerce" for numbers/dates.
"""
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
        #skip files
        if df["source_pdf"].iloc[0] in DROP_FILES:
            continue
        #drop unneeded columns
        df = df.drop(columns=DROP_COLUMNS, errors="ignore")
        #merge the df with the standardized headers
        frames.append(df.rename(columns=HEADERS))

    #merge all moth dataframes to one
    df = pd.concat(frames, ignore_index=True)
    #Adding missing columns as blank
    for col in HEADERS.values():
        if col not in df.columns:
            df[col] = ""
    #fiscal year column added
    df["fiscal_year"] = int(fy_dir.name[2:])
    for col in df.columns.drop(["page", "fiscal_year"]):
        df[col] = clean_text(df[col])
    #number cleaning
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
df = df[df["case_type"] == "RC"].copy()

"""
Dropping columns:
- region_offices (NLRB office codes, not used)
- case id, internal data, unusable
- columns known only after vote ends
- case_type, every row is RC now
"""
df = df.drop(columns=["region_offices", "case_id", "page", "union_to_certify", "case_type"])


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
n_unions = how many unions were on the ballot (1-3)
this goes by case + date, primary key
Reference: (Anthropic, 2026). groupby().transform("max") to copy n_unions
onto every row of the election.
"""
df["n_unions"] = (df[["union_name", "union_name_2", "union_name_3"]] != "").sum(axis=1)
df["n_unions"] = df.groupby(["case_number", "election_date"], dropna=False)["n_unions"].transform("max")

"""
Cases can have different votes on an election date. 
n_units = how many different votes (eligible + counts) one case has on one
election date, across every report. 
"""
units = df[VOTE_KEY].drop_duplicates()
n_units = units.groupby(["case_number", "election_date"]).size().rename("n_units")
df = df.merge(n_units, on=["case_number", "election_date"], how="left")

"""
Repeats occur because of overlapping months.
(Sep/Oct 2022, Dec 2018/Jan 2019)

For cases that are reopened/closed, keeping the latest final legal outcome.
Or the copy that has the closed reason/outcome specified.
Reference: (Anthropic, 2026). Sorting on has_reason and closed_date so
duplicated(keep="last") keeps the closed, latest copy.
"""
df["has_reason"] = (df["closed_reason"] != "")
df = df.sort_values(["has_reason", "closed_date"], na_position="first")
is_duplicate = df.duplicated(VOTE_KEY, keep="last")
print(f"exact repeats dropped: {is_duplicate.sum()}")
df = df[~is_duplicate].copy()


# %%
"""
Dropping multi-union elections, and extra columns.
Elections with 2 or 3 unions on the ballot are dropped. 
votes_for only counts union 1 there, so the win/loss and vote share mean something
different.
"""

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
Reference: (Anthropic, 2026). .str.rstrip("*") for the document names.
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
Reference: (Anthropic, 2026). Sets of case numbers for each certification,
and groupby().min() on a datetime column for the first tally.
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
# early reports have one column with WON or LOSS.
# later reports put WON in reported_win and LOSS in reported_loss
won_col = df["reported_win"] == "WON"
lost_col = (df["reported_win"] == "LOSS") | (df["reported_loss"] == "LOSS")

df["reported_result"] = ""
df.loc[lost_col, "reported_result"] = "lost"
df.loc[won_col, "reported_result"] = "won"
df.loc[won_col & lost_col, "reported_result"] = "both"

#if closed reason is missing, using the result from the won/loss cols
no_reason = (df["union_won"] == -1)
fill_won = no_reason & (df["reported_result"] == "won")
fill_lost = no_reason & (df["reported_result"] == "lost")
df.loc[fill_won, "union_won"] = 1
df.loc[fill_lost, "union_won"] = 0
print(f"target taken from WON/LOSS column instead, because missing closed reason: {fill_won.sum() + fill_lost.sum()}")

#dropping rows with missing data
no_target = df["union_won"] == -1
print(f"no target at all (dropped): {no_target.sum()}")
df = df[~no_target].copy()

print("\nunion_won vs the report's WON/LOSS columns:")
print(pd.crosstab(df["union_won"], df["reported_result"]))

"""
checking the target against the docket and the vote count.
when they disagree we can't always tell which one is right, so the row is
dropped instead of guessing.
- cert: the docket's certification (-1 = none or both)
- majority_for: 1 if the union got more votes (a tie is a loss)
"""
cert = df["case_number"].map(docket_cert).fillna(-1)
majority_for = (df["votes_for"] > df["votes_against"]).astype(int)

# the docket has one kind of certification and it isn't the label
docket_disagrees = (cert != -1) & (cert != df["union_won"])

"""
multi-unit cases: separate groups of employees voting the same day, each
on a ballot with the same single union.  spotted by different
counts for one case + date, or a docket with both kinds of certification.
rows marked both WON and LOSS are just as unclear.
"""
multi_unit = (df["n_units"] > 1) | df["case_number"].isin(both_cases)
marked_both = df["reported_result"] == "both"
tally_disagrees = (multi_unit | marked_both) & (df["union_won"] != majority_for)

drop = docket_disagrees | tally_disagrees
print(f"\ndocket disagrees with the label (dropped): {docket_disagrees.sum()}")
print(f"multi-unit or both WON and LOSS, label contradicts its own tally (dropped): {tally_disagrees.sum()}")
print(f"total dropped: {drop.sum()}")
df = df[~drop].copy()
df = df.drop(columns=["reported_win", "reported_loss", "reported_result", "n_units"])


# %%
"""
Sanity checks for votes
Reference: (Anthropic, 2026). Feedback on these checks (a tie is a loss,
eligible 0 or below the vote count -> missing) and filling bad election
dates from the docket tally with .map(first_tally).
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
print(f"\nelection date after the case closed (set to missing): {after_close.sum()}")
print(df.loc[after_close, ["case_number", "election_date", "closed_date"]])
df.loc[after_close, "election_date"] = pd.NaT

"""
missing election dates, use the first Tally of Ballots in the docket, which for most elections is the day of the vote
(median 0 days). 
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
Reference: (Anthropic, 2026). The CENSUS_REGIONS dictionary, the starting
PARENT_UNION keyword list, and the employer name regex (ALIAS_SPLIT,
SUFFIXES). We edited these afterwards.
"""

CENSUS_REGIONS = {
    "Northeast": ["CT", "ME", "MA", "NH", "RI", "VT", "NJ", "NY", "PA"],
    "Midwest":   ["IL", "IN", "MI", "OH", "WI", "IA", "KS", "MN", "MO", "NE", "ND", "SD"],
    "South":     ["DE", "DC", "FL", "GA", "MD", "NC", "SC", "VA", "WV",
                  "AL", "KY", "MS", "TN", "AR", "LA", "OK", "TX"],
    "West":      ["AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY",
                  "AK", "CA", "HI", "OR", "WA"],
    "Territory": ["PR", "GU", "VI", "AS", "MP"],
}

# flip it to state -> region so .map can look up each row's state
STATE_TO_REGION = {}
for region, states in CENSUS_REGIONS.items():
    for st in states:
        STATE_TO_REGION[st] = region

df["state"] = df["state"].str.upper()
df["census_region"] = df["state"].map(STATE_TO_REGION).fillna("Unknown")
print(f"\nstates with no census region: {df.loc[df['census_region'] == 'Unknown', 'state'].value_counts().to_dict()}")

"""
parent union by keyword. the first pattern that matches wins, so the order matters
(e.g. CWA before Workers United, IAM before SMART for joint locals).
(Workers United is SEIU-affiliated and RWDSU is UFCW-affiliated, but both are kept on their own)
only parents with about 50+ elections get their own group, smaller ones stay "Other"
"""
PARENT_UNION = [
    ("CWA", r"communications? workers|\bcwa\b|news ?guild|newspaper guild"),
    ("Workers United", r"workers united"),
    ("SEIU", r"service employees|seiu|1199"),
    ("RWDSU", r"retail,? wholesale|\brwdsu\b"),
    ("UFCW", r"food (?:and|&) commercial|\bufcw\b"),
    ("IAM", r"machinists|\biam(?:aw)?\b"),
    # industrial / transport
    ("Teamsters", r"teamster|\bibt\b|chauffeurs|warehousemen"),
    ("USW", r"steel ?workers|united steel|\busw\b"),
    ("UAW", r"automobile|\buaw\b"),
    ("IBEW", r"electrical workers|\bibew\b"),
    ("IUOE", r"operating engineers|stationary engineers|\biuoe\b"),
    ("BCTGM", r"bakery|confectionery|grain millers|\bbctgm\b"),
    ("ATU", r"amalgamated transit|\batu\b"),
    ("TWU", r"transport workers"),
    ("UWUA", r"utility workers|utilities workers|\buwua\b"),
    ("ILWU", r"longshore (?:and|&) warehouse|\bilwu\b"),
    # building trades
    ("LIUNA", r"laborers|\bliuna\b"),
    ("Carpenters", r"carpenters"),
    ("UA (Plumbers)", r"plumbers|pipefitters|steamfitters|sprinkler fitters|united association|\bua\b"),
    ("Ironworkers", r"iron ?workers|bridge,? structural"),
    ("SMART", r"sheet metal|\bsmart\b"),
    ("IUPAT", r"painters|\biupat\b"),
    # public sector / professional / health
    ("AFSCME", r"state,? county|\bafscme\b"),
    ("AFT", r"federation of teachers|\baft\b"),
    ("Nurses", r"nurse"),
    ("OPEIU", r"office (?:and|&) professional|\bopeiu\b"),
    ("IAFF", r"fire ?fighters|\biaff\b"),
    # hospitality / entertainment
    ("UNITE HERE", r"unite here|hotel employees"),
    ("IATSE", r"theatrical stage|\biatse\b"),
    # security / guards
    ("SPFPA", r"security,? police|\bspfpa\b"),
    ("UGSOA", r"government security officers|\bugsoa\b"),
    ("NATCA", r"air traffic controllers|\bnatca\b"),
]
union_lower = df["union_name"].str.lower()
df["parent_union"] = "Other"
for name, pattern in reversed(PARENT_UNION):   # reversed so the first match is written last
    df.loc[union_lower.str.contains(pattern, regex=True), "parent_union"] = name
# "** Data Incomplete **" is a placeholder in the PDF, not a union
df.loc[union_lower.str.contains("data incomplete"), "parent_union"] = "Unknown"
print(f"\n{df['parent_union'].value_counts().to_dict()}")

"""
employer name for grouping:
- lowercase
- nothing after d/b/a, a/k/a, f/k/a, "as agent of", "on behalf of"
  (Hyatt as agent of Airport Plaza Hotel -> hyatt)
- no notes in brackets: "(Joint Employer)", "(PAE)"
- & -> and, no punctuation
- no inc/llc/corp/u.s. at the end, no "the" at the start
"""
ALIAS_SPLIT = (r"\s+(?:d/?b/?a|d\.b\.a\.?|a/?k/?a|f/?k/?a|t/a|doing business as|formerly known as"
               r"|as agent (?:of|for)|on behalf of)\b")
SUFFIXES = (r"inc|incorporated|corp|corporation|co|company|companies|llc|l l c|llp|l l p|lp|l p"
            r"|ltd|limited|pllc|pc|p c|plc|na|n a|usa|us|u s")
TRAILING_SUFFIX = rf"(?:\s+(?:{SUFFIXES}))+$"   # one or more suffixes at the end

emp = df["case_name"].fillna("").str.lower()
emp = emp.str.split(ALIAS_SPLIT, n=1, regex=True).str[0]
emp = emp.str.replace(r"\([^)]*\)", " ", regex=True)
emp = emp.str.replace("&", " and ").str.replace("'", "")    # mcdonald's -> mcdonalds
emp = emp.str.replace(r"[^a-z0-9 ]", " ", regex=True)
emp = emp.str.split().str.join(" ")
emp = emp.str.replace(TRAILING_SUFFIX, "", regex=True)
emp = emp.str.replace(r"^the ", "", regex=True)
df["employer"] = emp
print(f"\nemployers: {df['employer'].nunique()} distinct, {(df['employer'] == '').sum()} blank")


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

Reference: (Anthropic, 2026). Splitting the raw text into lines with .str,
and grouping by case for the counts and the joined firm list (list_firms).
"""

participants = pd.read_csv(PARTICIPANTS_CSV, dtype=str).drop_duplicates()

# split the raw text into its lines (a missing line becomes "")
lines = participants["participant_raw_text"].fillna("").str.split("\n")
participants["party"] = lines.str[0]
participants["ptype"] = lines.str[1].fillna("")
participants["org"] = lines.str[3].fillna("")

# flags for each participant row
is_lawyer = participants["ptype"] == "Legal Representative"
participants["emp_lawyer"] = is_lawyer & (participants["party"] == "Employer")
participants["pet_lawyer"] = is_lawyer & (participants["party"] == "Petitioner")
participants["intervenor"] = participants["party"] == "Intervenor"

# the employer lawyer's firm, blank for everyone else
participants["emp_firm"] = ""
participants.loc[participants["emp_lawyer"], "emp_firm"] = participants["org"]

def list_firms(firms):
    # every firm the employer's lawyers come from, joined with "; " since
    # firm names have commas in them ("" = no lawyer listed)
    firms = sorted(set(firms[firms != ""]))
    return "; ".join(firms)


# one row per case
by_case = participants.groupby("case_number")
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
Reference: (Anthropic, 2026). The signed petition regex and
fillna(df["file_date"]) to fill from another column.
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
# file_date is folded into petition_date, not needed on its own
df = df.drop(columns=["file_date"])

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
    "fiscal_year", "case_number",
    # from the reports
    "case_name", "employer", "city", "state", "census_region",
    "union_name", "parent_union", "eligible", "election_date",
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
missing = set(KEEP) - set(df.columns)     # in KEEP but not in df
extra = set(df.columns) - set(KEEP)       # in df but not in KEEP
if missing or extra:
    raise ValueError(f"KEEP doesn't match the columns. missing: {missing}, extra: {extra}")

clean = df[KEEP]
clean = clean.sort_values(["election_date", "case_number"])
clean = clean.reset_index(drop=True)
clean.to_csv(OUT_CSV, index=False)
print(f"\nsaved {clean.shape} to {OUT_CSV}")
print(clean["union_won"].value_counts(normalize=True).round(3).to_dict())
print(clean.groupby("fiscal_year")["union_won"].agg(["size", "mean"]).round(3))
