"""
Lab 11 - Helper Utilities
"""
import asyncio
import os

from dotenv import load_dotenv
from google.genai import types


load_dotenv()


def get_google_api_keys() -> list[str]:
    """Return configured Google API keys in rotation order."""
    keys = [
        key.strip()
        for key in os.environ.get("GOOGLE_API_KEYS", "").split(",")
        if key.strip()
    ]
    primary = os.environ.get("GOOGLE_API_KEY", "").strip()
    if primary and primary not in keys:
        keys.insert(0, primary)
    return keys


def rotate_google_api_key() -> str | None:
    """Rotate GOOGLE_API_KEY to the next configured key."""
    keys = get_google_api_keys()
    if not keys:
        return None
    current = os.environ.get("GOOGLE_API_KEY", "").strip()
    try:
        next_index = (keys.index(current) + 1) % len(keys)
    except ValueError:
        next_index = 0
    os.environ["GOOGLE_API_KEY"] = keys[next_index]
    return keys[next_index]


def is_rate_limit_error(error: Exception) -> bool:
    """Detect Gemini/ADK quota errors without importing private ADK classes."""
    text = f"{type(error).__name__}: {error}".lower()
    return any(
        marker in text
        for marker in (
            "429",
            "resource_exhausted",
            "quota",
            "rate limit",
            "ratelimit",
        )
    )


async def chat_with_agent(agent, runner, user_message: str, session_id=None):
    """Send a message to the agent and get the response.

    Retries Gemini rate-limit errors and rotates GOOGLE_API_KEY when
    GOOGLE_API_KEYS contains multiple comma-separated keys.
    """
    max_retries = int(os.environ.get("GOOGLE_API_MAX_RETRIES", "3") or "3")
    delay_seconds = float(os.environ.get("GOOGLE_API_RETRY_DELAY_SECONDS", "2") or "2")
    rotation_enabled = os.environ.get("GOOGLE_API_KEY_ROTATION_ENABLED", "1") != "0"
    attempts = max(1, max_retries + 1)
    last_error = None

    for attempt in range(attempts):
        try:
            return await _chat_once(agent, runner, user_message, session_id=session_id)
        except Exception as e:
            last_error = e
            if not is_rate_limit_error(e) or attempt == attempts - 1:
                raise
            if rotation_enabled:
                rotated = rotate_google_api_key()
                if rotated:
                    print(
                        f"Rate limit hit; rotated GOOGLE_API_KEY "
                        f"({attempt + 1}/{attempts})."
                    )
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

    raise last_error


async def _chat_once(agent, runner, user_message: str, session_id=None):
    """Single ADK chat attempt."""
    user_id = "student"
    app_name = runner.app_name

    session = None
    if session_id is not None:
        try:
            session = await runner.session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
        except (ValueError, KeyError):
            pass

    if session is None:
        try:
            session = await runner.session_service.create_session(
                app_name=app_name, user_id=user_id
            )
        except Exception:
            session = await runner.session_service.create_session(
                app_name=app_name, user_id=user_id
            )

    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_message)],
    )

    final_response = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session.id, new_message=content
    ):
        if hasattr(event, "content") and event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    final_response += part.text

    return final_response, session
