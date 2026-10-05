"""Credit and session business logic for the private AI system."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional
import random


# ============================================================
# DAILY BONUS
# ============================================================

BONUS_WEIGHTS = {
    10: 20,
    11: 15,
    12: 15,
    13: 12,
    14: 10,
    15: 8,
    16: 7,
    17: 5,
    18: 5,
    19: 3,
}


# ============================================================
# SESSION CONFIGURATION
# ============================================================

AI_SESSION_INACTIVITY_MINUTES = 5


class CreditService:
    """
    High-level credit and AI session rules.

    Database remains the source of truth.
    """

    def __init__(self, db):
        self.db = db

    # ========================================================
    # BASIC CREDIT METHODS
    # ========================================================

    async def initialize_for_user(
        self,
        user_id: int
    ) -> bool:
        """Give the one-time 30-credit AI welcome gift."""
        return await self.db.initialize_ai_credits(
            user_id,
            30
        )

    async def balance(
        self,
        user_id: int
    ) -> int:
        row = await self.db.get_ai_credits(user_id)

        return (
            int(row["balance"])
            if row
            else 0
        )

    async def consume(
        self,
        user_id: int,
        amount: int = 1,
        reason: str = "AI_USAGE"
    ) -> Optional[dict]:

        if amount <= 0:
            raise ValueError(
                "Credit amount must be greater than zero"
            )

        return await self.db.consume_ai_credit(
            user_id,
            amount,
            transaction_type=reason,
            metadata={
                "source": "private_ai"
            },
        )

    async def add(
        self,
        user_id: int,
        amount: int,
        reason: str,
        admin_id: int | None = None
    ) -> Optional[dict]:

        if amount <= 0:
            raise ValueError(
                "Credit amount must be greater than zero"
            )

        return await self.db.add_ai_credits(
            user_id,
            amount,
            transaction_type=reason,
            admin_id=admin_id,
            metadata={
                "source": "private_ai"
            },
        )

    async def admin_adjust(
        self,
        user_id: int,
        amount: int,
        admin_id: int
    ) -> Optional[dict]:
        """
        Add or deduct credits through an admin action.
        Positive amount = add.
        Negative amount = deduct.
        """

        if amount == 0:
            raise ValueError(
                "Adjustment cannot be zero"
            )

        if amount > 0:
            return await self.db.add_ai_credits(
                user_id=user_id,
                amount=amount,
                transaction_type="ADMIN_ADJUSTMENT",
                admin_id=admin_id,
                metadata={
                    "source": "admin_credit_command"
                }
            )

        return await self.db.deduct_ai_credits(
            user_id=user_id,
            amount=abs(amount),
            transaction_type="ADMIN_ADJUSTMENT",
            admin_id=admin_id,
            metadata={
                "source": "admin_credit_command"
            }
        )    

    # ========================================================
    # DAILY BONUS
    # ========================================================

    async def daily_bonus_amount(self) -> int:
        return random.choices(
            list(BONUS_WEIGHTS.keys()),
            weights=list(BONUS_WEIGHTS.values()),
            k=1,
        )[0]

    async def can_claim_bonus(
        self,
        user_id: int,
        now: datetime | None = None
    ) -> tuple[bool, Optional[datetime]]:

        last = await self.db.get_last_bonus_at(
            user_id
        )

        if last is None:
            return True, None

        now = now or datetime.now(timezone.utc)

        if last.tzinfo is None:
            last = last.replace(
                tzinfo=timezone.utc
            )

        elapsed = now - last

        return (
            elapsed.total_seconds()
            >= 24 * 60 * 60,
            last
        )

    async def claim_daily_bonus(
        self,
        user_id: int
    ) -> tuple[
        Optional[int],
        Optional[datetime]
    ]:

        amount = await self.daily_bonus_amount()

        result = await self.db.claim_ai_daily_bonus(
            user_id,
            amount
        )

        if result is None:

            last = await self.db.get_last_bonus_at(
                user_id
            )

            if last is None:
                return None, None

            if last.tzinfo is None:
                last = last.replace(
                    tzinfo=timezone.utc
                )

            next_time = (
                last + timedelta(hours=24)
            )

            return None, next_time

        last_bonus = result[
            "last_bonus_at"
        ]

        if last_bonus.tzinfo is None:
            last_bonus = last_bonus.replace(
                tzinfo=timezone.utc
            )

        next_time = (
            last_bonus
            + timedelta(hours=24)
        )

        return amount, next_time

    # ========================================================
    # AI SESSION METHODS
    # ========================================================

    async def get_active_session(
        self,
        user_id: int
    ) -> Optional[dict]:

        return await self.db.get_active_ai_session(
            user_id
        )

    async def start_session(
        self,
        user_id: int
    ) -> dict:

        # First check whether an active session already exists.
        existing = await self.get_active_session(
            user_id
        )

        if existing:
            return existing

        return await self.db.create_ai_session(
            user_id
        )

    async def touch_session(
        self,
        session_id: int
    ) -> bool:

        return await self.db.touch_ai_session(
            session_id
        )

    async def bill_session(
        self,
        session_id: int
    ) -> Optional[dict]:

        return await self.db.bill_ai_session(
            session_id,
            inactivity_minutes=AI_SESSION_INACTIVITY_MINUTES
        )

    async def close_session(
        self,
        session_id: int
    ) -> bool:

        return await self.db.close_ai_session(
            session_id
        )

    async def prepare_session(
        self,
        user_id: int
    ) -> dict:

        """
        Get/create the user's active AI session.

        A session that has already crossed the inactivity
        window is closed before a new message starts.
        Idle time is never carried into a new session.
        """

        session = await self.get_active_session(
            user_id
        )

        if not session:
            return await self.start_session(
                user_id
            )

        last_activity = session.get(
            "last_activity_at"
        )

        if last_activity is not None:

            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(
                    tzinfo=timezone.utc
                )

            now = datetime.now(
                timezone.utc
            )

            inactive_seconds = (
                now - last_activity
            ).total_seconds()

            if inactive_seconds >= (
                AI_SESSION_INACTIVITY_MINUTES * 60
            ):

                # Bill only activity that happened before
                # the user became inactive.
                await self.bill_session(
                    int(session["id"])
                )

                await self.close_session(
                    int(session["id"])
                )

                return await self.start_session(
                    user_id
                )

        # Bill completed active minutes accumulated so far.
        billing = await self.bill_session(
            int(session["id"])
        )

        if (
            billing
            and not billing.get(
                "session_active",
                True
            )
        ):
            # Credits were exhausted while billing.
            return session

        await self.touch_session(
            int(session["id"])
        )

        return (
            await self.get_active_session(
                user_id
            )
            or session
        )


__all__ = [
    "CreditService",
    "BONUS_WEIGHTS",
    "AI_SESSION_INACTIVITY_MINUTES",
]
