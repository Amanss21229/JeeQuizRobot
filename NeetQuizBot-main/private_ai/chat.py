"""Provider-independent chat orchestration for Private AI.

The actual model provider is deliberately not hard-coded in Phase 2. The next
integration phase can plug in a free-tier provider without changing the bot's
credit/memory/safety interfaces.
"""

from __future__ import annotations

import html
import re

from dataclasses import dataclass
from typing import Protocol, Sequence

from .credits import CreditService
from .memory import MemoryService
from .prompts import build_system_prompt
from .safety import SafetyService
from .tasks import TaskService


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class ChatResult:
    text: str
    credits_used: int = 0
    safety_category: str = "normal"


class AIProvider(Protocol):
    async def generate(self, messages: Sequence[ChatMessage], system_prompt: str) -> str:
        """Generate one assistant response."""


class PrivateAI:
    """Coordinates safety, memory, provider call and credit accounting."""

    def __init__(self, db, provider: AIProvider):
        self.db = db
        self.provider = provider
        self.credits = CreditService(db)
        self.memory = MemoryService(db)
        self.safety = SafetyService()
        self.tasks = TaskService(db)

    async def prepare_user(self, user_id: int) -> bool:
        """Initialize profile and one-time credits for a user."""
        await self.memory.ensure_profile(user_id)
        return await self.credits.initialize_for_user(user_id)

    @staticmethod
    def _clean_history_text(
        text: str,
        max_chars: int = 2500
    ) -> str:
        """
        Keep short-term context useful without allowing one very
        large old message to dominate the next Gemini request.
        """

        text = (text or "").strip()

        if not text:
            return ""

        if len(text) <= max_chars:
            return text

        return (
            text[:max_chars].rstrip()
            + "\n[Earlier message shortened]"
        )

    def _prepare_history(
        self,
        history: Sequence[ChatMessage]
    ) -> list[ChatMessage]:
        """
        Build compact recent conversation context.

        Rules:
        - only user/model messages
        - ignore empty entries
        - keep recent turns
        - cap individual message size
        - cap total context size
        """

        cleaned: list[ChatMessage] = []

        for message in history:

            if message.role not in {
                "user",
                "model"
            }:
                continue

            content = self._clean_history_text(
                message.content
            )

            if not content:
                continue

            cleaned.append(
                ChatMessage(
                    role=message.role,
                    content=content
                )
            )

        # Maximum 10 previous messages = roughly 5 turns.
        cleaned = cleaned[-10:]

        # Additional total-character guard.
        max_total_chars = 12000

        selected: list[ChatMessage] = []
        total_chars = 0

        for message in reversed(cleaned):

            message_size = len(
                message.content
            )

            if (
                selected
                and total_chars + message_size
                > max_total_chars
            ):
                break

            selected.append(
                message
            )

            total_chars += message_size

        selected.reverse()

        return selected    

    async def respond(
        self,
        user_id: int,
        user_text: str,
        history: Sequence[ChatMessage] = ()
    ) -> ChatResult:

        user_text = (
            user_text
            or ""
        ).strip()

        if not user_text:
            return ChatResult(
                text="Send me a message and I'll help you.",
                credits_used=0,
                safety_category="normal",
            )

        # ----------------------------------------------------
        # SAFETY
        # ----------------------------------------------------

        safety = self.safety.classify(
            user_text
        )

        if not safety.allowed:
            return ChatResult(
                text=self._safe_response(
                    safety.category
                ),
                credits_used=0,
                safety_category=safety.category,
            )

        # ----------------------------------------------------
        # LONG-TERM MEMORY
        # ----------------------------------------------------

        memory_context = (
            await self.memory.build_context(
                user_id
            )
        )

        system_prompt = build_system_prompt(
            memory_context=memory_context,
            safety_constraints=(
                self.safety.system_constraints()
            ),
        )

        # ----------------------------------------------------
        # SHORT-TERM CONVERSATION CONTEXT
        # ----------------------------------------------------

        prepared_history = (
            self._prepare_history(
                history
            )
        )

        messages = (
            prepared_history
            + [
                ChatMessage(
                    role="user",
                    content=user_text
                )
            ]
        )

        # ----------------------------------------------------
        # MODEL
        # ----------------------------------------------------

        text = await self.provider.generate(
            messages,
            system_prompt
        )

        text = (
            text
            or ""
        ).strip()

        text = self.clean_telegram_html(
            text
        )        

        if not text:
            text = (
                "I couldn't generate a useful response "
                "for that. Please try again."
            )

        return ChatResult(
            text=text,
            credits_used=0,
            safety_category="normal"
        )

    @staticmethod
    def clean_telegram_html(
        text: str
    ) -> str:
        """
        Keep Gemini output compatible with Telegram HTML.

        Removes unsupported tags while preserving Telegram-safe
        presentation tags.
        """

        text = (text or "").strip()

        if not text:
            return ""

        # Remove accidental Markdown fenced-code markers.
        text = re.sub(
            r"```(?:html)?\s*",
            "",
            text,
            flags=re.IGNORECASE
        )

        text = text.replace(
            "```",
            ""
        )

        # Remove Markdown heading markers.
        text = re.sub(
            r"(?m)^\s*#{1,6}\s+",
            "",
            text
        )

        # Convert common Markdown bold leftovers.
        text = re.sub(
            r"\*\*(.+?)\*\*",
            r"<b>\1</b>",
            text,
            flags=re.DOTALL
        )

        # Remove common LaTeX display delimiters.
        text = text.replace(
            "$$",
            ""
        )

        text = re.sub(
            r"(?<!\\)\$(.+?)(?<!\\)\$",
            r"\1",
            text,
            flags=re.DOTALL
        )

        # Remove common \text{...} wrapper.
        text = re.sub(
            r"\\text\{([^{}]*)\}",
            r"\1",
            text
        )

        # Only permit Telegram-supported tags generated by
        # our system prompt.
        allowed_tags = {
            "b",
            "strong",
            "i",
            "em",
            "u",
            "ins",
            "s",
            "strike",
            "del",
            "code",
            "pre",
            "blockquote",
        }

        def sanitize_tag(match):

            full_tag = match.group(0)

            tag_match = re.match(
                r"</?\s*([a-zA-Z0-9]+)",
                full_tag
            )

            if not tag_match:
                return ""

            tag_name = (
                tag_match
                .group(1)
                .lower()
            )

            if tag_name not in allowed_tags:
                return ""

            # Strip attributes entirely.
            closing = full_tag.startswith(
                "</"
            )

            if closing:
                return f"</{tag_name}>"

            return f"<{tag_name}>"

        text = re.sub(
            r"</?[^>]+>",
            sanitize_tag,
            text
        )

        # Prevent excessive blank-space output.
        text = re.sub(
            r"\n{4,}",
            "\n\n\n",
            text
        )

        return text.strip()

    @staticmethod
    def _safe_response(category: str) -> str:
        if category == "high_risk":
            return ("I’m here with you, but I can’t provide instructions for harming yourself. "
                    "Please tell a trusted adult or someone you trust right now and stay with them.")
        if category == "explicit_sexual":
            return ("I can help with factual, age-appropriate health or biology questions, "
                    "but I can’t generate explicit sexual content.")
        return "I can help with that in a safe, age-appropriate way."


__all__ = ["PrivateAI", "AIProvider", "ChatMessage", "ChatResult"]
