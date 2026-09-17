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
uvicorn app.main:app --reload
# Server runs on http://localhost:8000
# API docs at http://localhost:8000/docs
```

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
# App shows QR code scanner - scan the code from viewer/host pairing screen
```

## QR Pairing
The viewer/host shows a QR code with:
```json
{"ip": "192.168.1.xxx", "port": 8000}
```
Scan once - the phone stores the IP and auto-uploads chunks when PC is reachable.

## Processing Pipeline
1. **Noise Reduction** - ffmpeg highpass/lowpass + anlmdn
2. **Transcription** - faster-whisper (GPU: large-v3, CPU: medium)
3. **Speaker Detection** - Heuristic (long segments = lecturer)
4. **Classification** - 6 categories via Ollama/Groq:
   - `concept` - Definitions, explanations
   - `example` - Worked problems, demos
   - `announcement` - Quiz/exam dates, deadlines
   - `qa` - Questions & answers
   - `action_item` - Assignments, tasks
   - `filler` - Silence, irrelevant
5. **Notes Generation** - Per-category documents (local or Groq)

## Configuration

### Host (.env)
```
HOST=0.0.0.0
PORT=8000
DATA_DIR=./data
OLLAMA_URL=http://localhost:11434
CLASSIFICATION_MODEL=llama3.1:8b
NOTES_MODEL=llama3.1:8b
GROQ_API_KEY=your_key_here  # Optional, for cloud notes
```

### Client
- Chunk duration: 5 minutes (configurable in `src/types/index.ts`)
- Retry: Exponential backoff, max 5 attempts
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
| GET | `/health` | Health check |
| POST | `/api/chunks/upload` | Upload audio chunk |
| GET | `/api/sessions` | List all sessions |
| GET | `/api/sessions/{id}` | Session detail |
| GET | `/api/sessions/{id}/segments` | Classified segments |
| GET | `/api/sessions/{id}/audio/{seq}` | Chunk audio file |
| GET | `/api/sessions/{id}/audio/full` | Full session audio |
| POST | `/api/sessions/{id}/notes` | Generate notes |

## Development Notes

- **Minimalist UI**: Dark theme, no heavy component libraries
- **Offline-first**: Phone queues chunks, retries on reconnect
- **Resumable**: 5-min chunks allow recovery from interruption
- **Single binary deploy**: Host is one FastAPI app with embedded worker