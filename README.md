# Predicting NLRB Union Election Outcomes

DATA1030 Fall 2026 final project (Placeholder).
Team: Yanan Jiang, Sima Raha, Cunyan Ma, Niharika Adari

## Overview

This project predicts whether a union wins a National Labor Relations Board (NLRB)
representation election, using information available before the vote. We built the
dataset ourselves by extracting the NLRB's monthly election reports (FY2012–FY2026) from
PDFs and scraping each case's docket and participant list from nlrb.gov, giving 15,270
single-union elections. Organizing campaigns take months of effort, so understanding which
factors relate to winning could help union organizers decide where to focus their
resources, and help researchers study how factors like employer legal representation or
campaign length shape outcomes. This repository contains the code to collect, clean, and
explore the data; modeling will be added later in the semester.

## Data
All data comes from the National Labor Relations Board (NLRB):
- Monthly election reports (PDFs): https://www.nlrb.gov/reports/agency-performance/election-reports
- Case docket pages: https://www.nlrb.gov/search/case

The cleaned dataset, `data/elections_clean.csv`, has 15,270 single-union RC elections
(FY2012–FY2026) and 21 columns. The target is `union_won` (1 = union won, 0 = lost).
- Column descriptions: `data/data_description.md`
- How the cleaning works, step by step: `processingexplained.md`
- What each data check found: `data/data_checks.md`

## Setup
Developed with Python 3.14.6. Package versions are listed in `requirements.txt`.

Using conda:

    conda env create -f environment.yml
    conda activate data1030-placeholder

Or using pip:

    pip install -r requirements.txt

## How to run
Run these from the main repo folder, in order:
1. `python src/pdfs_to_csv.py` downloads the NLRB monthly reports and converts them to CSVs in `data/raw_csvs/`
2. `python src/docket_scraping.py` scrapes each case's docket and participants into `data/docket_activity_merged.csv` and `data/case_participants_raw.csv`
3. `python src/preprocessmanual.py` cleans and merges everything into `data/elections_clean.csv`
4. EDA notebook (in progress) explores the data and makes the figures in `figures/`

## Repository structure
- `data/` raw and cleaned data
- `figures/` generated figures
- `results/` model outputs
- `report/` midterm report PDF and final presentation slides
- `src/` all code

## License
MIT, see `LICENSE`.
