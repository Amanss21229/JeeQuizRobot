"""Persistent reminder/task service for Private AI."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Optional

import pytz


class TaskService:
    DEFAULT_TIMEZONE = "Asia/Kolkata"

    WEEKDAYS = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }

    def __init__(self, db):
        self.db = db

    # ============================================================
    # BASIC DATABASE OPERATIONS
    # ============================================================

    async def create(
        self,
        user_id: int,
        task_text: str,
        schedule_type: str,
        schedule_data: dict,
        timezone_name: str = DEFAULT_TIMEZONE,
        next_run_at: datetime | None = None,
    ) -> int:

        return await self.db.create_ai_task(
            user_id=user_id,
            task_text=task_text,
            schedule_type=schedule_type,
            schedule_data=schedule_data,
            timezone_name=timezone_name,
            next_run_at=next_run_at,
        )

    async def list_active(
        self,
        user_id: int | None = None
    ) -> list[dict]:

        return await self.db.get_active_ai_tasks(
            user_id
        )

    async def cancel(
        self,
        user_id: int,
        task_id: int
    ) -> bool:

        return await self.db.deactivate_ai_task(
            task_id,
            user_id
        )

    async def get(
        self,
        user_id: int,
        task_id: int
    ) -> dict | None:

        return await self.db.get_active_ai_task(
            user_id=user_id,
            task_id=task_id
        )   

    async def edit_text(
        self,
        user_id: int,
        task_id: int,
        new_text: str
    ) -> dict | None:
        """Change reminder text without changing its schedule."""

        task = await self.get(
            user_id,
            task_id
        )

        if not task:
            return None

        new_text = (
            new_text
            or ""
        ).strip()

        if not new_text:
            return None

        new_text = new_text[:500]

        schedule_data = (
            task.get("schedule_data")
            or {}
        )

        if isinstance(
            schedule_data,
            str
        ):
            try:
                schedule_data = json.loads(
                    schedule_data
                )
            except (
                TypeError,
                ValueError
            ):
                schedule_data = {}

        if not isinstance(
            schedule_data,
            dict
        ):
            schedule_data = {}

        updated = (
            await self.db.update_ai_task(
                task_id=task_id,
                user_id=user_id,
                task_text=new_text,
                schedule_type=(
                    task.get("schedule_type")
                    or "once"
                ),
                schedule_data=schedule_data,
                timezone_name=(
                    task.get("timezone")
                    or self.DEFAULT_TIMEZONE
                ),
                next_run_at=task[
                    "next_run_at"
                ]
            )
        )

        if not updated:
            return None

        return await self.get(
            user_id,
            task_id
        )    

    async def reschedule(
        self,
        user_id: int,
        task_id: int,
        schedule_text: str
    ) -> dict | None:
        """
        Reschedule an existing reminder while keeping its
        original ID and task text.
        """

        task = await self.get(
            user_id,
            task_id
        )

        if not task:
            return None

        text = (
            schedule_text
            or ""
        ).strip()

        if not text:
            return None

        # Natural filler words.
        text = re.sub(
            r"^(?:to|at|for)\s+",
            "",
            text,
            flags=re.IGNORECASE
        ).strip()

        timezone_name = (
            task.get("timezone")
            or self.DEFAULT_TIMEZONE
        )

        timezone = pytz.timezone(
            timezone_name
        )

        now = datetime.now(
            timezone
        )

        schedule_type = "once"
        schedule_data = {}
        run_local = None

        # ----------------------------------------------------
        # IN X MINUTES / HOURS
        # ----------------------------------------------------

        match = re.fullmatch(
            r"in\s+(\d+)\s*"
            r"(minute|minutes|min|mins|"
            r"hour|hours|hr|hrs)",
            text,
            flags=re.IGNORECASE
        )

        if match:

            amount = int(
                match.group(1)
            )

            if amount <= 0:
                return None

            unit = (
                match.group(2)
                .lower()
            )

            if unit.startswith(
                ("hour", "hr")
            ):
                delta = timedelta(
                    hours=amount
                )
            else:
                delta = timedelta(
                    minutes=amount
                )

            run_local = now + delta

            schedule_type = "once"

            schedule_data = {
                "kind": "relative"
            }

        # ----------------------------------------------------
        # DAILY
        # ----------------------------------------------------

        if run_local is None:

            match = re.fullmatch(
                r"(?:daily|every\s+day)"
                r"(?:\s+at)?\s+"
                r"(\d{1,2})"
                r"(?::(\d{2}))?"
                r"\s*(am|pm)?",
                text,
                flags=re.IGNORECASE
            )

            if match:

                parsed = self._parse_clock(
                    match.group(1),
                    match.group(2),
                    match.group(3)
                )

                if not parsed:
                    return None

                hour, minute = parsed

                run_local = now.replace(
                    hour=hour,
                    minute=minute,
                    second=0,
                    microsecond=0
                )

                if run_local <= now:
                    run_local += timedelta(
                        days=1
                    )

                schedule_type = "daily"

                schedule_data = {
                    "hour": hour,
                    "minute": minute
                }

        # ----------------------------------------------------
        # WEEKLY
        # ----------------------------------------------------

        if run_local is None:

            weekday_names = "|".join(
                self.WEEKDAYS.keys()
            )

            match = re.fullmatch(
                rf"(?:every\s+)?"
                rf"({weekday_names})"
                r"(?:\s+at)?\s+"
                r"(\d{1,2})"
                r"(?::(\d{2}))?"
                r"\s*(am|pm)?",
                text,
                flags=re.IGNORECASE
            )

            if match:

                weekday_name = (
                    match.group(1)
                    .lower()
                )

                parsed = self._parse_clock(
                    match.group(2),
                    match.group(3),
                    match.group(4)
                )

                if not parsed:
                    return None

                hour, minute = parsed

                weekday = self.WEEKDAYS[
                    weekday_name
                ]

                days_ahead = (
                    weekday
                    - now.weekday()
                ) % 7

                run_local = (
                    now
                    + timedelta(
                        days=days_ahead
                    )
                ).replace(
                    hour=hour,
                    minute=minute,
                    second=0,
                    microsecond=0
                )

                if run_local <= now:
                    run_local += timedelta(
                        days=7
                    )

                schedule_type = "weekly"

                schedule_data = {
                    "weekday": weekday,
                    "weekday_name": weekday_name,
                    "hour": hour,
                    "minute": minute
                }

        # ----------------------------------------------------
        # TOMORROW
        # ----------------------------------------------------

        if run_local is None:

            match = re.fullmatch(
                r"tomorrow"
                r"(?:\s+at)?\s+"
                r"(\d{1,2})"
                r"(?::(\d{2}))?"
                r"\s*(am|pm)?",
                text,
                flags=re.IGNORECASE
            )

            if match:

                parsed = self._parse_clock(
                    match.group(1),
                    match.group(2),
                    match.group(3)
                )

                if not parsed:
                    return None

                hour, minute = parsed

                run_local = (
                    now
                    + timedelta(days=1)
                ).replace(
                    hour=hour,
                    minute=minute,
                    second=0,
                    microsecond=0
                )

                schedule_type = "once"

                schedule_data = {
                    "kind": "tomorrow"
                }

        # ----------------------------------------------------
        # TODAY
        # ----------------------------------------------------

        if run_local is None:

            match = re.fullmatch(
                r"today"
                r"(?:\s+at)?\s+"
                r"(\d{1,2})"
                r"(?::(\d{2}))?"
                r"\s*(am|pm)?",
                text,
                flags=re.IGNORECASE
            )

            if match:

                parsed = self._parse_clock(
                    match.group(1),
                    match.group(2),
                    match.group(3)
                )

                if not parsed:
                    return None

                hour, minute = parsed

                run_local = now.replace(
                    hour=hour,
                    minute=minute,
                    second=0,
                    microsecond=0
                )

                if run_local <= now:
                    return None

                schedule_type = "once"

                schedule_data = {
                    "kind": "today"
                }

        # ----------------------------------------------------
        # SIMPLE CLOCK
        # 9 pm
        # at 9:30 pm
        # ----------------------------------------------------

        if run_local is None:

            match = re.fullmatch(
                r"(?:at\s+)?"
                r"(\d{1,2})"
                r"(?::(\d{2}))?"
                r"\s*(am|pm)?",
                text,
                flags=re.IGNORECASE
            )

            if match:

                parsed = self._parse_clock(
                    match.group(1),
                    match.group(2),
                    match.group(3)
                )

                if not parsed:
                    return None

                hour, minute = parsed

                run_local = now.replace(
                    hour=hour,
                    minute=minute,
                    second=0,
                    microsecond=0
                )

                if run_local <= now:
                    run_local += timedelta(
                        days=1
                    )

                schedule_type = "once"

                schedule_data = {
                    "kind": "clock"
                }

        if run_local is None:
            return None

        next_run_at = self._to_db_datetime(
            run_local
        )

        updated = (
            await self.db.update_ai_task(
                task_id=task_id,
                user_id=user_id,
                task_text=task[
                    "task_text"
                ],
                schedule_type=schedule_type,
                schedule_data=schedule_data,
                timezone_name=timezone_name,
                next_run_at=next_run_at
            )
        )

        if not updated:
            return None

        return await self.get(
            user_id,
            task_id
        )    

    async def update_next_run(
        self,
        task_id: int,
        next_run_at: datetime | None,
        active: bool = True,
    ) -> None:

        await self.db.update_ai_task_next_run(
            task_id,
            next_run_at,
            active
        )

    # ============================================================
    # TIME HELPERS
    # ============================================================

    @staticmethod
    def _parse_clock(
        hour_text: str,
        minute_text: Optional[str],
        ampm: Optional[str],
    ) -> tuple[int, int] | None:

        try:
            hour = int(hour_text)
            minute = int(
                minute_text or 0
            )
        except ValueError:
            return None

        if minute < 0 or minute > 59:
            return None

        if ampm:
            ampm = ampm.lower()

            if hour < 1 or hour > 12:
                return None

            if ampm == "am":
                if hour == 12:
                    hour = 0
            else:
                if hour != 12:
                    hour += 12

        else:
            if hour < 0 or hour > 23:
                return None

        return hour, minute

    @staticmethod
    def _to_db_datetime(
        local_dt: datetime
    ) -> datetime:
        """
        ai_tasks.next_run_at currently uses TIMESTAMP rather
        than TIMESTAMPTZ.

        Store a timezone-naive UTC datetime consistently.
        """

        return (
            local_dt
            .astimezone(pytz.UTC)
            .replace(tzinfo=None)
        )

    @staticmethod
    def db_to_local(
        value: datetime,
        timezone_name: str
    ) -> datetime:

        timezone = pytz.timezone(
            timezone_name
        )

        if value.tzinfo is None:
            value = pytz.UTC.localize(
                value
            )

        return value.astimezone(
            timezone
        )

    # ============================================================
    # PARSER
    # ============================================================

    async def parse_and_create(
        self,
        user_id: int,
        text: str,
        timezone_name: str = DEFAULT_TIMEZONE,
    ) -> dict | None:
        """
        Parse a reminder request and create it.

        Returns:
        {
            "task_id": int,
            "task_text": str,
            "schedule_type": str,
            "next_run_at": datetime,
            "timezone": str
        }

        Returns None when the text is not a recognized
        reminder request.
        """

        original = (
            text or ""
        ).strip()

        if not original:
            return None

        # Remove command prefix.
        cleaned = re.sub(
            r"^/remind(?:@\w+)?\s*",
            "",
            original,
            flags=re.IGNORECASE,
        ).strip()

        # Remove conversational prefix.
        cleaned = re.sub(
            r"^remind\s+me\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()

        if not cleaned:
            return None

        timezone = pytz.timezone(
            timezone_name
        )

        now = datetime.now(
            timezone
        )

        # ========================================================
        # IN X MINUTES / HOURS
        # ========================================================

        match = re.match(
            r"^in\s+(\d+)\s*"
            r"(minute|minutes|min|mins|hour|hours|hr|hrs)"
            r"\s+(?:to\s+)?(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )

        if match:

            amount = int(
                match.group(1)
            )

            unit = (
                match.group(2)
                .lower()
            )

            task_text = (
                match.group(3)
                .strip()
            )

            if amount <= 0:
                return None

            if unit.startswith(
                ("hour", "hr")
            ):
                delta = timedelta(
                    hours=amount
                )
            else:
                delta = timedelta(
                    minutes=amount
                )

            run_local = now + delta

            return await self._create_parsed(
                user_id=user_id,
                task_text=task_text,
                schedule_type="once",
                schedule_data={
                    "kind": "relative"
                },
                run_local=run_local,
                timezone_name=timezone_name,
            )

        # ========================================================
        # DAILY
        # daily 8 pm revise biology
        # every day at 8 pm revise biology
        # ========================================================

        match = re.match(
            r"^(?:daily|every\s+day)"
            r"(?:\s+at)?\s+"
            r"(\d{1,2})"
            r"(?::(\d{2}))?"
            r"\s*(am|pm)?"
            r"\s+(?:to\s+)?(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )

        if match:

            parsed_time = self._parse_clock(
                match.group(1),
                match.group(2),
                match.group(3),
            )

            if not parsed_time:
                return None

            hour, minute = parsed_time

            task_text = (
                match.group(4)
                .strip()
            )

            run_local = now.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )

            if run_local <= now:
                run_local += timedelta(
                    days=1
                )

            return await self._create_parsed(
                user_id=user_id,
                task_text=task_text,
                schedule_type="daily",
                schedule_data={
                    "hour": hour,
                    "minute": minute,
                },
                run_local=run_local,
                timezone_name=timezone_name,
            )

        # ========================================================
        # WEEKLY
        # every monday at 8 pm mock test
        # ========================================================

        weekday_names = "|".join(
            self.WEEKDAYS.keys()
        )

        match = re.match(
            rf"^every\s+({weekday_names})"
            r"(?:\s+at)?\s+"
            r"(\d{1,2})"
            r"(?::(\d{2}))?"
            r"\s*(am|pm)?"
            r"\s+(?:to\s+)?(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )

        if match:

            weekday_name = (
                match.group(1)
                .lower()
            )

            parsed_time = self._parse_clock(
                match.group(2),
                match.group(3),
                match.group(4),
            )

            if not parsed_time:
                return None

            hour, minute = parsed_time

            task_text = (
                match.group(5)
                .strip()
            )

            target_weekday = (
                self.WEEKDAYS[
                    weekday_name
                ]
            )

            days_ahead = (
                target_weekday
                - now.weekday()
            ) % 7

            run_local = (
                now
                + timedelta(
                    days=days_ahead
                )
            ).replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )

            if run_local <= now:
                run_local += timedelta(
                    days=7
                )

            return await self._create_parsed(
                user_id=user_id,
                task_text=task_text,
                schedule_type="weekly",
                schedule_data={
                    "weekday": target_weekday,
                    "weekday_name": weekday_name,
                    "hour": hour,
                    "minute": minute,
                },
                run_local=run_local,
                timezone_name=timezone_name,
            )

        # ========================================================
        # TOMORROW
        # tomorrow at 8 pm revise physics
        # ========================================================

        match = re.match(
            r"^tomorrow"
            r"(?:\s+at)?\s+"
            r"(\d{1,2})"
            r"(?::(\d{2}))?"
            r"\s*(am|pm)?"
            r"\s+(?:to\s+)?(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )

        if match:

            parsed_time = self._parse_clock(
                match.group(1),
                match.group(2),
                match.group(3),
            )

            if not parsed_time:
                return None

            hour, minute = parsed_time

            task_text = (
                match.group(4)
                .strip()
            )

            run_local = (
                now
                + timedelta(days=1)
            ).replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )

            return await self._create_parsed(
                user_id=user_id,
                task_text=task_text,
                schedule_type="once",
                schedule_data={
                    "kind": "tomorrow"
                },
                run_local=run_local,
                timezone_name=timezone_name,
            )

        # ========================================================
        # TODAY
        # today at 9 pm revise chemistry
        # ========================================================

        match = re.match(
            r"^today"
            r"(?:\s+at)?\s+"
            r"(\d{1,2})"
            r"(?::(\d{2}))?"
            r"\s*(am|pm)?"
            r"\s+(?:to\s+)?(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )

        if match:

            parsed_time = self._parse_clock(
                match.group(1),
                match.group(2),
                match.group(3),
            )

            if not parsed_time:
                return None

            hour, minute = parsed_time

            task_text = (
                match.group(4)
                .strip()
            )

            run_local = now.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )

            if run_local <= now:
                return None

            return await self._create_parsed(
                user_id=user_id,
                task_text=task_text,
                schedule_type="once",
                schedule_data={
                    "kind": "today"
                },
                run_local=run_local,
                timezone_name=timezone_name,
            )

        # ========================================================
        # AT TIME
        # at 8 pm revise biology
        # 8 pm revise biology
        #
        # If today's time has passed → tomorrow.
        # ========================================================

        match = re.match(
            r"^(?:at\s+)?"
            r"(\d{1,2})"
            r"(?::(\d{2}))?"
            r"\s*(am|pm)?"
            r"\s+(?:to\s+)?(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )

        if match:

            parsed_time = self._parse_clock(
                match.group(1),
                match.group(2),
                match.group(3),
            )

            if not parsed_time:
                return None

            hour, minute = parsed_time

            task_text = (
                match.group(4)
                .strip()
            )

            run_local = now.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )

            if run_local <= now:
                run_local += timedelta(
                    days=1
                )

            return await self._create_parsed(
                user_id=user_id,
                task_text=task_text,
                schedule_type="once",
                schedule_data={
                    "kind": "clock"
                },
                run_local=run_local,
                timezone_name=timezone_name,
            )

        return None

    async def _create_parsed(
        self,
        user_id: int,
        task_text: str,
        schedule_type: str,
        schedule_data: dict,
        run_local: datetime,
        timezone_name: str,
    ) -> dict:

        task_text = (
            task_text
            .strip()
        )

        if len(task_text) > 500:
            task_text = task_text[:500]

        next_run_db = self._to_db_datetime(
            run_local
        )

        task_id = await self.create(
            user_id=user_id,
            task_text=task_text,
            schedule_type=schedule_type,
            schedule_data=schedule_data,
            timezone_name=timezone_name,
            next_run_at=next_run_db,
        )

        return {
            "task_id": task_id,
            "task_text": task_text,
            "schedule_type": schedule_type,
            "next_run_at": next_run_db,
            "timezone": timezone_name,
        }

    # ============================================================
    # AFTER DELIVERY
    # ============================================================

    async def mark_delivered(
        self,
        task: dict
    ) -> None:

        task_id = int(
            task["id"]
        )

        schedule_type = (
            task.get("schedule_type")
            or "once"
        )

        if schedule_type == "once":

            await self.update_next_run(
                task_id,
                None,
                active=False,
            )

            return

        timezone_name = (
            task.get("timezone")
            or self.DEFAULT_TIMEZONE
        )

        timezone = pytz.timezone(
            timezone_name
        )

        now = datetime.now(
            timezone
        )

        schedule_data = (
            task.get("schedule_data")
            or {}
        )

        # asyncpg normally returns JSON/JSONB as a string
        # unless a custom codec has been configured.
        if isinstance(schedule_data, str):
            try:
                schedule_data = json.loads(
                    schedule_data
                )
            except (TypeError, ValueError):
                schedule_data = {}

        if not isinstance(
            schedule_data,
            dict
        ):
            schedule_data = {}

        hour = int(
            schedule_data.get(
                "hour",
                8
            )
        )

        minute = int(
            schedule_data.get(
                "minute",
                0
            )
        )

        if schedule_type == "daily":

            next_local = now.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )

            if next_local <= now:
                next_local += timedelta(
                    days=1
                )

            await self.update_next_run(
                task_id,
                self._to_db_datetime(
                    next_local
                ),
                active=True,
            )

            return

        if schedule_type == "weekly":

            weekday = int(
                schedule_data.get(
                    "weekday",
                    0
                )
            )

            days_ahead = (
                weekday
                - now.weekday()
            ) % 7

            next_local = (
                now
                + timedelta(
                    days=days_ahead
                )
            ).replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )

            if next_local <= now:
                next_local += timedelta(
                    days=7
                )

            await self.update_next_run(
                task_id,
                self._to_db_datetime(
                    next_local
                ),
                active=True,
            )

            return

        # Unknown schedule type: safely deactivate.
        await self.update_next_run(
            task_id,
            None,
            active=False,
        )


__all__ = ["TaskService"]
