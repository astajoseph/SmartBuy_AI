"""Stock status of a vendor listing, from the availability text / schema.org value and the price."""
from __future__ import annotations

import re

# schema.org values in any capitalisation ("OutOfStock", Vijay Sales' "OutofStock", "out_of_stock") and shop wording
_NOT_BUYABLE = re.compile(r"out.?of.?stock|sold.?out|discontinued|pre.?order|back.?order|currently.?unavailable|"
                          r"not.?available|unavailable|notify.?me", re.I)


def in_stock(availability: str | None, price) -> bool:
    return price is not None and not _NOT_BUYABLE.search(availability or "")
