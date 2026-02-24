# Revrag Voice AI

A Python LiveKit voice agent that joins a LiveKit room, listens to the
remote participant's microphone, echoes back their speech ("You said: …"),
and handles interruptions and silence gracefully.

---

## Table of Contents

- [Architecture](#architecture)
- [Requirements](#requirements)
- [Setup](#setup)
- [Run](#run)
- [Environment Variables](#environment-variables)
- [SDKs & Services](#sdks--services)
- [How Overlap / Interruption Prevention Works](#how-overlap--interruption-prevention-works)
- [How Silence Handling Works](#how-silence-handling-works)
- [Limitations](#limitations)

---

## Architecture

```
remote mic audio
     │
     ▼
[VAD – Silero]          ← on-device voice-activity detection (no API key)
     │
     ▼
[STT – OpenAI Whisper]  ← speech → text
     │
     ▼
[EchoAgent.llm_node]    ← returns "You said: <text>" (no external LLM call)
     │
     ▼
[TTS – OpenAI TTS-1]    ← text → audio frames
     │
     ▼
LiveKit room audio track (published back to the room)
```

---

## Requirements

- Python 3.10+
- A [LiveKit Cloud](https://cloud.livekit.io/) project **or** a self-hosted
  LiveKit server
- An [OpenAI API key](https://platform.openai.com/api-keys) (used for
  Whisper STT and TTS-1)

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

# 4. Configure environment variables
cp .env.example .env
# Edit .env and fill in LIVEKIT_URL, LIVEKIT_API_KEY,
# LIVEKIT_API_SECRET, and OPENAI_API_KEY
```

---

## Run

### Production mode

```bash
python -m revrag_voice_ai.main start \
  --url        "$LIVEKIT_URL" \
  --api-key    "$LIVEKIT_API_KEY" \
  --api-secret "$LIVEKIT_API_SECRET"
```

The agent worker registers itself with LiveKit and waits for room
assignments.  When a participant joins any room dispatched to this worker
the agent automatically joins, greets the user, and starts the
echo pipeline.

### Development / console mode

```bash
python -m revrag_voice_ai.main dev
```

Connects once in development mode with hot-reload support.

### Direct module execution

```bash
PYTHONPATH=src python src/revrag_voice_ai/main.py start
```

---

## Environment Variables

| Variable             | Required | Description                                               |
|----------------------|----------|-----------------------------------------------------------|
| `LIVEKIT_URL`        | ✅       | WebSocket URL of your LiveKit project (`wss://…`)         |
| `LIVEKIT_API_KEY`    | ✅       | LiveKit API key                                           |
| `LIVEKIT_API_SECRET` | ✅       | LiveKit API secret                                        |
| `OPENAI_API_KEY`     | ✅       | OpenAI API key (Whisper STT + TTS-1)                      |

Copy `.env.example` to `.env` and fill in all four values.

---

## SDKs & Services

| Component              | Package                   | Service / Model             |
|------------------------|---------------------------|-----------------------------|
| Core framework         | `livekit-agents` ≥ 1.0    | LiveKit Agents Python SDK   |
| Voice-activity detect. | `livekit-plugins-silero`  | Silero VAD (on-device)      |
| Speech-to-text         | `livekit-plugins-openai`  | OpenAI Whisper              |
| Text-to-speech         | `livekit-plugins-openai`  | OpenAI TTS-1                |
| Environment loading    | `python-dotenv`           | —                           |

No LLM API call is made: `EchoAgent.llm_node` returns the echo string
directly, bypassing the LLM pipeline node.

---

## How Overlap / Interruption Prevention Works

The `AgentSession` is created with:

```python
allow_interruptions=True,
min_interruption_duration=0.5,  # seconds of speech before firing
min_interruption_words=0,       # any speech triggers interruption
```

**Flow when the user speaks while the agent is talking:**

1. Silero VAD detects the start of user speech.
2. After `min_interruption_duration` (0.5 s) of confirmed speech the
   framework calls `AgentActivity.interrupt()` internally.
3. The in-progress TTS stream is **immediately cancelled** and the
   partially-played audio track is stopped.
4. The agent transitions to **"listening"** state.
5. The agent will not speak again until the user has finished their new
   utterance and the STT has produced a final transcript.

The agent therefore **never speaks over the user**.

---

## How Silence Handling Works

`AgentSession` accepts a `user_away_timeout` parameter (set to **20 s**).

**Flow after 20 s of silence:**

1. After both the user and the agent have been in "listening" state for
   20 continuous seconds, the framework transitions `user_state` to
   `"away"` and emits a `user_state_changed` event.
2. Our event handler detects `new_state == "away"` and calls
   `session.say(SILENCE_REMINDER)` **once**.
3. The agent starts speaking → agent state → `"speaking"` → the away
   timer is **cancelled** by the framework.
4. After the reminder finishes, both sides return to "listening" and the
   20-second timer **restarts from zero**.
5. The reminder will fire again only after another full 20 s of silence,
   and not while the agent is already speaking.

---

## Limitations

- **Single participant**: the agent is designed for one-on-one
  conversations.  In multi-participant rooms only the first remote
  participant's audio is processed.
- **OpenAI API costs**: every utterance incurs Whisper STT and TTS-1
  charges.  The echo response is constructed locally so no LLM cost is
  incurred.
- **Silero VAD model download**: on first run the Silero VAD model
  (~1 MB) is downloaded automatically to the local cache.
- **No persistent history**: the chat context is reset when the agent
  disconnects.  There is no long-term memory or conversation storage.
- **Audio only**: video tracks are not subscribed to or published.
