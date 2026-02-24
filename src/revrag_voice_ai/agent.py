"""Revrag Voice AI — LiveKit voice agent.

Core behaviours
---------------
* Joins a LiveKit room and subscribes to remote participant audio.
* Uses OpenAI Whisper (STT) to transcribe user speech.
* Echoes back ``"You said: <text>"`` without an LLM round-trip by
  overriding :meth:`EchoAgent.llm_node`.
* Uses OpenAI TTS to synthesise the response and publish it to the room.

Advanced behaviours
-------------------
A) No-overlap / interruption handling
   ``AgentSession`` is constructed with ``allow_interruptions=True``
   (the SDK default).  The session monitors the VAD stream; when the
   user begins speaking while the agent is talking the current speech
   handle is interrupted and playback stops immediately.  No custom
   state-machine is required — the SDK handles it natively via
   ``min_interruption_duration``.

B) Silence handling
   ``AgentSession`` tracks inactivity through its ``user_away_timeout``
   parameter.  After ``SILENCE_TIMEOUT_SECS`` of silence the user
   state transitions from ``"listening"`` to ``"away"`` and a
   ``user_state_changed`` event is emitted.  The handler calls
   ``session.say()`` exactly once per idle window (guarded by
   ``_reminder_sent``).  The flag is cleared when the user next speaks
   so the reminder can fire again in future idle windows.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable
from typing import Any

from livekit.agents import JobContext, WorkerOptions, cli, llm
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.events import UserStateChangedEvent
from livekit.plugins import openai as openai_plugin
from livekit.plugins import silero

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

#: Seconds of user silence before the idle-reminder is played.
SILENCE_TIMEOUT_SECS: float = 20.0

#: Text spoken to the user after an idle period.
SILENCE_REMINDER_TEXT: str = "I'm here when you're ready."


# ---------------------------------------------------------------------------
# Echo agent — no LLM call; mirrors the user's last utterance
# ---------------------------------------------------------------------------


class EchoAgent(Agent):
    """Voice agent that echoes back whatever the user said.

    The echo is implemented by overriding :meth:`llm_node`, which is the
    text-generation stage of the voice pipeline.  Returning a plain
    :class:`str` from this coroutine bypasses any external LLM entirely.
    """

    def __init__(self) -> None:
        super().__init__(
            # instructions are unused by our llm_node override but are required
            # by the base class and serve as documentation for future maintainers.
            instructions=(
                "You are a simple echo agent. "
                "Respond with exactly 'You said: ' followed by what the user said."
            ),
        )

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: Any,
    ) -> str:
        """Return an echo of the user's last message.

        Overriding this method means the session never calls an external LLM,
        so no ``OPENAI_API_KEY`` is consumed for this stage.  The returned
        string is handed directly to the TTS node.
        """
        last_user_text = ""
        for msg in reversed(chat_ctx.messages()):
            if msg.role == "user":
                last_user_text = msg.text_content or ""
                break

        response = f"You said: {last_user_text}"
        logger.info("Echo response → %s", response)
        return response


# ---------------------------------------------------------------------------
# Job entrypoint
# ---------------------------------------------------------------------------


async def entrypoint(ctx: JobContext) -> None:
    """Main entrypoint invoked by the LiveKit Workers framework per job.

    How overlap / interruption prevention works
    -------------------------------------------
    ``AgentSession`` is constructed with ``allow_interruptions=True`` (the
    default).  Internally the session's VAD stream continuously monitors the
    incoming audio track.  Whenever a *start-of-speech* event is detected
    while an ``AgentActivity`` is producing audio, the active
    :class:`~livekit.agents.voice.SpeechHandle` is interrupted: the TTS
    generator is cancelled and the audio source is flushed.  The
    ``min_interruption_duration`` parameter debounces very short noise spikes
    so that brief sounds don't cancel speech prematurely.

    How silence handling works
    --------------------------
    ``user_away_timeout=SILENCE_TIMEOUT_SECS`` tells the session to
    transition the user state to ``"away"`` after that many seconds without
    detected speech.  A ``user_state_changed`` event is emitted at that
    point; the handler below calls :meth:`AgentSession.say` once to play the
    reminder.  ``_reminder_sent`` is a boolean flag that prevents the
    reminder from being repeated during the same idle window.  When the user
    starts speaking again the state transitions back to ``"speaking"`` and
    the flag is cleared so a future idle window can trigger a new reminder.
    """
    logger.info("Agent starting — room: %s", ctx.room.name)
    await ctx.connect()

    # Build the voice pipeline.
    session = AgentSession(
        stt=openai_plugin.STT(),   # OpenAI Whisper for transcription
        tts=openai_plugin.TTS(),   # OpenAI TTS for synthesis
        vad=silero.VAD.load(),     # Silero VAD for voice-activity detection

        # Interruption handling: if the user starts speaking while the agent
        # is talking, abort agent playback after this many seconds of speech.
        allow_interruptions=True,
        min_interruption_duration=0.3,

        # Silence handling: after this many seconds without user speech the
        # user state transitions to "away" and the reminder is triggered.
        user_away_timeout=SILENCE_TIMEOUT_SECS,
    )

    # Guard so the reminder fires only once per idle window.
    _reminder_sent: bool = False

    @session.on("user_state_changed")
    def _on_user_state_changed(ev: UserStateChangedEvent) -> None:
        nonlocal _reminder_sent

        if ev.new_state == "away" and not _reminder_sent:
            # User has been silent for SILENCE_TIMEOUT_SECS — play reminder once.
            _reminder_sent = True
            logger.info(
                "User silent for %.0f s — playing idle reminder", SILENCE_TIMEOUT_SECS
            )
            # session.say() is non-blocking; it schedules TTS playback and
            # returns a SpeechHandle immediately.
            session.say(SILENCE_REMINDER_TEXT, allow_interruptions=True)

        elif ev.new_state == "speaking":
            # User spoke again — reset so the reminder can fire in the next
            # idle window.
            _reminder_sent = False

    # Start the session — this connects RoomIO, subscribes to participant
    # audio, and begins the STT→echo→TTS pipeline asynchronously.
    await session.start(EchoAgent(), room=ctx.room)

    logger.info("Agent running. Waiting for room disconnect…")
    try:
        # Keep this coroutine alive until the process is asked to shut down.
        await asyncio.sleep(float("inf"))
    except asyncio.CancelledError:
        logger.info("Entrypoint cancelled — shutting down cleanly")


# ---------------------------------------------------------------------------
# Worker bootstrap (used when this module is run directly)
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the LiveKit worker process."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    main()
