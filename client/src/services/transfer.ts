import axios from 'axios';
import NetInfo from '@react-native-community/netinfo';
import RNFS from 'react-native-fs';
import { storage } from '../storage';
import {
  Chunk,
  ConnectionState,
  PCConfig,
  RecordingSession,
  TransferQueueItem,
  PING_INTERVAL_MS,
  RETRY_BASE_DELAY_MS,
  RETRY_MAX_DELAY_MS,
} from '../types';

export interface TransferProgress {
  chunkId: string;
  progress: number;
  status: 'pending' | 'uploading' | 'synced' | 'failed';
  error?: string;
}

type ProgressCallback = (progress: TransferProgress) => void;
type ConnectionCallback = (state: ConnectionState, ip: string | null) => void;

export class PairingError extends Error {
  constructor(message: string, readonly reason: 'unauthorized' | 'unreachable') {
    super(message);
  }
}

function toFileUri(path: string): string {
  return path.startsWith('file://') ? path : `file://${path}`;
}

function describeError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail;
    if (error.response) {
      return `HTTP ${error.response.status}${detail ? `: ${JSON.stringify(detail)}` : ''}`;
    }
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

class TransferService {
  private progressCallbacks: Set<ProgressCallback> = new Set();
  private connectionCallbacks: Set<ConnectionCallback> = new Set();
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private unsubscribeNetInfo: (() => void) | null = null;
  private processingQueue = false;
  private pcConfig: PCConfig | null = null;
  private activeIp: string | null = null;
  private connection: ConnectionState = 'unpaired';

  subscribe(cb: ProgressCallback): () => void {
    this.progressCallbacks.add(cb);
    return () => this.progressCallbacks.delete(cb);
  }

  subscribeConnection(cb: ConnectionCallback): () => void {
    this.connectionCallbacks.add(cb);
    cb(this.connection, this.activeIp);
    return () => this.connectionCallbacks.delete(cb);
  }

  private notify(progress: TransferProgress): void {
    this.progressCallbacks.forEach((cb) => cb(progress));
  }

  private setConnection(state: ConnectionState, ip: string | null): void {
    this.connection = state;
    this.activeIp = state === 'connected' ? ip : null;
    this.connectionCallbacks.forEach((cb) => cb(state, this.activeIp));
  }

  async initialize(): Promise<void> {
    this.pcConfig = await storage.getPCConfig();
    if (!this.unsubscribeNetInfo) {
      // Joining a different Wi-Fi or hotspot usually means the PC has a different address.
      this.unsubscribeNetInfo = NetInfo.addEventListener(() => {
        if (this.pcConfig) {
          this.pingAndProcess();
        }
      });
    }
    if (this.pcConfig) {
      this.startPingLoop();
    }
  }

  async setPCConfig(config: PCConfig): Promise<void> {
    this.pcConfig = config;
    await storage.savePCConfig(config);
    this.startPingLoop();
  }

  async clearPCConfig(): Promise<void> {
    this.pcConfig = null;
    await storage.clearPCConfig();
    this.stopPingLoop();
    this.setConnection('unpaired', null);
  }

  getPCConfig(): PCConfig | null {
    return this.pcConfig;
  }

  isPaired(): boolean {
    return this.pcConfig !== null;
  }

  /**
   * Returns the first of the config's addresses that answers with this token.
   * Throws PairingError when none do, so pairing screens can explain why.
   */
  async findReachableIp(config: PCConfig): Promise<string> {
    const candidates = [config.lastReachableIp, ...config.ips].filter(
      (ip, i, all): ip is string => Boolean(ip) && all.indexOf(ip) === i,
    );
    let tokenRejected = false;

    for (const ip of candidates) {
      try {
        await axios.get(`http://${ip}:${config.port}/api/ping`, {
          timeout: 4000,
          headers: { 'X-Pair-Token': config.token },
        });
        return ip;
      } catch (error) {
        if (axios.isAxiosError(error) && error.response?.status === 401) {
          tokenRejected = true;
        }
      }
    }

    if (tokenRejected) {
      throw new PairingError('The PC rejected the pair token. Scan the QR code again.', 'unauthorized');
    }
    throw new PairingError(
      `Could not reach the PC at ${candidates.join(', ') || 'any address'} on port ${config.port}.`,
      'unreachable',
    );
  }

  private startPingLoop(): void {
    this.stopPingLoop();
    this.pingTimer = setInterval(() => this.pingAndProcess(), PING_INTERVAL_MS);
    this.pingAndProcess();
  }

