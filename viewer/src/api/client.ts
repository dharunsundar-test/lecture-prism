import axios from 'axios';
import { Session, Segment } from '../types';

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

export async function generateNotes(
  sessionId: string,
  model: 'local' | 'groq'
): Promise<{ documents: any[] }> {
  const { data } = await api.post(`/sessions/${sessionId}/notes`, { model });
  return data;
}

export function getAudioUrl(sessionId: string, chunkSeq: number): string {
  return `/api/sessions/${sessionId}/audio/${chunkSeq}`;
}

export function getFullAudioUrl(sessionId: string): string {
  return `/api/sessions/${sessionId}/audio/full`;
}