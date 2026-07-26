"""zall.extensions.science — E3 Science Kit extension (E3_SCIENCE_KIT.md §2.5).

Provides ScienceStore for append-only JSONL persistence of hypotheses,
experiments, and evidence.

IPR: extensions/ can import anything; core/ stays pure.
References: docs/E3_SCIENCE_KIT.md, MASTER.md §12.3 E3.
"""

from __future__ import annotations

from zall.extensions.science.store import ScienceStore

__all__ = ["ScienceStore"]