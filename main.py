"""Entrypoint script for the Revrag Voice AI agent.

Usage
-----
Development (auto-reload, console mode)::

    python main.py dev

Production / remote worker::

    python main.py start

Connect to a specific LiveKit server::

    python main.py start \\
        --url wss://your-project.livekit.cloud \\
        --api-key  <LIVEKIT_API_KEY> \\
        --api-secret <LIVEKIT_API_SECRET>

All LiveKit credentials and the OpenAI API key can also be supplied via
environment variables (see ``.env.example``).
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv

# Load .env file before importing agent so env-vars are available during
# plugin initialisation.
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from revrag_voice_ai.agent import entrypoint  # noqa: E402

from livekit.agents import WorkerOptions, cli  # noqa: E402

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
