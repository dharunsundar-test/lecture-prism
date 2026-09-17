import AsyncStorage from '@react-native-async-storage/async-storage';
import { Course, RecordingSession, Chunk, PCConfig, TransferQueueItem } from '../types';
import * as RNFS from 'react-native-fs';

const KEYS = {
  COURSES: '@lecture_capture:courses',
  SESSIONS: '@lecture_capture:sessions',
  CHUNKS: '@lecture_capture:chunks',
  PC_CONFIG: '@lecture_capture:pc_config',
  TRANSFER_QUEUE: '@lecture_capture:transfer_queue',
} as const;

const DEFAULT_COURSES: Course[] = [
  { id: 'cs', name: 'Control Systems', color: '#2563EB' },
  { id: 'ml', name: 'Machine Learning', color: '#7C3AED' },
  { id: 'ds', name: 'Data Structures', color: '#059669' },
  { id: 'os', name: 'Operating Systems', color: '#DC2626' },
];

// The recorder and the uploader both read-modify-write the same lists. Serialising those
// updates stops one from overwriting the other's change.
let writeLock: Promise<unknown> = Promise.resolve();

function withLock<T>(fn: () => Promise<T>): Promise<T> {
  const run = writeLock.then(fn, fn);
  writeLock = run.catch(() => undefined);
  return run;
}

async function readJson<T>(key: string, fallback: T): Promise<T> {
  try {
    const data = await AsyncStorage.getItem(key);
    return data ? JSON.parse(data) : fallback;
  } catch {
    return fallback;
  }
}

function queueKey(item: TransferQueueItem): string {
  return item.kind === 'chunk' ? `chunk:${item.chunkId}` : `finalize:${item.sessionId}`;
}

interface ChunkSidecar {
  sessionId: string;
  seq: number;
  path: string;
  startedAt: number;
  endedAt: number;
  sizeBytes: number;
}

