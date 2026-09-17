import { DeviceEventEmitter, EventSubscription, NativeEventEmitter, NativeModules, Platform } from 'react-native';

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

// iOS modules (RCTEventEmitter) only deliver events through a NativeEventEmitter bound to the
// module; on Android the same emitter forwards DeviceEventEmitter events.
const recorderEvents = NativeModules.LectureRecorder ? new NativeEventEmitter(NativeModules.LectureRecorder) : null;

function subscribe<T>(event: string, listener: (payload: T) => void): () => void {
  const subscription: EventSubscription = recorderEvents
    ? recorderEvents.addListener(event, (payload: any) => listener(payload))
    : DeviceEventEmitter.addListener(event, listener);
  return () => subscription.remove();
}

export const nativeRecorder = {
  isAvailable: () => Boolean(NativeModules.LectureRecorder),

  start: (sessionId: string, sessionDir: string, chunkMs: number, startSeq: number) =>
    requireNative<LectureRecorderNative>('LectureRecorder').start(sessionId, sessionDir, chunkMs, startSeq),

  stop: () => requireNative<LectureRecorderNative>('LectureRecorder').stop(),

  onChunk(listener: (event: NativeChunkEvent) => void): () => void {
    return subscribe('LectureRecorder.chunk', listener);
  },

  onError(listener: (message: string) => void): () => void {
    return subscribe<{ message: string }>('LectureRecorder.error', (e) => listener(e.message));
  },
};

export const qrScanner = {
  scan: () => requireNative<QrScannerNative>('QrScanner').scan(),
};
