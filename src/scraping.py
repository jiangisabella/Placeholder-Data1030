import time
from pathlib import Path
from urllib.parse import urljoin

import requests

# two steps:
# extract all pdfs: https://www.nlrb.gov/reports/agency-performance-report/election-reports/election-reports-fy-2026
# extract all case dockets 

pdf_url = "https://www.nlrb.gov/reports/agency-performance/election-reports"
case_docket_url = 'https://www.nlrb.gov/search/case'

