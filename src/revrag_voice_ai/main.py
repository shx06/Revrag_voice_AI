"""RevRAG Voice AI — CLI entrypoint.

Usage
-----
Start the agent worker (connects to LiveKit and awaits room assignments)::

    python -m revrag_voice_ai.main start

Or via the installed script::

    revrag-voice-ai start

Environment variables (see .env.example):
    LIVEKIT_URL        — wss:// URL of your LiveKit Cloud project or server
    LIVEKIT_API_KEY    — LiveKit API key
    LIVEKIT_API_SECRET — LiveKit API secret
    OPENAI_API_KEY     — OpenAI API key (used for Whisper STT and TTS-1)
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from livekit.agents import WorkerOptions, cli

from .agent import entrypoint

# Load .env before any other imports that read env vars.
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    """Run the LiveKit Agents CLI (``start`` / ``dev`` / ``connect`` sub-commands)."""
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    main()
