import { Platform, PermissionsAndroid } from 'react-native';
import { storage } from '../storage';
import { CHUNK_DURATION_MS, RecordingSession } from '../types';
import { nativeRecorder, NativeChunkEvent } from './nativeModules';
import { transferService } from './transfer';

export interface RecordingState {
  isRecording: boolean;
  currentTime: number;
  currentChunk: number;
  sessionId: string | null;
}

type RecordingCallback = (state: RecordingState) => void;
type ErrorCallback = (message: string) => void;

const IDLE_STATE: RecordingState = {
  isRecording: false,
  currentTime: 0,
  currentChunk: 0,
  sessionId: null,
};

/**
 * Coordinates the native recorder (which writes the chunk files) with local storage and the
 * upload queue. Chunk rotation happens natively so it keeps working in the background; this
 * class records each finished chunk and queues it for upload.
 */
class AudioRecorderService {
  private callbacks: Set<RecordingCallback> = new Set();
  private errorCallbacks: Set<ErrorCallback> = new Set();
  private state: RecordingState = { ...IDLE_STATE };
  private session: RecordingSession | null = null;
  private nextSeq = 0;
  private elapsedBeforeResume = 0;
  private resumedAt = 0;
  private tickTimer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    nativeRecorder.onChunk((event) => this.handleChunk(event));
    nativeRecorder.onError((message) => this.handleNativeError(message));
  }

  subscribe(cb: RecordingCallback): () => void {
    this.callbacks.add(cb);
    cb(this.state);
    return () => this.callbacks.delete(cb);
  }

  onError(cb: ErrorCallback): () => void {
    this.errorCallbacks.add(cb);
    return () => this.errorCallbacks.delete(cb);
  }

  private notify(): void {
    const snapshot = { ...this.state };
    this.callbacks.forEach((cb) => cb(snapshot));
  }

  private async requestPermissions(): Promise<void> {
    if (Platform.OS !== 'android') {
      return;
    }
    const mic = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO, {
      title: 'Microphone Permission',
      message: 'Lecture Capture needs microphone access to record lectures',
      buttonPositive: 'OK',
    });
    if (mic !== PermissionsAndroid.RESULTS.GRANTED) {
      throw new Error('Microphone permission denied');
    }
    if (Number(Platform.Version) >= 33) {
      // Optional: without it the recording notification is hidden, but recording still works.
      await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS);
    }
  }

  async startRecording(courseId: string): Promise<RecordingSession> {
    if (this.session) {
      throw new Error('A recording is already in progress');
    }
    if (!nativeRecorder.isAvailable()) {
      throw new Error(`Recording is not supported on ${Platform.OS} yet`);
    }
    await this.requestPermissions();

    const session = storage.createSessionMetadata(courseId);
    await storage.upsertSession(session);
    try {
      await nativeRecorder.start(session.id, await storage.getSessionDir(session.id), CHUNK_DURATION_MS, 0);
    } catch (error) {
      await storage.upsertSession({ ...session, status: 'failed' });
      throw error;
    }

    this.session = session;
    this.nextSeq = 0;
    this.elapsedBeforeResume = 0;
    this.state = { isRecording: true, currentTime: 0, currentChunk: 0, sessionId: session.id };
    this.startTicking();
    this.notify();
    return session;
  }

  async pauseRecording(): Promise<void> {
    if (!this.state.isRecording || !this.session) {
      return;
    }
    // Pausing closes the current chunk; resuming starts the next sequence number.
    this.nextSeq = Math.max(this.nextSeq, await nativeRecorder.stop());
    this.stopTicking();

    this.session = { ...this.session, status: 'paused' };
    await storage.upsertSession(this.session);
    this.state = { ...this.state, isRecording: false, currentChunk: this.nextSeq };
    this.notify();
  }

  async resumeRecording(): Promise<void> {
    if (this.state.isRecording || !this.session) {
      return;
    }
    await this.requestPermissions();
    await nativeRecorder.start(
      this.session.id,
      await storage.getSessionDir(this.session.id),
      CHUNK_DURATION_MS,
      this.nextSeq,
    );

    this.session = { ...this.session, status: 'recording' };
    await storage.upsertSession(this.session);
    this.state = { ...this.state, isRecording: true };
    this.startTicking();
    this.notify();
  }

  async stopRecording(): Promise<RecordingSession | null> {
    if (!this.session) {
      return null;
    }
    if (this.state.isRecording) {
      this.nextSeq = Math.max(this.nextSeq, await nativeRecorder.stop());
      this.stopTicking();
    }

    const endedAt = Date.now();
    const completed: RecordingSession = {
      ...this.session,
      endedAt,
      duration: this.elapsedBeforeResume,
      status: 'completed',
    };
    await storage.upsertSession(completed);
    // Chunk events may still be in flight; the host accepts finalize before or after the last chunk.
    await transferService.enqueueFinalize(completed, this.nextSeq);

    this.session = null;
    this.state = { ...IDLE_STATE };
    this.notify();
    return completed;
  }

  getState(): RecordingState {
    return this.state;
  }

  getSession(): RecordingSession | null {
    return this.session;
  }

  private async handleChunk(event: NativeChunkEvent): Promise<void> {
    this.nextSeq = Math.max(this.nextSeq, event.seq + 1);
    const chunk = storage.chunkFromRecording(event);
    if (await storage.addChunk(chunk)) {
      await transferService.enqueueChunk(chunk);
    }
    if (this.session?.id === event.sessionId) {
      this.state = { ...this.state, currentChunk: this.nextSeq };
      this.notify();
    }
  }

  private async handleNativeError(message: string): Promise<void> {
    // The service has already stopped and saved whatever it had recorded.
    if (this.session && this.state.isRecording) {
      this.stopTicking();
      this.session = { ...this.session, status: 'paused' };
      await storage.upsertSession(this.session);
      this.state = { ...this.state, isRecording: false };
      this.notify();
    }
    this.errorCallbacks.forEach((cb) => cb(message));
  }

  private startTicking(): void {
    this.stopTicking();
    this.resumedAt = Date.now();
    this.tickTimer = setInterval(() => {
      this.state = { ...this.state, currentTime: this.elapsedBeforeResume + (Date.now() - this.resumedAt) };
      this.notify();
    }, 1000);
  }

  private stopTicking(): void {
    if (this.tickTimer) {
      clearInterval(this.tickTimer);
      this.tickTimer = null;
      this.elapsedBeforeResume += Date.now() - this.resumedAt;
      this.state = { ...this.state, currentTime: this.elapsedBeforeResume };
    }
  }
}

export const audioRecorder = new AudioRecorderService();
