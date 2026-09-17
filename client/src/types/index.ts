export interface Course {
  id: string;
  name: string;
  color: string;
}

export interface RecordingSession {
  id: string;
  courseId: string;
  startedAt: number;
  endedAt?: number;
  duration?: number;
  status: 'recording' | 'paused' | 'completed' | 'failed';
  chunks: Chunk[];
}

export interface Chunk {
  id: string;
  sessionId: string;
  seq: number;
  audioPath: string;
  duration: number;
  size: number;
  status: 'pending' | 'uploading' | 'synced' | 'failed';
  startedAt: number;
  endedAt: number;
  error?: string;
  retryCount: number;
}

export interface PCConfig {
  ip: string;
  port: number;
  pairedAt: number;
}

export interface TransferQueueItem {
  chunkId: string;
  sessionId: string;
  filePath: string;
  attempts: number;
  lastAttempt?: number;
}

export const CHUNK_DURATION_MS = 5 * 60 * 1000;
export const MAX_RETRIES = 5;
export const RETRY_BASE_DELAY_MS = 30000;
export const PING_INTERVAL_MS = 30000;