export const storage = {
  async getCourses(): Promise<Course[]> {
    return readJson(KEYS.COURSES, DEFAULT_COURSES);
  },

  async saveCourses(courses: Course[]): Promise<void> {
    await AsyncStorage.setItem(KEYS.COURSES, JSON.stringify(courses));
  },

  async getSessions(): Promise<RecordingSession[]> {
    return readJson(KEYS.SESSIONS, []);
  },

  upsertSession(session: RecordingSession): Promise<void> {
    return withLock(async () => {
      const sessions = await this.getSessions();
      await AsyncStorage.setItem(
        KEYS.SESSIONS,
        JSON.stringify([session, ...sessions.filter((s) => s.id !== session.id)]),
      );
    });
  },

  async getChunks(): Promise<Chunk[]> {
    return readJson(KEYS.CHUNKS, []);
  },

  /** Adds a chunk unless one with the same id exists. Returns whether it was added. */
  addChunk(chunk: Chunk): Promise<boolean> {
    return withLock(async () => {
      const chunks = await this.getChunks();
      if (chunks.some((c) => c.id === chunk.id)) {
        return false;
      }
      await AsyncStorage.setItem(KEYS.CHUNKS, JSON.stringify([chunk, ...chunks]));
      return true;
    });
  },

  updateChunk(chunkId: string, patch: Partial<Chunk>): Promise<void> {
    return withLock(async () => {
      const chunks = await this.getChunks();
      await AsyncStorage.setItem(
        KEYS.CHUNKS,
        JSON.stringify(chunks.map((c) => (c.id === chunkId ? { ...c, ...patch } : c))),
      );
    });
  },

  async getPCConfig(): Promise<PCConfig | null> {
    const data = await readJson<(Partial<PCConfig> & { ip?: string }) | null>(KEYS.PC_CONFIG, null);
    if (!data) {
      return null;
    }
    // Configs saved before multi-IP pairing stored a single `ip` and no token.
    return {
      ips: data.ips ?? (data.ip ? [data.ip] : []),
      port: data.port ?? 8000,
      token: data.token ?? '',
      pairedAt: data.pairedAt ?? 0,
      lastReachableIp: data.lastReachableIp,
    };
  },

  async savePCConfig(config: PCConfig): Promise<void> {
    await AsyncStorage.setItem(KEYS.PC_CONFIG, JSON.stringify(config));
  },

  async clearPCConfig(): Promise<void> {
    await AsyncStorage.removeItem(KEYS.PC_CONFIG);
  },

  async getTransferQueue(): Promise<TransferQueueItem[]> {
    const items = await readJson<any[]>(KEYS.TRANSFER_QUEUE, []);
    // Items queued by the first version had no kind and were always chunks.
    return items.map((item) => ({ nextAttemptAt: 0, ...item, kind: item.kind ?? 'chunk' }));
  },

  updateTransferQueue(fn: (queue: TransferQueueItem[]) => TransferQueueItem[]): Promise<void> {
    return withLock(async () => {
      const queue = await this.getTransferQueue();
      await AsyncStorage.setItem(KEYS.TRANSFER_QUEUE, JSON.stringify(fn(queue)));
    });
  },

  enqueueTransfer(item: TransferQueueItem): Promise<void> {
    const key = queueKey(item);
    return this.updateTransferQueue((queue) =>
      queue.some((q) => queueKey(q) === key) ? queue : [...queue, item],
    );
  },

  removeFromTransferQueue(item: TransferQueueItem): Promise<void> {
    const key = queueKey(item);
    return this.updateTransferQueue((queue) => queue.filter((q) => queueKey(q) !== key));
  },

  async getAudioDir(): Promise<string> {
    const dir = `${RNFS.DocumentDirectoryPath}/LectureCapture/audio`;
    const exists = await RNFS.exists(dir);
    if (!exists) {
      await RNFS.mkdir(dir);
    }
    return dir;
  },

  async getSessionDir(sessionId: string): Promise<string> {
    const base = await this.getAudioDir();
    const dir = `${base}/${sessionId}`;
    const exists = await RNFS.exists(dir);
    if (!exists) {
      await RNFS.mkdir(dir);
    }
    return dir;
  },

  /**
   * Finds chunks the native recorder finished (it writes a JSON sidecar per chunk) that never
   * reached storage, e.g. because the app was killed while recording in the background.
   */
  async findUnrecordedChunks(): Promise<Chunk[]> {
    const known = new Set((await this.getChunks()).map((c) => c.id));
    const found: Chunk[] = [];
    const sessionDirs = (await RNFS.readDir(await this.getAudioDir())).filter((d) => d.isDirectory());

    for (const dir of sessionDirs) {
      const sidecars = (await RNFS.readDir(dir.path)).filter((f) => f.isFile() && f.name.endsWith('.json'));
      for (const file of sidecars) {
        try {
          const meta: ChunkSidecar = JSON.parse(await RNFS.readFile(file.path, 'utf8'));
          const chunk = this.chunkFromRecording(meta);
          if (!known.has(chunk.id) && (await RNFS.exists(chunk.audioPath))) {
            found.push(chunk);
          }
        } catch {
          // Unreadable sidecar: the audio file is still on disk, just not recoverable automatically.
        }
      }
    }
    return found;
  },

  generateSessionId(): string {
    return `session_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
  },

  generateChunkId(sessionId: string, seq: number): string {
    return `${sessionId}_chunk_${seq}`;
  },

  chunkFromRecording(meta: ChunkSidecar): Chunk {
    return {
      id: this.generateChunkId(meta.sessionId, meta.seq),
      sessionId: meta.sessionId,
      seq: meta.seq,
      audioPath: meta.path,
      duration: meta.endedAt - meta.startedAt,
      size: meta.sizeBytes,
      status: 'pending',
      startedAt: meta.startedAt,
      endedAt: meta.endedAt,
      retryCount: 0,
    };
  },

  createSessionMetadata(courseId: string): RecordingSession {
    return {
      id: this.generateSessionId(),
      courseId,
      startedAt: Date.now(),
      status: 'recording',
    };
  },
};
