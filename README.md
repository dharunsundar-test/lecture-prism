# Lecture Capture System

A complete lecture capture and notes system with three components:

- **Client** (React Native / Android) - Records lectures in 5-min chunks, pairs with PC via QR code, auto-uploads on LAN
- **Host** (FastAPI + SQLite) - Receives chunks, processes with faster-whisper + local LLM classification, generates notes
- **Viewer** (React + Vite) - Synced audio/notes playback with click-to-seek

## Architecture

```
┌─────────────┐     LAN HTTP      ┌─────────────┐
│   Phone     │ ─────────────────► │    PC       │
│  (Android)  │  chunk upload     │  (FastAPI)  │
└─────────────┘                   └─────────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
             ┌──────────┐         ┌────────────┐       ┌──────────┐
             │  faster- │         │  Ollama    │       │  SQLite  │
             │ whisper  │         │  (llama3)  │       │  (meta)  │
             └──────────┘         └────────────┘       └──────────┘
                    │                    │
                    ▼                    ▼
             ┌──────────────────────────────────────┐
             │        Notes Generation              │
             │  (local llama3.1:8b OR Groq cloud)   │
             └──────────────────────────────────────┘
                    │
                    ▼
             ┌─────────────┐
             │   Viewer    │
             │  (React)    │
             └─────────────┘
```

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 20+
- Android Studio (for client)
- ffmpeg (for audio processing)
- Ollama (for local LLM) - `ollama pull llama3.1:8b`

### 1. Host Server
```bash
cd host
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000
# Server runs on http://localhost:8000 (0.0.0.0 so the phone can reach it)
# API docs at http://localhost:8000/docs
# Tests: pip install pytest httpx && python -m pytest
```
`ffmpeg`/`ffprobe` must be on PATH: they are used for noise reduction, chunk durations and
joining chunks into the full-session audio the viewer plays.

### 2. Viewer (Desktop)
```bash
cd viewer
npm install
npm run dev
# Runs on http://localhost:5173
```

### 3. Client (Android)
```bash
cd client
npm install
npx react-native run-android
```
Recording runs in an Android foreground service (`android/.../recorder/RecorderService.kt`), so it
continues with the screen off.

On iOS the recorder is a local native module (`modules/lecture-capture-ios`, linked by
`react-native.config.js`); run `cd ios && pod install` before `npx react-native run-ios`. It uses the
`audio` background mode to keep recording with the screen locked, and stops (saving what it has) if a
call interrupts. QR scanning isn't available on iOS yet: pair with the manual IP/port/token fields.

## QR Pairing
Open http://localhost:8000/api/pair on the PC (or **Pair phone** in the viewer). It shows a QR code with:
```json
{"ips": ["192.168.1.23", "10.42.0.1"], "port": 8000, "token": "..."}
```
Scan it with **Pair PC** in the app (uses Google's code scanner, so Google Play services are required;
the page also shows the values for manual entry). The phone tries each IP, so pairing keeps working
when the PC moves between Wi-Fi and a hotspot, and sends the token as `X-Pair-Token` on every request.
The token lives in `host/data/pair_token.txt`; delete it to revoke a paired phone.

Recordings stay queued on the phone and upload when the PC is reachable. If the phone never connects,
allow inbound TCP 8000 for Python in Windows Defender Firewall.

## Processing Pipeline
Per chunk, as soon as it arrives:
1. **Noise Reduction** - ffmpeg highpass/lowpass + anlmdn
2. **Transcription** - faster-whisper (GPU: large-v3, CPU: medium), saved to `data/transcripts/`
   with a loudness (dBFS) value per segment

Per session, once the phone has finalized it and every chunk is transcribed:

3. **Speaker Roles** - segments are split into two loudness levels; the level with the most speaking
   time is the lecturer, so it works whether the phone is near the lecturer or the students. Falls back
   to a timing heuristic when there's only one level.
