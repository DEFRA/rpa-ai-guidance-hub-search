"""Made-up titles and codes for title and code matching tests.

None of them is or resembles a real document title or code; they only keep the
shapes that matter: a series of titles sharing a start, codes with digits, and
words long enough to be forgiven a typo.
"""

CODE = "XY12"
SHORTER_CODE = "XY1"
NEAR_CODE = "XY13"
OTHER_CODE = "ZZ3"

SERIES = "Alpha Beta Topic"
SERIES_TITLE = f"{SERIES} - {CODE} or {OTHER_CODE} Example Rule Limit"
"""8 words once "or" is left out: alpha beta topic xy12 zz3 example rule limit."""
SERIES_SIBLING_TITLE = f"{SERIES} - Other Notes"
OTHER_SERIES_TITLE = "Alpha Gamma Topic Notes"

TYPO_TITLE = "Sample Report Layout Overview Notes"
"""5 words; "Sample Reports" and "sample reprot" both name it."""
