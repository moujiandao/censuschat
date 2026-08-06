"""USPS state name <-> postal abbreviation — public, static reference data.

Not Snowflake schema data (unrelated to schema-notes.md recon); shared
between src/snapshot.py (builds the display name for a state/county row from
the raw abbreviation Snowflake stores) and src/tools.py (normalizes a
user-typed full state name to the abbreviation before matching).

Verified against live Snowflake (2020_METADATA_CBG_FIPS_CODES.STATE): the
column holds the two-letter postal abbreviation ("CA"), not the full name —
the opposite of schema-notes.md's "state/county FIPS -> names" description.
"""

from __future__ import annotations

ABBR_TO_NAME: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

NAME_TO_ABBR: dict[str, str] = {name.lower(): abbr for abbr, name in ABBR_TO_NAME.items()}
