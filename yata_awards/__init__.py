"""
Award progress engine taken from YATA (Yet Another Torn App) by Kivou [2000607],
https://github.com/Kivou-2000607/yata - licensed GPL-3.0 (see LICENSE-GPL3.md).

Changes for Strike List: Django/YATA imports swapped for local equivalents, and two
stray lines in the "commitment" category commented out (they crashed on unmatched medals).
The award-to-stat mappings and goal/ETA maths are YATA's, unchanged.
"""
from .functions import AWARDS_CAT, HONORS_UNREACH, createAwards  # noqa: F401
