from __future__ import annotations

import re
from typing import Optional


class TripIdExtractor:
    def extract(self, message: str) -> Optional[str]:
        text = message.lower()
        match = re.search(r"\b(?:trip\s*id|tripid|trip\s*no|trip\s*number)\s*[:#-]?\s*([a-z0-9_\-]{4,20})\b", text)
        if match:
            return match.group(1)
        return None