4. **Classification** - the whole session transcript is classified by the local Ollama model in ~3-minute
   windows (with 30 s of context), so topics that cross a chunk boundary stay together. Each span gets a
   category and a one-line summary:
   - `concept` - Definitions, explanations
   - `example` - Worked problems, demos
   - `announcement` - Quiz/exam dates, deadlines
   - `qa` - Questions & answers
   - `action_item` - Assignments, tasks
   - `filler` - Silence, irrelevant

   Deadlines are resolved against the lecture date ("next Tuesday" -> an absolute date).
5. **Notes Generation** - On demand from the viewer, per-category documents (local or Groq). Every bullet
   carries a `[m:ss]` timestamp, so the viewer highlights the note being played and seeks when clicked.

If a step fails (e.g. Ollama isn't running), the session shows the error and a **Retry processing** button.

## Configuration

### Host (`host/.env`)
```
HOST=0.0.0.0
PORT=8000
DATA_DIR=./data
OLLAMA_URL=http://localhost:11434
CLASSIFICATION_MODEL=llama3.1:8b
NOTES_MODEL=llama3.1:8b
WHISPER_MODEL=auto                     # auto = large-v3 with CUDA, medium on CPU
GROQ_API_KEY=your_key_here             # Optional, for cloud notes
GROQ_MODEL=llama-3.3-70b-versatile     # Check Groq's model list if this is retired
```

### Client
- Chunk duration: 5 minutes (configurable in `src/types/index.ts`)
- Retry: exponential backoff capped at 5 minutes, retried until the PC is reachable
- Ping interval: 30 seconds

## Project Structure
```
Lecture_Prism/
├── client/                 # React Native Android app
│   ├── android/           # Native Android project
│   └── src/
│       ├── screens/       # RecordingScreen, QRPairingScreen
│       ├── services/      # audioRecorder, transfer
│       ├── storage/       # AsyncStorage + RNFS
│       └── types/         # TypeScript interfaces
├── host/                  # FastAPI server
│   └── app/
│       ├── api/           # Routes (upload, sessions, segments, notes, audio)
│       ├── core/          # Config
│       ├── db/            # SQLite operations
│       └── services/      # Processing pipeline, worker
└── viewer/                # React + Vite desktop app
    └── src/
        ├── components/    # SessionList, SessionView
        ├── api/           # Axios client
        └── types/         # Shared interfaces
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
Requests from other machines need `X-Pair-Token`; requests from the PC itself (the viewer) don't.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (no token) |
| GET | `/api/ping` | Token check used by the phone |
| GET | `/api/pair` | Pairing QR page (PC only) |
| POST | `/api/chunks/upload` | Upload audio chunk (idempotent; optional `sha256`) |
| POST | `/api/sessions/{id}/finalize` | Phone stopped recording: sets end time and expected chunk count |
| GET | `/api/sessions` | List all sessions |
| GET | `/api/sessions/{id}` | Session detail (status, analysis status, error) |
| POST | `/api/sessions/{id}/reprocess` | Retry failed chunks/analysis; finalizes a session the phone never finalized |
| GET | `/api/sessions/{id}/segments` | Classified segments (session-relative times) |
| GET | `/api/sessions/{id}/audio/{seq}` | Chunk audio file |
| GET | `/api/sessions/{id}/audio/full` | Full session audio (chunks joined with ffmpeg) |
| GET | `/api/sessions/{id}/notes` | Saved notes, with `items` (lines and their `start_ms`) |
| POST | `/api/sessions/{id}/notes` | Generate and save notes (`{"model": "local" \| "groq"}`) |

## Development Notes

- **Minimalist UI**: Dark theme, no heavy component libraries
- **Offline-first**: Phone queues chunks, retries on reconnect
- **Resumable**: 5-min chunks allow recovery from interruption
- **Single binary deploy**: Host is one FastAPI app with embedded worker