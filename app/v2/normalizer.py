from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Dict, List, Tuple


class QueryNormalizer:
    SYNONYMS: Dict[str, str] = {
        "shipment number": "shipment",
        "consignment": "shipment",
        "parcel": "shipment",
        "gaadi": "vehicle",
        "truck": "vehicle",
        "kahan": "where",
        "kab": "when",
        "pahunchega": "eta",
        "aayega": "eta",
        "delay hua": "delay",
        "ruka": "stopped",
        "ruk gaya": "stopped",
        "live location": "location",
        "current location": "location",
        "customer id": "customer_id",
        "route id": "route_id",
    }

    COMMON_TYPO_MAP: Dict[str, str] = {
        "voilation": "violation",
        "delievery": "delivery",
        "locaton": "location",
        "trp": "trip",
        "shiment": "shipment",
        "vehcle": "vehicle",
    }

    def normalize(self, message: str) -> Tuple[str, List[str]]:
        text = message.strip().lower()
        notes: List[str] = []

        text = re.sub(r"\s+", " ", text)
        for source, target in self.SYNONYMS.items():
            if source in text:
                text = text.replace(source, target)
                notes.append(f"synonym:{source}->{target}")

        for source, target in self.COMMON_TYPO_MAP.items():
            if source in text:
                text = text.replace(source, target)
                notes.append(f"typo:{source}->{target}")

        tokens = [self._correct_token(token) for token in text.split()]
        normalized = " ".join(tokens)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized, notes

    def _correct_token(self, token: str) -> str:
        if len(token) <= 4:
            return token
        matches = get_close_matches(token, ["shipment", "vehicle", "location", "delay", "alert", "analytics", "customer", "route", "status", "trips"], n=1, cutoff=0.9)
        return matches[0] if matches else token