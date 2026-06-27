from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import dateparser
from dateparser.search import search_dates


@dataclass(slots=True)
class DateRange:
    date_from: str | None = None
    date_to: str | None = None

    def to_mongo(self) -> dict:
        if not self.date_from or not self.date_to:
            return {}

        return {
            "$gte": self.date_from,
            "$lte": self.date_to,
        }


class DateExtractor:
    def extract(self, text: str) -> DateRange:
        text = text.lower().strip()
        now = datetime.now()

        handlers = [
            self._handle_fiscal_year,
            self._handle_quarter,
            self._handle_mtd,
            self._handle_ytd,
            self._handle_relative_keywords,
            self._handle_colloquial_date_range,
            self._handle_explicit_range,
            # Specific-date handlers come BEFORE month-range handlers so that
            # "12 may 2026" is resolved as May 12, not the whole of May.
            self._handle_specific_day_month_year,
            self._handle_month_year,
            self._handle_month_only,
            self._handle_search_dates,
            self._handle_single_date,
        ]

        for handler in handlers:
            result = handler(text, now)
            if result:
                return result

        return DateRange()

    # --------------------------------------------------------
    # Fiscal Year
    # --------------------------------------------------------

    def _handle_fiscal_year(
        self,
        text: str,
        now: datetime,
    ) -> DateRange | None:

        if "current financial year" in text or "current fy" in text:
            return self._current_fy(now)

        match = re.search(r"\bfy\s*(\d{2,4})\b", text)

        if not match:
            return None

        fy = match.group(1)

        if len(fy) == 2:
            year = 2000 + int(fy)
        else:
            year = int(fy)

        start = datetime(year - 1, 4, 1)
        end = datetime(year, 3, 31)

        return self._range(start, end)

    def _current_fy(self, now: datetime) -> DateRange:
        if now.month >= 4:
            start = datetime(now.year, 4, 1)
            end = now
        else:
            start = datetime(now.year - 1, 4, 1)
            end = now

        return self._range(start, end)

    # --------------------------------------------------------
    # Quarter
    # --------------------------------------------------------

    def _handle_quarter(
        self,
        text: str,
        now: datetime,
    ) -> DateRange | None:

        match = re.search(
            r"\bq([1-4])(?:\s*(\d{4}))?\b",
            text,
            flags=re.I,
        )

        if not match:
            return None

        quarter = int(match.group(1))
        year = int(match.group(2) or now.year)

        quarter_map = {
            1: (1, 3),
            2: (4, 6),
            3: (7, 9),
            4: (10, 12),
        }

        start_month, end_month = quarter_map[quarter]

        start = datetime(year, start_month, 1)

        if end_month == 12:
            end = datetime(year, 12, 31)
        else:
            end = datetime(year, end_month + 1, 1) - timedelta(days=1)

        return self._range(start, end)

    # --------------------------------------------------------
    # MTD / YTD
    # --------------------------------------------------------

    def _handle_mtd(
        self,
        text: str,
        now: datetime,
    ) -> DateRange | None:

        if "mtd" not in text and "month to date" not in text:
            return None

        start = datetime(now.year, now.month, 1)

        return self._range(start, now)

    def _handle_ytd(
        self,
        text: str,
        now: datetime,
    ) -> DateRange | None:

        if "ytd" not in text and "year to date" not in text:
            return None

        start = datetime(now.year, 1, 1)

        return self._range(start, now)

    # --------------------------------------------------------
    # Relative keywords
    # --------------------------------------------------------

    def _handle_relative_keywords(
        self,
        text: str,
        now: datetime,
    ) -> DateRange | None:

        if "today" in text:
            return self._range(now, now)

        if "yesterday" in text:
            day = now - timedelta(days=1)
            return self._range(day, day)

        if "this week" in text:
            start = now - timedelta(days=now.weekday())
            return self._range(start, now)

        if "last week" in text:
            end = now - timedelta(days=now.weekday() + 1)
            start = end - timedelta(days=6)
            return self._range(start, end)

        if "this month" in text:
            start = datetime(now.year, now.month, 1)
            return self._range(start, now)

        if "last month" in text:
            last_day = now.replace(day=1) - timedelta(days=1)
            start = last_day.replace(day=1)
            return self._range(start, last_day)

        if "this year" in text:
            start = datetime(now.year, 1, 1)
            return self._range(start, now)

        match = re.search(r"last\s+(\d+)\s+days?", text)

        if match:
            days = int(match.group(1))
            start = now - timedelta(days=days)
            return self._range(start, now)

        return None

    # --------------------------------------------------------
    # Explicit Range
    # --------------------------------------------------------

    def _handle_explicit_range(
        self,
        text: str,
        now: datetime,
    ) -> DateRange | None:

        patterns = [
            r"between\s+(.+?)\s+and\s+(.+)",
            r"from\s+(.+?)\s+(?:to|till|until|through)\s+(.+)",
            r"(.+?)\s+(?:to|till|until|through|-)\s+(.+)",
        ]

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if not match:
                continue

            start = dateparser.parse(
                match.group(1),
                settings={
                    "RELATIVE_BASE": now,
                    "PREFER_DATES_FROM": "past",
                    "DATE_ORDER": "DMY",
                },
            )

            end = dateparser.parse(
                match.group(2),
                settings={
                    "RELATIVE_BASE": now,
                    "PREFER_DATES_FROM": "past",
                    "DATE_ORDER": "DMY",
                },
            )

            if start and end:
                return self._range(start, end)

        return None

    # --------------------------------------------------------
    # Month-only  (e.g. "may month", "in may", "for may 2026")
    # Fires when month name present but NOT followed by a year digit
    # (that case is already handled by _handle_month_year above)
    # --------------------------------------------------------

    def _handle_month_only(
        self,
        text: str,
        now: datetime,
    ) -> DateRange | None:
        """Return a full-month range when only a month name is present."""
        # Skip if a specific day is already present (e.g. "15 may total trips")
        if self._has_specific_day(text):
            return None
        month_names = "|".join(self._MONTH_MAP.keys())
        match = re.search(rf"\b({month_names})\b", text, flags=re.IGNORECASE)
        if not match:
            return None

        month = self._MONTH_MAP[match.group(1).lower()]

        # Try to find an explicit 4-digit year in the text
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            year = int(year_match.group(1))
        else:
            year = now.year
            if month > now.month:
                year -= 1

        last_day = calendar.monthrange(year, month)[1]
        start = datetime(year, month, 1)
        end = datetime(year, month, last_day)
        return self._range(start, end)

    # --------------------------------------------------------
    # Search Dates
    # --------------------------------------------------------

    def _handle_search_dates(
        self,
        text: str,
        now: datetime,
    ) -> DateRange | None:

        results = search_dates(
            text,
            settings={
                "RELATIVE_BASE": now,
                "PREFER_DATES_FROM": "past",
                "DATE_ORDER": "DMY",
            },
        )

        if not results:
            return None

        dates = [x[1] for x in results]

        if len(dates) >= 2:
            return self._range(
                min(dates),
                max(dates),
            )

        return None

    # --------------------------------------------------------
    # Month-Year Range  (e.g. "jan 2026", "january 2026", "dec-25")
    # --------------------------------------------------------

    _MONTH_MAP: dict[str, int] = {
        "january": 1, "jan": 1,
        "february": 2, "feb": 2,
        "march": 3, "mar": 3,
        "april": 4, "apr": 4,
        "may": 5,
        "june": 6, "jun": 6,
        "july": 7, "jul": 7,
        "august": 8, "aug": 8,
        "september": 9, "sep": 9, "sept": 9,
        "october": 10, "oct": 10,
        "november": 11, "nov": 11,
        "december": 12, "dec": 12,
    }

    def _has_specific_day(self, text: str) -> bool:
        """True when the text contains a day number next to a month name."""
        month_names = "|".join(self._MONTH_MAP.keys())
        # DD Month  or  Month DD  or  DD Month YYYY
        return bool(
            re.search(
                rf"\b(\d{{1,2}})\s+(?:{month_names})\b",
                text,
                flags=re.IGNORECASE,
            )
            or re.search(
                rf"\b(?:{month_names})\s+(\d{{1,2}})\b",
                text,
                flags=re.IGNORECASE,
            )
        )

    def _handle_specific_day_month_year(
        self,
        text: str,
        now: datetime,
    ) -> DateRange | None:
        """Handle patterns like '12 may 2026', '12 may', 'may 12 2026'."""
        month_names = "|".join(self._MONTH_MAP.keys())
        # Pattern: DD Month [YYYY]
        match = re.search(
            rf"\b(\d{{1,2}})\s+({month_names})(?:\s+(\d{{4}}))?\b",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            # Pattern: Month DD [YYYY]
            match = re.search(
                rf"\b({month_names})\s+(\d{{1,2}})(?:\s+(\d{{4}}))?\b",
                text,
                flags=re.IGNORECASE,
            )
            if not match:
                return None
            month = self._MONTH_MAP[match.group(1).lower()]
            day = int(match.group(2))
            raw_year = match.group(3)
        else:
            day = int(match.group(1))
            month = self._MONTH_MAP[match.group(2).lower()]
            raw_year = match.group(3)

        if day < 1 or day > 31:
            return None

        year = int(raw_year) if raw_year else now.year
        if year < 100:
            year = 2000 + year

        try:
            specific = datetime(year, month, day)
        except ValueError:
            return None

        return self._range(specific, specific)

    def _handle_month_year(
        self,
        text: str,
        now: datetime,
    ) -> DateRange | None:
        """Return a full-month range for patterns like 'jan 2026' or 'january 25'."""
        # Skip if a specific day is already present (e.g. "12 may 2026")
        if self._has_specific_day(text):
            return None
        month_names = "|".join(self._MONTH_MAP.keys())
        pattern = rf"\b({month_names})\s*[-/]?\s*(\d{{2,4}})\b"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None

        month = self._MONTH_MAP[match.group(1).lower()]
        raw_year = int(match.group(2))
        year = 2000 + raw_year if raw_year < 100 else raw_year

        last_day = calendar.monthrange(year, month)[1]
        start = datetime(year, month, 1)
        end = datetime(year, month, last_day)
        return self._range(start, end)

    # --------------------------------------------------------
    # Single Date
    # --------------------------------------------------------

    def _handle_single_date(
        self,
        text: str,
        now: datetime,
    ) -> DateRange | None:

        parsed = dateparser.parse(
            text,
            settings={
                "RELATIVE_BASE": now,
                "PREFER_DATES_FROM": "past",
                "DATE_ORDER": "DMY",
            },
        )

        if not parsed:
            return None

        return self._range(parsed, parsed)

    # --------------------------------------------------------
    # Colloquial Ranges (e.g. "may 1 to 10", "10 to 20 may", "1 may to 10 june")
    # --------------------------------------------------------

    def _handle_colloquial_date_range(
        self,
        text: str,
        now: datetime,
    ) -> DateRange | None:
        month_names = "|".join(self._MONTH_MAP.keys())

        # Year detection helper
        year_match = re.search(r"\b(20\d{2})\b", text)
        explicit_year = int(year_match.group(1)) if year_match else None

        # Case 3: DD Month1 to DD Month2 (e.g., "10 may to 20 june")
        p3 = rf"\b(?:from\s+)?(\d{{1,2}})\s+({month_names})\s*(?:to|till|until|through|-)\s*(\d{{1,2}})\s+({month_names})\b"
        m3 = re.search(p3, text, flags=re.IGNORECASE)
        if m3:
            day_from = int(m3.group(1))
            month_from = self._MONTH_MAP[m3.group(2).lower()]
            day_to = int(m3.group(3))
            month_to = self._MONTH_MAP[m3.group(4).lower()]

            year_from = explicit_year if explicit_year else now.year
            year_to = explicit_year if explicit_year else now.year

            if not explicit_year and month_from > month_to:
                if now.month <= 6:
                    year_from = now.year - 1
                else:
                    year_to = now.year + 1

            try:
                start = datetime(year_from, month_from, day_from)
                end = datetime(year_to, month_to, day_to)
                return self._range(start, end)
            except ValueError:
                pass

        # Case 1: Month DD to DD (e.g., "may 1 to 10")
        p1 = rf"\b(?:from\s+)?({month_names})\s+(\d{{1,2}})\s*(?:to|till|until|through|-)\s*(\d{{1,2}})\b"
        m1 = re.search(p1, text, flags=re.IGNORECASE)
        if m1:
            month = self._MONTH_MAP[m1.group(1).lower()]
            day_from = int(m1.group(2))
            day_to = int(m1.group(3))

            year = explicit_year if explicit_year else now.year
            if not explicit_year and month > now.month:
                year -= 1

            try:
                start = datetime(year, month, day_from)
                end = datetime(year, month, day_to)
                if start > end:
                    start, end = end, start
                return self._range(start, end)
            except ValueError:
                pass

        # Case 2: DD to DD Month (e.g., "1 to 10 may")
        p2 = rf"\b(?:from\s+)?(\d{{1,2}})\s*(?:to|till|until|through|-)\s*(\d{{1,2}})\s+({month_names})\b"
        m2 = re.search(p2, text, flags=re.IGNORECASE)
        if m2:
            day_from = int(m2.group(1))
            day_to = int(m2.group(2))
            month = self._MONTH_MAP[m2.group(3).lower()]

            year = explicit_year if explicit_year else now.year
            if not explicit_year and month > now.month:
                year -= 1

            try:
                start = datetime(year, month, day_from)
                end = datetime(year, month, day_to)
                if start > end:
                    start, end = end, start
                return self._range(start, end)
            except ValueError:
                pass

        return None

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------

    def _range(
        self,
        start: datetime,
        end: datetime,
    ) -> DateRange:

        return DateRange(
            date_from=start.strftime("%Y-%m-%d 00:00:00"),
            date_to=end.strftime("%Y-%m-%d 23:59:59"),
        )