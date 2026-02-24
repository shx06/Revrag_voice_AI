# Revrag Voice AI

A LiveKit voice agent built with the **LiveKit Agents Python SDK (v1.x)**.  
The agent joins a LiveKit room, listens to participants, transcribes their speech, and echoes it back as synthesised audio — all in real time.

---

## Table of Contents

- [Features](#features)
- [Project structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Environment variables](#environment-variables)
- [Running the agent](#running-the-agent)
- [How overlap prevention works](#how-overlap-prevention-works)
- [How silence handling works](#how-silence-handling-works)
- [SDK and external services](#sdk-and-external-services)
- [Known limitations](#known-limitations)

---

## Features

| Behaviour | Description |
|---|---|
| **Echo response** | Transcribes user speech and replies *"You said: \<text\>"* via TTS |
| **No-overlap / interruption handling** | Agent speech is cancelled immediately when the user starts speaking |
| **Silence reminder** | Plays *"I'm here when you're ready."* after 20 s of user inactivity (once per idle window) |
| **Graceful shutdown** | Handles `SIGINT` / `SIGTERM`; cleans up room connection |
| **Structured logging** | `INFO`-level logs for all significant events |

---

## Project structure

```
.
├── main.py                         # CLI entrypoint — run this to start the agent
├── src/
│   └── revrag_voice_ai/
│       ├── __init__.py
│       └── agent.py                # EchoAgent, entrypoint, silence logic
├── requirements.txt                # Pinned runtime dependencies
├── pyproject.toml                  # PEP 517/518 build metadata
├── .env.example                    # Required environment variables (copy → .env)
└── README.md
```

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | ≥ 3.10 |
| LiveKit server | Cloud or self-hosted (free tier available at [livekit.io](https://livekit.io)) |
| OpenAI account | Free-tier / pay-as-you-go API key |

---

## Setup

```bash
# 1. Clone the repository
git clone https://github.com/shx06/Revrag_voice_AI.git
cd Revrag_voice_AI

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the example env file and fill in your credentials
cp .env.example .env
# Edit .env with your LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET,
# and OPENAI_API_KEY
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `LIVEKIT_URL` | ✅ | WebSocket URL of your LiveKit server, e.g. `wss://your-project.livekit.cloud` |
| `LIVEKIT_API_KEY` | ✅ | LiveKit project API key (from your dashboard) |
| `LIVEKIT_API_SECRET` | ✅ | LiveKit project API secret |
| `OPENAI_API_KEY` | ✅ | OpenAI API key — used for both Whisper STT and TTS |

All variables can be placed in a `.env` file in the project root (loaded automatically by `python-dotenv`).

---

## Running the agent

### Development mode (with console + auto-reload)

```bash
python main.py dev
```

This starts the agent in *console mode*, letting you type text directly to test the pipeline without a real microphone.

### Production / remote worker

```bash
python main.py start
```

Or with explicit credentials:

```bash
python main.py start \
  --url wss://your-project.livekit.cloud \
  --api-key  YOUR_LIVEKIT_API_KEY \
  --api-secret YOUR_LIVEKIT_API_SECRET
```

Once the worker is running it registers itself with the LiveKit server and waits for room-dispatch events.  Connect a client (browser, phone app, etc.) to the same LiveKit project and the agent will automatically join the room and begin listening.

---

## How overlap prevention works

> **TL;DR** — the SDK handles it natively; no custom state machine is required.

`AgentSession` is constructed with `allow_interruptions=True` (the default).  
Internally the session runs a continuous VAD (Silero) stream over the incoming participant audio.  When a *start-of-speech* event is detected while the agent is actively playing back audio:

1. The current `SpeechHandle` is marked as *interrupted*.
2. The TTS generator task is cancelled.
3. The audio source is flushed so playback stops immediately.

The `min_interruption_duration=0.3` parameter (300 ms) acts as a debounce filter — very short noise spikes do not cancel speech prematurely.

```
User:   ──────────── speaking ────────────────────────────
Agent:  ───────── speaking ──✂ (interrupted) ─────────────
                              ↑
                       VAD detects user speech;
                       SpeechHandle.interrupted = True
```

---

## How silence handling works

`AgentSession` accepts a `user_away_timeout` parameter (set to **20 seconds** here).  After that many consecutive seconds without detected user speech, the session transitions the user state from `"listening"` to `"away"` and emits a `user_state_changed` event.

The `_on_user_state_changed` handler in `entrypoint`:

1. Checks `ev.new_state == "away"` and the `_reminder_sent` guard flag.
2. If not already sent, calls `session.say(SILENCE_REMINDER_TEXT)` — a single, non-blocking TTS playback.
3. Sets `_reminder_sent = True` to prevent the reminder from looping.
4. Resets `_reminder_sent = False` when `ev.new_state == "speaking"` so the reminder can fire again in the *next* idle window.

```
Timeline:
  t=0  User stops speaking
  t=20 user_state → "away"  → reminder plays once
  t=25 User speaks again    → _reminder_sent reset
  t=50 User stops again
  t=70 user_state → "away"  → reminder plays again (new idle window)
```

---

## SDK and external services

| Component | Provider | Package | Free tier |
|---|---|---|---|
| Agent framework | LiveKit | `livekit-agents` | ✅ (server free tier available) |
| STT | OpenAI Whisper | `livekit-plugins-openai` | Pay-as-you-go (~$0.006 / min) |
| TTS | OpenAI TTS | `livekit-plugins-openai` | Pay-as-you-go (~$0.015 / 1k chars) |
| VAD | Silero | `livekit-plugins-silero` | ✅ (runs locally, no API key) |

---

## Known limitations

- **Echo only** — the agent mirrors user speech verbatim.  There is no conversational LLM; the `llm_node` override returns a plain string without calling any language model.
- **Single participant** — the agent connects to the first remote participant.  Multi-participant rooms are not explicitly handled.
- **OpenAI dependency** — both STT and TTS require an `OPENAI_API_KEY`.  Replacing either with a different provider (e.g. Deepgram for STT, ElevenLabs for TTS) requires swapping the plugin in `agent.py`.
- **No persistent storage** — conversation history is held in memory for the duration of the session only.
- **Network latency** — round-trip latency depends on the OpenAI API response time and your network.  Under normal conditions expect < 1 s end-to-end latency.
