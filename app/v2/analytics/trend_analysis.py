from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List


def trend_series(records: List[Dict[str, Any]], date_field: str, bucket: str = "day") -> List[Dict[str, Any]]:
    counter = Counter()
    for record in records:
        raw_value = record.get(date_field)
        if not raw_value:
            continue
        text = str(raw_value)
        try:
            moment = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                moment = datetime.strptime(text[:10], "%Y-%m-%d")
            except ValueError:
                continue
        label = moment.strftime("%Y-%m-%d") if bucket == "day" else moment.strftime("%Y-%m")
        counter[label] += 1
    return [{"period": period, "count": count} for period, count in sorted(counter.items())]