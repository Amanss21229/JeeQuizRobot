"""Private AI profile and long-term memory helpers."""

from __future__ import annotations

import re
from typing import Any, Optional


# These fields are intentionally NOT learned automatically.
# They may only be stored through explicit future consent flows.
SENSITIVE_PROFILE_FIELDS = {
    "age",
    "gender",
    "city",
}


# Topics that should never be silently converted into durable memory.
SENSITIVE_MEMORY_TERMS = {
    "religion",
    "caste",
    "political",
    "politics",
    "party",
    "vote",
    "voting",
    "sexual",
    "sex life",
    "medical",
    "disease",
    "diagnosis",
    "depression",
    "anxiety",
    "password",
    "otp",
    "pin",
    "aadhaar",
    "pan number",
    "bank account",
}


class MemoryService:
    """
    Durable personalization for Private AI.

    This service stores only small, useful facts.
    Full conversations are NOT stored in ai_profiles.
    Activity GC remains the conversation archive.
    """

    def __init__(self, db):
        self.db = db

    # ============================================================
    # BASIC PROFILE
    # ============================================================

    async def ensure_profile(
        self,
        user_id: int
    ) -> dict:
        return await self.db.create_ai_profile(
            user_id
        )

    async def get_profile(
        self,
        user_id: int
    ) -> Optional[dict]:
        return await self.db.get_ai_profile(
            user_id
        )

    async def update_profile(
        self,
        user_id: int,
        **fields: Any
    ) -> None:

        allowed = {
            "preferred_name",
            "age",
            "city",
            "gender",
            "study_class",
            "exam_target",
            "goals",
            "preferences",
            "memory_summary",
        }

        unknown = set(fields) - allowed

        if unknown:
            raise ValueError(
                f"Unsupported profile fields: "
                f"{sorted(unknown)}"
            )

        await self.db.update_ai_profile(
            user_id,
            **fields
        )

    # ============================================================
    # CONTEXT FOR GEMINI
    # ============================================================

    async def build_context(
        self,
        user_id: int
    ) -> str:

        profile = await self.db.get_ai_profile(
            user_id
        )

        if not profile:
            return "No saved personalization yet."

        labels = {
            "preferred_name": "User's preferred name",
            "study_class": "Current study class",
            "exam_target": "Current exam target",
            "goals": "User-stated goals",
            "preferences": "Response/study preferences",
            "memory_summary": "Other explicitly remembered facts",
        }

        parts = []

        for key, label in labels.items():

            value = profile.get(key)

            if value:
                parts.append(
                    f"{label}: {value}"
                )

        # Sensitive profile fields are only included if they
        # were explicitly stored through an approved flow.
        for key in (
            "age",
            "city",
            "gender"
        ):

            value = profile.get(key)

            if value is not None and value != "":
                parts.append(
                    f"{key.replace('_', ' ').title()}: "
                    f"{value}"
                )

        if not parts:
            return "No saved personalization yet."

        return "\n".join(parts)

    # ============================================================
    # MEMORY DISPLAY
    # ============================================================

    async def get_memory_text(
        self,
        user_id: int
    ) -> str:

        profile = await self.get_profile(
            user_id
        )

        if not profile:
            return "No saved memory yet."

        rows = []

        fields = (
            ("preferred_name", "Name"),
            ("study_class", "Class"),
            ("exam_target", "Exam target"),
            ("goals", "Goals"),
            ("preferences", "Preferences"),
            ("memory_summary", "Other useful facts"),
        )

        for key, label in fields:

            value = profile.get(key)

            if value:
                rows.append(
                    f"• {label}: {value}"
                )

        if not rows:
            return "No saved memory yet."

        return "\n".join(rows)

    # ============================================================
    # MEMORY LEARNING
    # ============================================================

    async def learn_from_message(
        self,
        user_id: int,
        text: str
    ) -> dict:
        """
        Learn small, durable and non-sensitive facts from a message.

        Returns the fields that were changed.
        """

        text = (text or "").strip()

        if not text:
            return {}

        if len(text) > 1500:
            return {}

        lowered = text.lower()

        # Never silently save sensitive information.
        if any(
            term in lowered
            for term in SENSITIVE_MEMORY_TERMS
        ):
            return {}

        updates = {}

        # --------------------------------------------------------
        # Preferred name
        # Examples:
        # "call me Aman"
        # "mujhe Aman bulao"
        # --------------------------------------------------------

        name_patterns = [
            r"\bmy name is\s+([A-Za-z][A-Za-z .'-]{1,30}?)(?=\s+(?:and|but|aur)\b|[.!?,]|$)",
            r"\bcall me\s+([A-Za-z][A-Za-z .'-]{1,30}?)(?=\s+(?:and|but|aur)\b|[.!?,]|$)",
            r"\bmujhe\s+([A-Za-z][A-Za-z .'-]{1,30}?)\s+bulao\b",
            r"\bmera naam\s+([A-Za-z][A-Za-z .'-]{1,30}?)(?:\s+hai)?(?=\s+(?:and|but|aur)\b|[.!?,]|$)",
        ]        

        for pattern in name_patterns:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE
            )

            if match:

                name = self._clean_value(
                    match.group(1),
                    40
                )

                if (
                    name
                    and self._valid_preferred_name(
                        name
                    )
                ):
                    updates[
                        "preferred_name"
                    ] = name

                break

        # --------------------------------------------------------
        # Exam target
        #
        # Examples:
        # "my target is JEE 2027"
        # "mera target JEE 2027 hai"
        # "I am preparing for JEE 2027"
        # --------------------------------------------------------

        target_patterns = [
            r"\bmy target is\s+([^.!?\n]{2,60}?)(?=\s+(?:and|but|aur)\b|[.!?]|$)",
            r"\bmera target\s+([^.!?\n]{2,60}?)(?:\s+hai)?(?=\s+(?:and|but|aur)\b|[.!?]|$)",
            r"\bi am preparing for\s+([^.!?\n]{2,60}?)(?=\s+(?:and|but|aur)\b|[.!?]|$)",
            r"\bi'm preparing for\s+([^.!?\n]{2,60}?)(?=\s+(?:and|but|aur)\b|[.!?]|$)",

            # Aspirant forms
            r"\bi am (?:a|an)\s+((?:jee|jee)(?:\s+\d{4})?)\s+aspirant\b",
            r"\bi'm (?:a|an)\s+((?:jee|jee)(?:\s+\d{4})?)\s+aspirant\b",
            r"\b(?:main|mai)\s+((?:jee|jee)(?:\s+\d{4})?)\s+aspirant\b",

            r"\bmai\s+([^.!?\n]{2,60}?)\s+ke liye prepare kar raha",
            r"\bmain\s+([^.!?\n]{2,60}?)\s+ke liye prepare kar raha",
        ]

        for pattern in target_patterns:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE
            )

            if match:

                target = self._clean_value(
                    match.group(1),
                    80
                )

                target = re.sub(
                    r"\s+hai$",
                    "",
                    target,
                    flags=re.IGNORECASE
                ).strip()

                if target:

                    target_upper = (
                        target.upper()
                    )

                    exam_match = re.search(
                        r"\b(JEE|JEE)"
                        r"(?:\s+(20\d{2}))?\b",
                        target_upper
                    )

                    if exam_match:

                        exam_name = (
                            exam_match.group(1)
                        )

                        exam_year = (
                            exam_match.group(2)
                        )

                        if exam_year:
                            target = (
                                f"{exam_name} "
                                f"{exam_year}"
                            )
                        else:
                            target = exam_name

                    updates[
                        "exam_target"
                    ] = target

                break

        # --------------------------------------------------------
        # Study class
        #
        # Examples:
        # "I am in class 12"
        # "main class 11 me hu"
        # --------------------------------------------------------

        class_patterns = [
            r"\bi am in class\s+([0-9]{1,2}(?:th|st|nd|rd)?)",
            r"\bi'm in class\s+([0-9]{1,2}(?:th|st|nd|rd)?)",
            r"\b(?:main|mai)\s+class\s+([0-9]{1,2}(?:th|st|nd|rd)?)\s+(?:me|mein)",
        ]

        for pattern in class_patterns:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE
            )

            if match:

                study_class = self._clean_value(
                    match.group(1),
                    30
                )

                if study_class:
                    updates["study_class"] = (
                        f"Class {study_class}"
                    )

                break

        # --------------------------------------------------------
        # Explicit goals
        #
        # We intentionally require goal-like wording here.
        # --------------------------------------------------------

        goal_patterns = [
            r"\bmy goal is\s+([^.!?\n]{3,120})",
            r"\bmera goal\s+([^.!?\n]{3,120}?)(?:\s+hai)?$",
            r"\bi want to achieve\s+([^.!?\n]{3,120})",
        ]

        for pattern in goal_patterns:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE
            )

            if match:

                goal = self._clean_value(
                    match.group(1),
                    160
                )

                goal = re.sub(
                    r"\s+hai$",
                    "",
                    goal,
                    flags=re.IGNORECASE
                ).strip()

                if goal:
                    updates["goals"] = goal

                break

        # --------------------------------------------------------
        # Learning / response preferences
        #
        # Examples:
        # "I prefer short explanations"
        # "mujhe hinglish me explain karna"
        # --------------------------------------------------------

        preference_patterns = [
            r"\bi prefer\s+([^.!?\n]{3,120})",
            r"\bmujhe\s+([^.!?\n]{3,120}?)\s+me explain karna",
            r"\bexplain to me in\s+([^.!?\n]{3,80})",
        ]

        for pattern in preference_patterns:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE
            )

            if match:

                preference = self._clean_value(
                    match.group(1),
                    160
                )

                if preference:
                    updates[
                        "preferences"
                    ] = preference

                break

        # --------------------------------------------------------
        # Explicit "remember" facts
        #
        # This is stored in memory_summary rather than guessing
        # a database field.
        # --------------------------------------------------------

        explicit_fact = self._extract_explicit_memory(
            text
        )

        if explicit_fact:

            profile = await self.get_profile(
                user_id
            )

            old_summary = ""

            if profile:
                old_summary = (
                    profile.get(
                        "memory_summary"
                    )
                    or ""
                )

            new_summary = self._append_memory_fact(
                old_summary,
                explicit_fact
            )

            updates[
                "memory_summary"
            ] = new_summary

        if updates:

            await self.update_profile(
                user_id,
                **updates
            )

        return updates

    # ============================================================
    # USER MEMORY CONTROLS
    # ============================================================

    async def forget_all(
        self,
        user_id: int
    ) -> None:

        await self.db.clear_ai_profile_memory(
            user_id
        )

    async def forget_field(
        self,
        user_id: int,
        field: str
    ) -> bool:

        aliases = {
            "name": "preferred_name",
            "preferred_name": "preferred_name",

            "class": "study_class",
            "study_class": "study_class",

            "age": "age",
            "city": "city",
            "gender": "gender",            

            "exam": "exam_target",
            "target": "exam_target",
            "exam_target": "exam_target",

            "goal": "goals",
            "goals": "goals",

            "preference": "preferences",
            "preferences": "preferences",

            "facts": "memory_summary",
            "memory": "memory_summary",
            "memory_summary": "memory_summary",
        }

        real_field = aliases.get(
            field.lower().strip()
        )

        if not real_field:
            return False

        await self.db.clear_ai_profile_field(
            user_id,
            real_field
        )

        return True

    # ============================================================
    # INTERNAL HELPERS
    # ============================================================

    @staticmethod
    def _valid_preferred_name(
        value: str
    ) -> bool:
        """
        Reject obvious sentences/descriptions accidentally
        captured as a person's preferred name.
        """

        value = (
            value
            or ""
        ).strip()

        if not value:
            return False

        words = value.split()

        if len(words) > 4:
            return False

        lowered = value.lower()

        rejected = {
            "tired",
            "sad",
            "happy",
            "fine",
            "good",
            "okay",
            "ok",
            "student",
            "aspirant",
            "jee aspirant",
            "jee aspirant",
            "a student",
            "an aspirant",
        }

        if lowered in rejected:
            return False

        if re.search(
            r"\b(?:jee|jee|class|student|aspirant|preparing)\b",
            lowered
        ):
            return False

        return bool(
            re.fullmatch(
                r"[A-Za-z][A-Za-z .'-]{1,39}",
                value
            )
        )    

    @staticmethod
    def _clean_value(
        value: str,
        max_length: int
    ) -> str:

        value = re.sub(
            r"\s+",
            " ",
            value or ""
        ).strip(" .,-")

        return value[:max_length].strip()

    def _extract_explicit_memory(
        self,
        text: str
    ) -> Optional[str]:

        patterns = [
            r"\bremember that\s+(.+)",
            r"\bremember\s+(.+)",
            r"\byaad rakhna ki\s+(.+)",
            r"\byaad rakhna\s+(.+)",
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE
            )

            if not match:
                continue

            fact = self._clean_value(
                match.group(1),
                220
            )

            lowered = fact.lower()

            if any(
                term in lowered
                for term in SENSITIVE_MEMORY_TERMS
            ):
                return None

            return fact or None

        return None

    @staticmethod
    def _append_memory_fact(
        current: str,
        new_fact: str
    ) -> str:

        facts = [
            item.strip()
            for item in (
                current or ""
            ).split(" | ")
            if item.strip()
        ]

        # Avoid exact duplicate memories.
        if not any(
            item.lower() == new_fact.lower()
            for item in facts
        ):
            facts.append(
                new_fact
            )

        # Keep memory intentionally compact.
        facts = facts[-8:]

        summary = " | ".join(
            facts
        )

        return summary[:1200]


__all__ = [
    "MemoryService",
    "SENSITIVE_PROFILE_FIELDS",
]
