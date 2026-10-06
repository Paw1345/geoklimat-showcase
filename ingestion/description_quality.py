"""Recognize explicit absence markers without rewriting source evidence."""

import re

# Shared by Python display logic and PostgreSQL quality aggregates.
# PostgreSQL's locale-dependent \s does not include all Python Unicode spaces.
SPACES = (
    "\t\n\r\f\v \u001c\u001d\u001e\u001f\u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
MISSING_DESCRIPTION = f"^[{SPACES}]*(?:-|–|—|\\.\\.\\.|…)?[{SPACES}]*$"


def has_description(value):
    return re.fullmatch(MISSING_DESCRIPTION, value or "") is None