  private stopPingLoop(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private async pingAndProcess(): Promise<void> {
    const config = this.pcConfig;
    if (!config) {
      return;
    }
    if (this.connection !== 'connected') {
      this.setConnection('searching', null);
    }

    try {
      const ip = await this.findReachableIp(config);
      this.setConnection('connected', ip);
      if (config.lastReachableIp !== ip) {
        this.pcConfig = { ...config, lastReachableIp: ip };
        await storage.savePCConfig(this.pcConfig);
      }
      await this.processQueue();
    } catch (error) {
      this.setConnection(error instanceof PairingError ? error.reason : 'unreachable', null);
    }
  }

  async enqueueChunk(chunk: Chunk): Promise<void> {
    await storage.enqueueTransfer({
      kind: 'chunk',
      chunkId: chunk.id,
      sessionId: chunk.sessionId,
      attempts: 0,
      nextAttemptAt: 0,
    });
    this.processQueue();
  }

  async enqueueFinalize(session: RecordingSession, chunkCount: number): Promise<void> {
    await storage.enqueueTransfer({
      kind: 'finalize',
      sessionId: session.id,
      endedAt: session.endedAt ?? Date.now(),
      chunkCount,
      attempts: 0,
      nextAttemptAt: 0,
    });
    this.processQueue();
  }

  private async processQueue(): Promise<void> {
    if (this.processingQueue || !this.pcConfig || !this.activeIp) {
      return;
    }
    this.processingQueue = true;

    try {
      const tried = new Set<string>();
      for (;;) {
        const now = Date.now();
        const queue = await storage.getTransferQueue();
        const item = queue.find((q) => q.nextAttemptAt <= now && !tried.has(this.itemKey(q)));
        if (!item) {
          break;
        }
        tried.add(this.itemKey(item));

        const ok = await this.send(item);
        if (!ok && this.connection !== 'connected') {
          // Lost the PC; the next successful ping resumes the queue.
          break;
        }
      }
    } finally {
      this.processingQueue = false;
    }
  }

  private itemKey(item: TransferQueueItem): string {
    return item.kind === 'chunk' ? `chunk:${item.chunkId}` : `finalize:${item.sessionId}`;
  }

  private async send(item: TransferQueueItem): Promise<boolean> {
    try {
      if (item.kind === 'chunk') {
        await this.uploadChunk(item);
      } else {
        await this.sendFinalize(item);
      }
      await storage.removeFromTransferQueue(item);
      return true;
    } catch (error) {
      await this.recordFailure(item, error);
      return false;
    }
  }

  private async uploadChunk(item: Extract<TransferQueueItem, { kind: 'chunk' }>): Promise<void> {
    const chunk = (await storage.getChunks()).find((c) => c.id === item.chunkId);
    if (!chunk || !(await RNFS.exists(chunk.audioPath))) {
      // Nothing left to send; drop it rather than retrying forever.
      if (chunk) {
        await storage.updateChunk(chunk.id, { status: 'failed', error: 'Audio file is missing on the phone' });
        this.notify({ chunkId: chunk.id, progress: 0, status: 'failed', error: 'Audio file is missing' });
      }
      return;
    }
    const session = (await storage.getSessions()).find((s) => s.id === chunk.sessionId);
    const config = this.pcConfig!;

    this.notify({ chunkId: chunk.id, progress: 0, status: 'uploading' });
    await storage.updateChunk(chunk.id, { status: 'uploading' });

    const formData = new FormData();
    formData.append('audio', {
      uri: toFileUri(chunk.audioPath),
      name: `chunk_${chunk.seq}.m4a`,
      type: 'audio/mp4',
    } as any);
    formData.append('session_id', chunk.sessionId);
    formData.append('seq', String(chunk.seq));
    formData.append('course_tag', session?.courseId ?? 'unknown');
    formData.append('started_at', String(chunk.startedAt));
    formData.append('ended_at', String(chunk.endedAt));
    formData.append('session_started_at', String(session?.startedAt ?? chunk.startedAt));
    formData.append('sha256', await RNFS.hash(chunk.audioPath, 'sha256'));

    await axios.post(`http://${this.activeIp}:${config.port}/api/chunks/upload`, formData, {
      timeout: 120000,
      headers: { 'Content-Type': 'multipart/form-data', 'X-Pair-Token': config.token },
      onUploadProgress: (e) => {
        const progress = e.total ? Math.round((e.loaded * 100) / e.total) : 0;
        this.notify({ chunkId: chunk.id, progress, status: 'uploading' });
      },
    });

    await storage.updateChunk(chunk.id, { status: 'synced', error: undefined });
    this.notify({ chunkId: chunk.id, progress: 100, status: 'synced' });
  }

  private async sendFinalize(item: Extract<TransferQueueItem, { kind: 'finalize' }>): Promise<void> {
    const session = (await storage.getSessions()).find((s) => s.id === item.sessionId);
    const config = this.pcConfig!;
    await axios.post(
      `http://${this.activeIp}:${config.port}/api/sessions/${item.sessionId}/finalize`,
      {
        ended_at: item.endedAt,
        chunk_count: item.chunkCount,
        course_tag: session?.courseId ?? 'unknown',
        started_at: session?.startedAt ?? item.endedAt,
      },
      { timeout: 15000, headers: { 'X-Pair-Token': config.token } },
    );
  }

  private async recordFailure(item: TransferQueueItem, error: unknown): Promise<void> {
    const message = describeError(error);
    const attempts = item.attempts + 1;
    // Keep retrying with capped backoff: the usual cause is simply that the PC is off.
    const delay = Math.min(RETRY_BASE_DELAY_MS * 2 ** (attempts - 1), RETRY_MAX_DELAY_MS);
    const key = this.itemKey(item);

    await storage.updateTransferQueue((queue) =>
      queue.map((q) =>
        this.itemKey(q) === key ? { ...q, attempts, nextAttemptAt: Date.now() + delay, lastError: message } : q,
      ),
    );

    if (item.kind === 'chunk') {
      await storage.updateChunk(item.chunkId, { status: 'pending', error: message, retryCount: attempts });
      this.notify({ chunkId: item.chunkId, progress: 0, status: 'pending', error: message });
    }

    if (axios.isAxiosError(error)) {
      if (error.response?.status === 401) {
        this.setConnection('unauthorized', null);
      } else if (!error.response) {
        this.setConnection('unreachable', null);
      }
    }
  }

  /** Makes every queued item eligible to send immediately. */
  async retryNow(): Promise<void> {
    await storage.updateTransferQueue((queue) => queue.map((q) => ({ ...q, nextAttemptAt: 0 })));
    await this.pingAndProcess();
  }
}

export const transferService = new TransferService();
