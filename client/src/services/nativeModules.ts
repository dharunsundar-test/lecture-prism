import { DeviceEventEmitter, NativeModules, Platform } from 'react-native';

/** Emitted by the Android recorder service each time a chunk file is complete. */
export interface NativeChunkEvent {
  sessionId: string;
  seq: number;
  path: string;
  startedAt: number;
  endedAt: number;
  sizeBytes: number;
}

interface LectureRecorderNative {
  start(sessionId: string, sessionDir: string, chunkMs: number, startSeq: number): Promise<void>;
  /** Resolves with the sequence number the next chunk should use. */
  stop(): Promise<number>;
  getStatus(): Promise<{ recording: boolean; nextSeq: number }>;
}

interface QrScannerNative {
  /** Resolves with the scanned text, or null if the user cancelled. */
  scan(): Promise<string | null>;
}

function requireNative<T>(name: string): T {
  const mod = NativeModules[name] as T | undefined;
  if (!mod) {
    throw new Error(`${name} is not available on ${Platform.OS} yet`);
  }
  return mod;
}

export const nativeRecorder = {
  isAvailable: () => Boolean(NativeModules.LectureRecorder),

  start: (sessionId: string, sessionDir: string, chunkMs: number, startSeq: number) =>
    requireNative<LectureRecorderNative>('LectureRecorder').start(sessionId, sessionDir, chunkMs, startSeq),

  stop: () => requireNative<LectureRecorderNative>('LectureRecorder').stop(),

  onChunk(listener: (event: NativeChunkEvent) => void): () => void {
    const subscription = DeviceEventEmitter.addListener('LectureRecorder.chunk', listener);
    return () => subscription.remove();
  },

  onError(listener: (message: string) => void): () => void {
    const subscription = DeviceEventEmitter.addListener('LectureRecorder.error', (e: { message: string }) =>
      listener(e.message),
    );
    return () => subscription.remove();
  },
};

export const qrScanner = {
  scan: () => requireNative<QrScannerNative>('QrScanner').scan(),
};
