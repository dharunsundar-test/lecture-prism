export interface Session {
  id: string;
  course_tag: string;
  started_at: number;
  ended_at: number | null;
  /** Set once the phone finalizes the session; null while still recording or uploading. */
  expected_chunks: number | null;
  status: 'capturing' | 'processing' | 'done' | 'failed';
  /** null = waiting for every chunk to be transcribed; then 'running' | 'done' | 'failed'. */
  analysis_status: 'running' | 'done' | 'failed' | null;
  error_msg: string | null;
  chunks: Chunk[];
}

export interface Chunk {
  id: string;
  session_id: string;
  seq: number;
  audio_path: string;
  duration_ms: number;
  size_bytes: number;
  status: string;
  error_msg: string | null;
  started_at: number;
  ended_at: number;
}

export interface Segment {
  id: string;
  session_id: string;
  start_ms: number;
  end_ms: number;
  category: Category;
  summary: string | null;
  inferred_deadline: number | null;
  source_text: string;
  speaker_role: 'lecturer' | 'other' | null;
}

export type Category = 'concept' | 'example' | 'announcement' | 'qa' | 'action_item' | 'filler';

export interface NotesDocument {
  id: string;
  session_id: string;
  type: 'concepts' | 'examples' | 'announcements' | 'qa';
  content: string;
  /** content split into lines, with each line's [m:ss] timestamp parsed out by the host. */
  items: NoteItem[];
  generated_at: number;
  model: 'local' | 'groq';
}

export interface NoteItem {
  kind: 'heading' | 'item' | 'text';
  text: string;
  start_ms: number | null;
}

export const CATEGORY_COLORS: Record<Category, string> = {
  concept: '#3B82F6',
  example: '#8B5CF6',
  announcement: '#EF4444',
  qa: '#06B6D4',
  action_item: '#F59E0B',
  filler: '#64748B',
};

export const CATEGORY_LABELS: Record<Category, string> = {
  concept: 'Concept',
  example: 'Example',
  announcement: 'Announcement',
  qa: 'Q&A',
  action_item: 'Action Item',
  filler: 'Filler',
};