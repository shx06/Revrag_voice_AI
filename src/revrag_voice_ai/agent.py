"""RevRAG Voice AI — agent logic.

Architecture
------------
The agent is built on the LiveKit Agents Python SDK (v1.x) and uses the
standard ``AgentSession`` / ``Agent`` pipeline:

  remote mic audio
       │
       ▼
  [VAD – Silero]          ← detects when the user starts / stops speaking
       │
       ▼
  [STT – OpenAI Whisper]  ← converts user audio frames to text
       │
       ▼
  [llm_node (EchoAgent)]  ← returns "You said: <text>" (no external LLM needed)
       │
       ▼
  [TTS – OpenAI TTS-1]    ← synthesises the response text to audio frames
       │
       ▼
  LiveKit room audio track (published by the framework)

Overlap / Interruption handling (Requirement 2A)
------------------------------------------------
``AgentSession`` is created with ``allow_interruptions=True`` (explicit).
Internally, when the VAD detects the user speaking while the agent's TTS
is playing, the framework calls ``AgentActivity.interrupt()``, which
immediately cancels the in-progress TTS stream and transitions the agent
back to the "listening" state.  Two tunable knobs control sensitivity:

* ``min_interruption_duration`` (seconds of continuous speech before
  triggering) — set to 0.5 s so brief noises do not cut the agent off.
* ``min_interruption_words`` (minimum words detected) — set to 0 so any
  confirmed speech immediately triggers the interruption.

The agent will **never** speak over the user because the framework halts
audio forwarding to the TTS output as soon as the interruption fires.

Silence handling (Requirement 2B)
----------------------------------
``AgentSession`` has a built-in ``user_away_timeout`` parameter.  When
both the user and the agent have been in "listening" state for that many
seconds, the framework transitions ``user_state`` to ``"away"``.

We subscribe to the ``"user_state_changed"`` event.  When
``new_state == "away"`` we call ``session.say(SILENCE_REMINDER)`` exactly
once.  As soon as the agent starts speaking, the away timer is cancelled
by the framework.  It only restarts once *both* sides return to
"listening", ensuring the reminder fires at most once per idle period and
resets automatically on new user speech.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from livekit.agents import Agent, AgentSession, AutoSubscribe, JobContext, llm
from livekit.agents.voice.events import UserStateChangedEvent
from livekit.plugins import openai as lk_openai
from livekit.plugins import silero

logger = logging.getLogger(__name__)

# How long (seconds) the room must be silent before the reminder fires.
SILENCE_TIMEOUT_S: float = 20.0

# The single reminder sentence played after prolonged silence.
SILENCE_REMINDER: str = "I'm still here whenever you're ready to speak."


class EchoAgent(Agent):
    """A LiveKit voice agent that echoes user speech back as 'You said: <text>'.

    The ``llm_node`` pipeline node is overridden to produce the echo response
    locally — no external LLM API call is made.
    """

    def __init__(self) -> None:
        super().__init__(
            # ``instructions`` is required by the base class.  It is not used
            # here because we bypass the LLM entirely via llm_node, but it
            # serves as documentation for the agent's purpose.
            instructions=(
                "You are a simple echo assistant. "
                "Repeat back exactly what the user says, prefixed with 'You said: '."
            ),
            # Allow the user to interrupt the agent at any time.
            allow_interruptions=True,
        )

    async def on_enter(self) -> None:
        """Send a greeting when the agent first enters the room."""
        session = self._get_activity_or_raise().session
        session.say(
            "Hello! I'm listening. Say something and I'll repeat it back to you.",
            allow_interruptions=False,
        )

    async def llm_node(  # type: ignore[override]
        self,
        chat_ctx: llm.ChatContext,
        tools: list[Any],
        model_settings: Any,
    ) -> str:
        """Override the LLM pipeline node to echo the last user message.

        Returns a plain ``str``; the framework automatically feeds it through
        ``tts_node`` to synthesise speech.  No external LLM service is called.
        """
        user_msgs = [
            m
            for m in chat_ctx.items
            if isinstance(m, llm.ChatMessage) and m.role == "user"
        ]

        if not user_msgs:
            return "I didn't catch that. Could you repeat yourself?"

        user_text = (user_msgs[-1].text_content or "").strip()
        if not user_text:
            return "I didn't catch that. Could you repeat yourself?"

        response = f"You said: {user_text}"
        logger.info("Echo response: %r", response)
        return response


async def entrypoint(ctx: JobContext) -> None:
    """LiveKit Agents entrypoint — called once per room by the worker process.

    Sets up an ``AgentSession`` with VAD, STT, and TTS, wires up the silence
    reminder, starts the agent, and waits until the room session ends.
    """
    logger.info("Agent connecting to room %r", ctx.room.name)

    # AUDIO_ONLY → subscribe only to microphone audio tracks (no video).
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    session = AgentSession(
        # Silero VAD: lightweight on-device voice-activity detection.
        # Required for STT streaming and for interruption detection.
        vad=silero.VAD.load(),
        # OpenAI Whisper for speech-to-text.
        stt=lk_openai.STT(),
        # OpenAI TTS-1 for text-to-speech synthesis.
        tts=lk_openai.TTS(),
        # No ``llm`` argument — EchoAgent.llm_node handles response generation.
        #
        # --- Interruption settings (Requirement 2A) ---
        allow_interruptions=True,
        min_interruption_duration=0.5,   # seconds of speech before interrupting
        min_interruption_words=0,        # any detected speech triggers interruption
        #
        # --- Silence / away timer (Requirement 2B) ---
        # After SILENCE_TIMEOUT_S of mutual silence, user_state → "away".
        user_away_timeout=SILENCE_TIMEOUT_S,
    )

    # --- Silence reminder (Requirement 2B) ---
    # The callback MUST be synchronous (livekit-agents restriction).
    # session.say() is non-async; it schedules speech and returns a SpeechHandle.
    @session.on("user_state_changed")
    def _on_user_state_changed(ev: UserStateChangedEvent) -> None:
        if ev.new_state == "away":
            logger.info(
                "User silent for %.0f s — playing reminder", SILENCE_TIMEOUT_S
            )
            session.say(SILENCE_REMINDER, allow_interruptions=True)

    session.start(EchoAgent(), room=ctx.room)
    logger.info("Agent started in room %r", ctx.room.name)

    try:
        # The worker framework will cancel this coroutine when the job ends.
        await asyncio.sleep(float("inf"))
    except asyncio.CancelledError:
        logger.info("Agent task cancelled — shutting down gracefully")
    finally:
        await session.aclose()
        logger.info("Agent disconnected from room %r", ctx.room.name)
