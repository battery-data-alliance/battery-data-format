"""Localised month-name normalisation for datetime text.

A vendor file can spell a month in the operator's own language, so a
datetime parse also tries the text with every month name token rewritten to
its two-digit month number before a format that would otherwise read that
token through ``%b``/``%B`` is retried with those directives rewritten to
``%m``. This module is the only reader of ``babel`` in the package: it holds
the CLDR locale scan behind two functions, :func:`month_numeral` (the
scalar form's rewrite of one text) and :func:`numeral_replacement_map` (the
expression form's vectorised token-replacement map), both built from the
same private table, so the two forms cannot disagree on which month a token
names.

Never pass a format that carries ``%b`` or ``%B`` to Polars
``str.to_datetime``. Polars matches ``"Jan"`` through its abbreviated table
even under ``%B``, then slices by the wide name's length and reads past the
end of the string. That is a Rust panic, and ``strict=False`` does not guard
against it. A plain English abbreviation panics the same way, so no locale
and no CLDR width is required to reach it. Rewriting every month-name token
to a plain numeral, and every ``%b``/``%B`` directive in the format to
``%m``, keeps that directive from ever being asked to parse anything.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from functools import lru_cache

from babel import Locale
from babel.localedata import locale_identifiers

# A month name candidate: a run of at least three letters, of any script. The
# CLDR narrow forms, which are one or two characters and heavily ambiguous,
# stay out of the map, so a shorter run is never a candidate.
_MONTH_TOKEN_RE = re.compile(r"[^\W\d_]{3,}")

_MONTH_CONTEXTS = ("format", "stand-alone")

# The CLDR widths this module scans votes across, so a name only one width
# uses is still counted. Not exposed: a caller of this module never asks for
# one width's spelling, only a token's month number.
_MONTH_WIDTHS = ("abbreviated", "wide")


@lru_cache(maxsize=1)
def _english_month_names() -> dict[str, dict[int, str]]:
    """Return the English CLDR month names, by width.

    Returns:
        A mapping of each width in ``_MONTH_WIDTHS`` to that width's month
        names, keyed by month number.
    """
    english = Locale.parse("en")
    return {width: dict(english.months["format"][width]) for width in _MONTH_WIDTHS}


@lru_cache(maxsize=1)
def _english_day_names() -> frozenset[str]:
    """Return every English CLDR day name, in every context and width.

    Returns:
        The lowercase day names, the trailing abbreviation dot stripped from
        each. A ``%a`` or ``%A`` token in the text is one of these, so the
        month map must not claim it.
    """
    english = Locale.parse("en")
    names: set[str] = set()
    for context in english.days.values():
        for width in context.values():
            names.update(name.lower().strip(".") for name in width.values())
    return frozenset(names)


@lru_cache(maxsize=1)
def _month_numbers() -> dict[str, int]:
    """Map every unambiguous CLDR month name to its month number.

    Babel serves the month names of each base language CLDR carries. One
    spelling can name a different month in two languages, so a token takes
    the month the largest number of languages give it. A token two months
    tie on is ambiguous and is left out of the map. An English day name is
    dropped, so a weekday another language spells like a month never
    rewrites the weekday an English format reads. An English month name
    always keeps its own month, so normalisation never rewrites text that
    already parses.

    Returns:
        Lowercase month name to month number, the trailing abbreviation dot
        stripped from each key.
    """
    votes: dict[str, Counter[int]] = defaultdict(Counter)
    for identifier in sorted(i for i in locale_identifiers() if "_" not in i):
        try:
            locale = Locale.parse(identifier)
        except Exception:
            continue
        for context in _MONTH_CONTEXTS:
            for width in _MONTH_WIDTHS:
                for number, name in locale.months.get(context, {}).get(width, {}).items():
                    votes[name.lower().strip(".")][number] += 1

    day_names = _english_day_names()
    numbers: dict[str, int] = {}
    for token, counted in votes.items():
        if token in day_names:
            continue
        ranked = counted.most_common(2)
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            continue
        numbers[token] = ranked[0][0]
    for names in _english_month_names().values():
        for number, name in names.items():
            numbers[name.lower().strip(".")] = number
    return numbers


def month_numeral(text: str) -> str:
    """Return ``text`` with every month name token replaced by its two-digit month number.

    Args:
        text: Datetime text that can carry a localised month name.

    Returns:
        ``text`` unchanged when no token names a month.
    """
    if not _MONTH_TOKEN_RE.search(text):
        return text
    numbers = _month_numbers()

    def _replace(match: re.Match[str]) -> str:
        number = numbers.get(match.group(0).lower())
        return match.group(0) if number is None else f"{number:02d}"

    return _MONTH_TOKEN_RE.sub(_replace, text)


@lru_cache(maxsize=1)
def numeral_replacement_map() -> dict[str, str]:
    """Return the localised month-name to two-digit-numeral map.

    Draws on the same :func:`_month_numbers` table :func:`month_numeral`
    reads, so a vectorised token replacement built from this map and a
    per-text rewrite built from that function can never disagree on which
    month a token names. A key that is not a whole ``_MONTH_TOKEN_RE`` run
    stays out: the scalar form rewrites such a run alone, so a short CLDR
    token like ``"pm"`` must not reach the vectorised form either. Every
    remaining token maps to its month number, zero-padded to two digits, in
    lowercase, title-case, and upper-case: a vectorised literal-pattern
    replacement matches case exactly, unlike the scalar form's ``.lower()``
    lookup, which folds full Unicode and so needs no stored case variant.

    Returns:
        Longest key first, so a short abbreviation that is a literal prefix
        of a longer word (``"Jan"`` of ``"Januar"``) cannot consume that
        word's position first and shadow it; a caller doing a leftmost
        vectorised replacement should keep this ordering.
    """
    numbers = _month_numbers()
    mapping: dict[str, str] = {}
    for token, number in numbers.items():
        if not _MONTH_TOKEN_RE.fullmatch(token):
            continue
        numeral = f"{number:02d}"
        for spelling in (token, token.capitalize(), token.upper()):
            mapping[spelling] = numeral
    return dict(sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True))
