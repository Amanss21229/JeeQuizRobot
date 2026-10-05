"""Safety gate for student-facing private AI conversations."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SafetyResult:
    allowed: bool
    category: str = "normal"
    note: str = ""


class SafetyService:
    """Lightweight pre/post-processing safety layer.

    This is not a substitute for provider safety filters. It prevents the bot
    from deliberately entering disallowed modes and flags high-risk messages
    for safer handling.
    """

    def classify(self, text: str) -> SafetyResult:
        normalized = (text or "").lower().strip()

        # Avoid detailed self-harm content. The integration layer should respond
        # supportively and encourage reaching a trusted adult/local emergency help.
        self_harm_patterns = [
            r"\bkill myself\b",
            r"\bend my life\b",
            r"\bsuicide\b",
            r"\bself[- ]?harm\b",
        ]
        if any(re.search(p, normalized) for p in self_harm_patterns):
            return SafetyResult(False, "high_risk", "Use a supportive safety response; do not provide methods or concealment advice.")

        # Explicit sexual-content generation is not an appropriate chat mode,
        # while factual sexual-health education can still be handled safely.
        explicit_patterns = [
            r"\bporn\b",
            r"\bsex scene\b",
            r"\berotic\b",
            r"\bnudes?\b",
        ]
        if any(re.search(p, normalized) for p in explicit_patterns):
            return SafetyResult(False, "explicit_sexual", "Keep the response educational and age-appropriate; do not generate sexual content.")

        return SafetyResult(True)

    def system_constraints(self) -> str:
        return (
            "Be friendly, supportive and age-appropriate. Never generate explicit sexual content, "
            "instructions for dangerous/illegal activity, or methods/details for self-harm. "
            "For sensitive health questions, provide factual educational information and encourage "
            "a trusted adult or qualified professional when appropriate. Do not claim certainty about outcomes."
        )


__all__ = ["SafetyService", "SafetyResult"]
