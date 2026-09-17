import AsyncStorage from '@react-native-async-storage/async-storage';
import { Course, RecordingSession, Chunk, PCConfig, TransferQueueItem, CHUNK_DURATION_MS } from '../types';
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

export const storage = {
  async getCourses(): Promise<Course[]> {
    try {
      const data = await AsyncStorage.getItem(KEYS.COURSES);
      return data ? JSON.parse(data) : DEFAULT_COURSES;
    } catch {
      return DEFAULT_COURSES;
    }
  },

  async saveCourses(courses: Course[]): Promise<void> {
    await AsyncStorage.setItem(KEYS.COURSES, JSON.stringify(courses));
  },

  async getSessions(): Promise<RecordingSession[]> {
    try {
      const data = await AsyncStorage.getItem(KEYS.SESSIONS);
      return data ? JSON.parse(data) : [];
    } catch {
      return [];
    }
  },

  async saveSessions(sessions: RecordingSession[]): Promise<void> {
    await AsyncStorage.setItem(KEYS.SESSIONS, JSON.stringify(sessions));
  },

  async getChunks(): Promise<Chunk[]> {
    try {
      const data = await AsyncStorage.getItem(KEYS.CHUNKS);
      return data ? JSON.parse(data) : [];
    } catch {
      return [];
    }
  },

  async saveChunks(chunks: Chunk[]): Promise<void> {
    await AsyncStorage.setItem(KEYS.CHUNKS, JSON.stringify(chunks));
  },

  async getPCConfig(): Promise<PCConfig | null> {
    try {
      const data = await AsyncStorage.getItem(KEYS.PC_CONFIG);
      return data ? JSON.parse(data) : null;
    } catch {
      return null;
    }
  },

  async savePCConfig(config: PCConfig): Promise<void> {
    await AsyncStorage.setItem(KEYS.PC_CONFIG, JSON.stringify(config));
  },

  async clearPCConfig(): Promise<void> {
    await AsyncStorage.removeItem(KEYS.PC_CONFIG);
  },

  async getTransferQueue(): Promise<TransferQueueItem[]> {
    try {
      const data = await AsyncStorage.getItem(KEYS.TRANSFER_QUEUE);
      return data ? JSON.parse(data) : [];
    } catch {
      return [];
    }
  },

  async saveTransferQueue(queue: TransferQueueItem[]): Promise<void> {
    await AsyncStorage.setItem(KEYS.TRANSFER_QUEUE, JSON.stringify(queue));
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

  generateSessionId(): string {
    return `session_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
  },

  generateChunkId(sessionId: string, seq: number): string {
    return `${sessionId}_chunk_${seq}`;
  },

  async createChunkMetadata(
    sessionId: string,
    seq: number,
    audioPath: string,
    duration: number,
    size: number,
    startedAt: number,
    endedAt: number
  ): Promise<Chunk> {
    return {
      id: this.generateChunkId(sessionId, seq),
      sessionId,
      seq,
      audioPath,
      duration,
      size,
      status: 'pending',
      startedAt,
      endedAt,
      retryCount: 0,
    };
  },

  async createSessionMetadata(courseId: string): Promise<RecordingSession> {
    const id = this.generateSessionId();
    return {
      id,
      courseId,
      startedAt: Date.now(),
      status: 'recording',
      chunks: [],
    };
  },
};