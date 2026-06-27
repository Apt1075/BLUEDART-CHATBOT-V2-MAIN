from __future__ import annotations

import re
from typing import Optional


class ImeiExtractor:
    def extract(self, message: str) -> Optional[str]:
        match = re.search(r"\b(\d{15})\b", message)
        return match.group(1) if match else None