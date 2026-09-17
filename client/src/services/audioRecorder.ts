import AudioRecorderPlayer from 'react-native-audio-recorder-player';
import { Platform, PermissionsAndroid } from 'react-native';
import RNFS from 'react-native-fs';
import { storage, CHUNK_DURATION_MS } from '../storage';
import { Chunk, RecordingSession } from '../types';

const audioRecorderPlayer = new AudioRecorderPlayer();

export interface RecordingState {
  isRecording: boolean;
  currentTime: number;
  currentChunk: number;
  sessionId: string | null;
}

type RecordingCallback = (state: RecordingState) => void;

class AudioRecorderService {
  private callbacks: Set<RecordingCallback> = new Set();
  private state: RecordingState = {
    isRecording: false,
    currentTime: 0,
    currentChunk: 0,
    sessionId: null,
  };
  private chunkTimer: ReturnType<typeof setTimeout> | null = null;
  private session: RecordingSession | null = null;
  private chunkStartTime = 0;
  private currentChunkPath = '';

  subscribe(cb: RecordingCallback): () => void {
    this.callbacks.add(cb);
    cb(this.state);
    return () => this.callbacks.delete(cb);
  }

  private notify(): void {
    this.callbacks.forEach((cb) => cb(this.state));
  }

  private async requestPermission(): Promise<boolean> {
    if (Platform.OS === 'android') {
      const granted = await PermissionsAndroid.request(
        PermissionsAndroid.PERMISSIONS.RECORD_AUDIO,
        {
          title: 'Microphone Permission',
          message: 'Lecture Capture needs microphone access to record lectures',
          buttonNeutral: 'Ask Me Later',
          buttonNegative: 'Cancel',
          buttonPositive: 'OK',
        }
      );
      return granted === PermissionsAndroid.RESULTS.GRANTED;
    }
    return true;
  }

  async startRecording(courseId: string): Promise<RecordingSession> {
    const hasPermission = await this.requestPermission();
    if (!hasPermission) {
      throw new Error('Microphone permission denied');
    }

    this.session = await storage.createSessionMetadata(courseId);
    this.state = {
      isRecording: true,
      currentTime: 0,
      currentChunk: 0,
      sessionId: this.session.id,
    };
    this.notify();

    await this.startNewChunk();
    this.startChunkTimer();

    return this.session;
  }

  private async startNewChunk(): Promise<void> {
    if (!this.session) return;

    const seq = this.state.currentChunk;
    const dir = await storage.getSessionDir(this.session.id);
    this.currentChunkPath = `${dir}/chunk_${seq}.m4a`;
    this.chunkStartTime = Date.now();

    await audioRecorderPlayer.startRecorder(this.currentChunkPath, {
      SampleRate: 44100,
      Channels: 1,
      AudioQuality: 'High',
      AudioEncoding: 'aac',
      MeteringEnabled: false,
    });
  }

  private startChunkTimer(): void {
    this.chunkTimer = setTimeout(() => this.rotateChunk(), CHUNK_DURATION_MS);
  }

  private async rotateChunk(): Promise<void> {
    if (!this.session || !this.state.isRecording) return;

    await audioRecorderPlayer.stopRecorder();
    const chunkEndTime = Date.now();
    const duration = chunkEndTime - this.chunkStartTime;

    try {
      const stat = await RNFS.stat(this.currentChunkPath);
      const chunk = await storage.createChunkMetadata(
        this.session.id,
        this.state.currentChunk,
        this.currentChunkPath,
        duration,
        stat.size,
        this.chunkStartTime,
        chunkEndTime
      );

      this.session.chunks.push(chunk);
      await storage.saveSessions([this.session, ...(await storage.getSessions()).filter(s => s.id !== this.session!.id)]);
      await storage.saveChunks([chunk, ...(await storage.getChunks())]);

      this.state.currentChunk += 1;
      this.notify();

      await this.startNewChunk();
      this.startChunkTimer();
    } catch (error) {
      console.error('Failed to finalize chunk:', error);
    }
  }

  async pauseRecording(): Promise<void> {
    if (!this.state.isRecording || !this.session) return;

    if (this.chunkTimer) {
      clearTimeout(this.chunkTimer);
      this.chunkTimer = null;
    }

    await audioRecorderPlayer.stopRecorder();
    const chunkEndTime = Date.now();
    const duration = chunkEndTime - this.chunkStartTime;

    try {
      const stat = await RNFS.stat(this.currentChunkPath);
      const chunk = await storage.createChunkMetadata(
        this.session.id,
        this.state.currentChunk,
        this.currentChunkPath,
        duration,
        stat.size,
        this.chunkStartTime,
        chunkEndTime
      );

      this.session.chunks.push(chunk);
      this.session.status = 'paused';
      await storage.saveSessions([this.session, ...(await storage.getSessions()).filter(s => s.id !== this.session.id)]);
      await storage.saveChunks([chunk, ...(await storage.getChunks())]);
    } catch (error) {
      console.error('Failed to pause recording:', error);
    }

    this.state.isRecording = false;
    this.notify();
  }

  async resumeRecording(): Promise<void> {
    if (this.state.isRecording || !this.session) return;

    const hasPermission = await this.requestPermission();
    if (!hasPermission) {
      throw new Error('Microphone permission denied');
    }

    this.session.status = 'recording';
    this.state.isRecording = true;
    this.notify();

    await this.startNewChunk();
    this.startChunkTimer();
  }

  async stopRecording(): Promise<RecordingSession | null> {
    if (!this.session) return null;

    if (this.chunkTimer) {
      clearTimeout(this.chunkTimer);
      this.chunkTimer = null;
    }

    if (this.state.isRecording) {
      await audioRecorderPlayer.stopRecorder();
      const chunkEndTime = Date.now();
      const duration = chunkEndTime - this.chunkStartTime;

      try {
        const stat = await RNFS.stat(this.currentChunkPath);
        const chunk = await storage.createChunkMetadata(
          this.session.id,
          this.state.currentChunk,
          this.currentChunkPath,
          duration,
          stat.size,
          this.chunkStartTime,
          chunkEndTime
        );

        this.session.chunks.push(chunk);
        await storage.saveChunks([chunk, ...(await storage.getChunks())]);
      } catch (error) {
        console.error('Failed to finalize last chunk:', error);
      }
    }

    this.session.endedAt = Date.now();
    this.session.duration = this.session.endedAt - this.session.startedAt;
    this.session.status = 'completed';
    await storage.saveSessions([this.session, ...(await storage.getSessions()).filter(s => s.id !== this.session.id)]);

    const completedSession = this.session;
    this.session = null;
    this.state = {
      isRecording: false,
      currentTime: 0,
      currentChunk: 0,
      sessionId: null,
    };
    this.notify();

    return completedSession;
  }

  getState(): RecordingState {
    return this.state;
  }

  getSession(): RecordingSession | null {
    return this.session;
  }
}

export const audioRecorder = new AudioRecorderService();