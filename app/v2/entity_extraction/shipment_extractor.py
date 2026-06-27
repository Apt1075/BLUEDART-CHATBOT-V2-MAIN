from __future__ import annotations

import re
from typing import Optional


class ShipmentExtractor:
    def extract(self, message: str) -> Optional[str]:
        text = message.upper()
        long_match = re.search(r"\b(9\d{14})\b", text)
        if long_match:
            return long_match.group(1)
        short_match = re.search(r"\b(\d{7,9})\b", text)
        return short_match.group(1) if short_match else None