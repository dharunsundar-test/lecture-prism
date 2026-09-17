import axios from 'axios';
import { NotesDocument, Session, Segment } from '../types';

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
});

export async function fetchSessions(): Promise<Session[]> {
  const { data } = await api.get('/sessions');
  return data;
}

export async function fetchSession(sessionId: string): Promise<Session> {
  const { data } = await api.get(`/sessions/${sessionId}`);
  return data;
}

export async function fetchSegments(sessionId: string): Promise<Segment[]> {
  const { data } = await api.get(`/sessions/${sessionId}/segments`);
  return data;
}

export async function fetchNotes(sessionId: string): Promise<NotesDocument[]> {
  const { data } = await api.get(`/sessions/${sessionId}/notes`);
  return data.documents;
}

export async function generateNotes(
  sessionId: string,
  model: 'local' | 'groq'
): Promise<NotesDocument[]> {
  // Local models can take several minutes over a full lecture.
  const { data } = await api.post(`/sessions/${sessionId}/notes`, { model }, { timeout: 15 * 60 * 1000 });
  return data.documents;
}

/** The host's error detail when there is one, e.g. "GROQ_API_KEY is not set in host/.env". */
export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

export function getAudioUrl(sessionId: string, chunkSeq: number): string {
  return `/api/sessions/${sessionId}/audio/${chunkSeq}`;
}

export function getFullAudioUrl(sessionId: string): string {
  return `/api/sessions/${sessionId}/audio/full`;
}