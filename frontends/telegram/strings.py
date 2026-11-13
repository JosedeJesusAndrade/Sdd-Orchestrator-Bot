"""Telegram UI copy for the core ``ChatView`` contract.

The core formats these templates; the Telegram frontend owns the wording. The
values come from ``locales.es`` so the Telegram experience is byte-identical to
before the copy was removed from ``PromptService``.
"""

from __future__ import annotations

from locales import es
from services.chat_view import CoreStrings

TELEGRAM_STRINGS = CoreStrings(
    processing=es.PROMPT_PROCESSING,
    processing_elapsed=es.PROMPT_PROCESSING_ELAPSED,
    cancelled=es.PROMPT_CANCELLED,
    completed=es.PROMPT_COMPLETED,
    completed_no_output=es.PROMPT_COMPLETED_NO_OUTPUT,
    error=es.PROMPT_ERROR,
    error_code=es.PROMPT_ERROR_CODE,
    error_code_no_output=es.PROMPT_ERROR_CODE_NO_OUTPUT,
    delivery_failed=es.PROMPT_DELIVERY_FAILED,
    permission_not_accepted=es.PERMISSION_NOT_ACCEPTED,
    permission_not_accepted_notice=es.PERMISSION_NOT_ACCEPTED_NOTICE,
    new_session=es.PROMPT_NEW_SESSION,
)
