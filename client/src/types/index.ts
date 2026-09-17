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
  /** Every LAN address the PC reported; its IP differs between Wi-Fi and a phone hotspot. */
  ips: string[];
  port: number;
  token: string;
  pairedAt: number;
  lastReachableIp?: string;
}

interface QueueItemBase {
  sessionId: string;
  attempts: number;
  nextAttemptAt: number;
  lastError?: string;
}

export type TransferQueueItem =
  | (QueueItemBase & { kind: 'chunk'; chunkId: string })
  | (QueueItemBase & { kind: 'finalize'; endedAt: number; chunkCount: number });

export type ConnectionState = 'unpaired' | 'searching' | 'connected' | 'unreachable' | 'unauthorized';

export const CHUNK_DURATION_MS = 5 * 60 * 1000;
export const PING_INTERVAL_MS = 30000;
export const RETRY_BASE_DELAY_MS = 5000;
export const RETRY_MAX_DELAY_MS = 5 * 60 * 1000;
