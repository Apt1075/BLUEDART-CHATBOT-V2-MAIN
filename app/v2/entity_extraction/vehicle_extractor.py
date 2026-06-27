from __future__ import annotations

import re
from typing import Optional


class VehicleExtractor:
    def extract(self, message: str) -> Optional[str]:
        text = message.upper()
        match = re.search(r"\b([A-Z]{2}\d{2}[A-Z]{1,3}\d{4})\b", text)
        return match.group(1) if match else None