import axios from 'axios';
import NetInfo from '@react-native-community/netinfo';
import { storage, MAX_RETRIES, RETRY_BASE_DELAY_MS, PING_INTERVAL_MS } from '../storage';
import { Chunk, PCConfig, TransferQueueItem } from '../types';

export interface TransferProgress {
  chunkId: string;
  progress: number;
  status: 'pending' | 'uploading' | 'synced' | 'failed';
  error?: string;
}

type ProgressCallback = (progress: TransferProgress) => void;

class TransferService {
  private callbacks: Set<ProgressCallback> = new Set();
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private processingQueue = false;
  private pcConfig: PCConfig | null = null;

  subscribe(cb: ProgressCallback): () => void {
    this.callbacks.add(cb);
    return () => this.callbacks.delete(cb);
  }

  private notify(progress: TransferProgress): void {
    this.callbacks.forEach((cb) => cb(progress));
  }

  private getBaseUrl(): string {
    if (!this.pcConfig) return '';
    return `http://${this.pcConfig.ip}:${this.pcConfig.port}`;
  }

  async initialize(): Promise<void> {
    this.pcConfig = await storage.getPCConfig();
    if (this.pcConfig) {
      this.startPingLoop();
      this.processQueue();
    }
  }

  async setPCConfig(config: PCConfig): Promise<void> {
    this.pcConfig = config;
    await storage.savePCConfig(config);
    this.startPingLoop();
    this.processQueue();
  }

  async clearPCConfig(): Promise<void> {
    this.pcConfig = null;
    await storage.clearPCConfig();
    this.stopPingLoop();
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
    if (!this.pcConfig) return;

    try {
      await axios.get(`${this.getBaseUrl()}/health`, { timeout: 5000 });
      this.processQueue();
    } catch {
      // PC not reachable, will retry on next ping
    }
  }

  async enqueueChunk(chunk: Chunk): Promise<void> {
    const queue = await storage.getTransferQueue();
    const item: TransferQueueItem = {
      chunkId: chunk.id,
      sessionId: chunk.sessionId,
      filePath: chunk.audioPath,
      attempts: 0,
    };
    await storage.saveTransferQueue([...queue, item]);
    this.processQueue();
  }

  private async processQueue(): Promise<void> {
    if (this.processingQueue || !this.pcConfig) return;

    const netInfo = await NetInfo.fetch();
    if (!netInfo.isConnected) return;

    this.processingQueue = true;

    try {
      const queue = await storage.getTransferQueue();
      const pending = queue.filter((item) => item.attempts < MAX_RETRIES);

      for (const item of pending) {
        await this.uploadChunk(item);
        const updatedQueue = (await storage.getTransferQueue()).filter((q) => q.chunkId !== item.chunkId);
        await storage.saveTransferQueue(updatedQueue);
      }
    } finally {
      this.processingQueue = false;
    }
  }

  private async uploadChunk(item: TransferQueueItem): Promise<void> {
    const chunks = await storage.getChunks();
    const chunk = chunks.find((c) => c.id === item.chunkId);
    if (!chunk) return;

    const sessions = await storage.getSessions();
    const session = sessions.find((s) => s.id === chunk.sessionId);
    if (!session) return;

    this.notify({ chunkId: item.chunkId, progress: 0, status: 'uploading' });

    try {
      const formData = new FormData();
      formData.append('audio', {
        uri: `file://${item.filePath}`,
        name: `chunk_${chunk.seq}.m4a`,
        type: 'audio/m4a',
      } as any);
      formData.append('session_id', chunk.sessionId);
      formData.append('seq', chunk.seq.toString());
      formData.append('course_tag', session.courseId);
      formData.append('started_at', chunk.startedAt.toString());
      formData.append('ended_at', chunk.endedAt.toString());

      await axios.post(`${this.getBaseUrl()}/api/chunks/upload`, formData, {
        timeout: 120000,
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (e) => {
          const progress = e.total ? Math.round((e.loaded * 100) / e.total) : 0;
          this.notify({ chunkId: item.chunkId, progress, status: 'uploading' });
        },
      });

      await this.markChunkSynced(chunk.id);
      this.notify({ chunkId: item.chunkId, progress: 100, status: 'synced' });
    } catch (error: any) {
      const newAttempts = item.attempts + 1;
      const queue = await storage.getTransferQueue();
      const updated = queue.map((q) =>
        q.chunkId === item.chunkId ? { ...q, attempts: newAttempts, lastAttempt: Date.now() } : q
      );
      await storage.saveTransferQueue(updated);

      const delay = RETRY_BASE_DELAY_MS * Math.pow(2, newAttempts - 1);
      this.notify({
        chunkId: item.chunkId,
        progress: 0,
        status: newAttempts >= MAX_RETRIES ? 'failed' : 'pending',
        error: error.message,
      });

      if (newAttempts < MAX_RETRIES) {
        setTimeout(() => this.processQueue(), delay);
      }
    }
  }

  private async markChunkSynced(chunkId: string): Promise<void> {
    const chunks = await storage.getChunks();
    const updated = chunks.map((c) =>
      c.id === chunkId ? { ...c, status: 'synced' as const } : c
    );
    await storage.saveChunks(updated);

    const sessions = await storage.getSessions();
    const updatedSessions = sessions.map((s) => {
      const sessionChunks = updated.filter((c) => c.sessionId === s.id);
      const allSynced = sessionChunks.length > 0 && sessionChunks.every((c) => c.status === 'synced');
      if (allSynced && s.status !== 'completed') {
        return { ...s, status: 'completed' as const };
      }
      return s;
    });
    await storage.saveSessions(updatedSessions);
  }

  async retryFailed(): Promise<void> {
    const queue = await storage.getTransferQueue();
    const failed = queue.filter((item) => item.attempts >= MAX_RETRIES);
    const reset = failed.map((item) => ({ ...item, attempts: 0 }));
    const others = queue.filter((item) => item.attempts < MAX_RETRIES);
    await storage.saveTransferQueue([...others, ...reset]);
    this.processQueue();
  }

  getPCConfig(): PCConfig | null {
    return this.pcConfig;
  }

  isPaired(): boolean {
    return this.pcConfig !== null;
  }
}

export const transferService = new TransferService();