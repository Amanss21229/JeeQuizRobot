"""Gemini API provider for Private AI."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Sequence

from google import genai
from google.genai import types
from google.genai.errors import APIError

from .chat import ChatMessage


logger = logging.getLogger(__name__)


class GeminiProvider:
    """
    Gemini provider with transient-error recovery and model fallback.

    Strategy:
    1. Try the primary model.
    2. Retry temporary failures once with a short backoff.
    3. If the primary model remains overloaded/unavailable,
       try fallback models.
    4. Do not hide permanent configuration/auth errors.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.8-flash",
    ):
        self.client = genai.Client(
            api_key=api_key
        )

        self.model = model

        # Primary model always goes first.
        # Duplicates are removed automatically.
        candidates = [
            model,
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
        ]

        self.models = list(
            dict.fromkeys(candidates)
        )

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        system_prompt: str,
    ) -> str:

        contents = [
            types.Content(
                role=(
                    "user"
                    if message.role == "user"
                    else "model"
                ),
                parts=[
                    types.Part(
                        text=message.content
                    )
                ],
            )
            for message in messages
        ]

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=1200,
        )

        last_error = None

        for model_name in self.models:

            # One normal request + one application-level retry.
            for attempt in range(2):

                try:

                    logger.info(
                        "Private AI Gemini request: "
                        "model=%s attempt=%s",
                        model_name,
                        attempt + 1,
                    )

                    response = await asyncio.wait_for(
                        self.client.aio.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=config,
                        ),
                        timeout=45
                    )

                    text = getattr(
                        response,
                        "text",
                        None
                    )

                    if not text:
                        raise RuntimeError(
                            "Gemini returned an empty response"
                        )

                    logger.info(
                        "Private AI Gemini success: model=%s",
                        model_name,
                    )

                    return text.strip()

                except asyncio.TimeoutError as exc:

                    last_error = exc

                    logger.warning(
                        "Gemini request timed out: "
                        "model=%s attempt=%s",
                        model_name,
                        attempt + 1
                    )

                    if attempt == 0:

                        await asyncio.sleep(
                            1.0
                        )

                        continue

                    break                

                except APIError as exc:

                    last_error = exc

                    status_code = getattr(
                        exc,
                        "code",
                        None
                    )

                    if status_code is None:
                        status_code = getattr(
                            exc,
                            "status_code",
                            None
                        )

                    try:
                        status_code = int(
                            status_code
                        )
                    except (
                        TypeError,
                        ValueError
                    ):
                        status_code = None

                    # Only these failures are considered temporary.
                    transient = (
                        status_code == 408
                        or status_code == 429
                        or (
                            status_code is not None
                            and 500 <= status_code <= 599
                        )
                    )

                    if not transient:
                        logger.exception(
                            "Non-transient Gemini API error "
                            "using model %s",
                            model_name,
                        )
                        raise

                    logger.warning(
                        "Temporary Gemini failure: "
                        "model=%s status=%s attempt=%s",
                        model_name,
                        status_code,
                        attempt + 1,
                    )

                    # Retry same model once.
                    if attempt == 0:

                        delay = (
                            1.0
                            + random.uniform(
                                0.1,
                                0.5
                            )
                        )

                        await asyncio.sleep(
                            delay
                        )

                        continue

                    # Same model failed again.
                    # Continue outer loop → fallback model.
                    logger.warning(
                        "Switching from overloaded model %s "
                        "to fallback model.",
                        model_name,
                    )

                    break

                except RuntimeError as exc:

                    # Empty/invalid model response.
                    last_error = exc

                    logger.warning(
                        "Gemini model %s returned no usable "
                        "response. Trying fallback.",
                        model_name,
                    )

                    break

                except Exception:

                    # Programming errors, malformed requests,
                    # authentication/configuration issues etc.
                    # should remain visible rather than being
                    # incorrectly disguised as capacity problems.
                    logger.exception(
                        "Unexpected Gemini provider error "
                        "using model %s",
                        model_name,
                    )

                    raise

        # Every available model failed.
        if last_error:
            raise last_error

        raise RuntimeError(
            "No Gemini model was able to generate a response"
        )


__all__ = ["GeminiProvider"]
