import { useEffect, useRef, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  fetchSession, fetchSegments, fetchNotes, getFullAudioUrl, generateNotes, reprocessSession, errorMessage,
} from '../api/client';
import { Session, Segment, NotesDocument, NoteItem, CATEGORY_COLORS, CATEGORY_LABELS } from '../types';

const NOTE_SECTIONS = [
  { type: 'concepts', label: 'Concepts', color: CATEGORY_COLORS.concept },
  { type: 'examples', label: 'Examples', color: CATEGORY_COLORS.example },
  { type: 'announcements', label: 'Announcements', color: CATEGORY_COLORS.announcement },
  { type: 'qa', label: 'Q&A', color: CATEGORY_COLORS.qa },
] as const;

// Note timestamps are whole seconds, so they can sit just before their segment's exact start.
const NOTE_MATCH_SLACK_MS = 1000;
const REFRESH_WHILE_PROCESSING_MS = 10000;

export function SessionView() {
  const { id } = useParams<{ id: string }>();
  const [session, setSession] = useState<Session | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeSegmentId, setActiveSegmentId] = useState<string | null>(null);
  const [model, setModel] = useState<'local' | 'groq'>('local');
  const [generating, setGenerating] = useState(false);
  const [notes, setNotes] = useState<NotesDocument[]>([]);
  const [notesError, setNotesError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    if (!id) return;
    loadData();
  }, [id]);

  // Keep a lecture that is still uploading or processing up to date without a manual reload.
  const inProgress = session?.status === 'capturing' || session?.status === 'processing';
  useEffect(() => {
    if (!inProgress) return;
    const timer = setInterval(loadData, REFRESH_WHILE_PROCESSING_MS);
    return () => clearInterval(timer);
  }, [inProgress, id]);

  const loadData = async () => {
    try {
      const [sessionData, segmentsData, notesData] = await Promise.all([
        fetchSession(id!),
        fetchSegments(id!),
        fetchNotes(id!),
      ]);
      setSession(sessionData);
      setSegments(segmentsData);
      setNotes(notesData);
      setError(null);
    } catch (e) {
      setError('Failed to load session');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const sync = () => {
      const currentMs = audio.currentTime * 1000;
      // End-exclusive, so seeking to a segment's start doesn't also match the one before it
      // (they share that boundary). The final segment still matches at the very end of the audio.
      const segment =
        segments.find(s => currentMs >= s.start_ms && currentMs < s.end_ms) ??
        segments.find(s => currentMs >= s.start_ms && currentMs <= s.end_ms);
      setActiveSegmentId(segment?.id ?? null);
    };
    audio.addEventListener('timeupdate', sync);
    audio.addEventListener('seeked', sync);
    return () => {
      audio.removeEventListener('timeupdate', sync);
      audio.removeEventListener('seeked', sync);
    };
  }, [segments, loading]);

  const seekTo = (ms: number) => {
    if (audioRef.current) {
      audioRef.current.currentTime = ms / 1000;
    }
  };

  const handleGenerateNotes = async () => {
    setGenerating(true);
    setNotesError(null);
    try {
      setNotes(await generateNotes(id!, model));
    } catch (e) {
      setNotesError(`Could not generate notes: ${errorMessage(e)}`);
    } finally {
      setGenerating(false);
    }
  };

  const handleRetry = async () => {
    setRetrying(true);
    try {
      setSession(await reprocessSession(id!));
    } catch (e) {
      setError(`Could not retry: ${errorMessage(e)}`);
    } finally {
      setRetrying(false);
    }
  };

  if (loading) {
    return (
      <div className="container">
        <div className="loading"><div className="spinner" /></div>
      </div>
    );
  }

  if (error || !session) {
    return (
      <div className="container empty-state">
        <h2>Error</h2>
        <p>{error || 'Session not found'}</p>
        <Link to="/" className="btn btn-primary" style={{marginTop: 16}}>Back</Link>
      </div>
    );
  }

  const activeSegment = segments.find(s => s.id === activeSegmentId) ?? null;
  const isNoteActive = (item: NoteItem) =>
    activeSegment !== null &&
    item.start_ms !== null &&
    item.start_ms >= activeSegment.start_ms - NOTE_MATCH_SLACK_MS &&
    item.start_ms < activeSegment.end_ms - NOTE_MATCH_SLACK_MS;

  const failedChunks = session.chunks.filter(c => c.status === 'failed');
  const failureDetail = session.error_msg ?? failedChunks.map(c => `Chunk ${c.seq + 1}: ${c.error_msg}`).join('\n');

  return (
    <div className="container">
      <Link to="/" className="back-link">← Back to Sessions</Link>

      <div className="toolbar">
        <div style={{flex: 1}}>
          <h2 style={{fontSize: 20, fontWeight: 700}}>{getCourseName(session.course_tag)}</h2>
          <div style={{color: 'var(--text-muted)', fontSize: 14, marginTop: 4}}>
            {new Date(session.started_at).toLocaleString()} · {formatTimeMs(session.ended_at ? session.ended_at - session.started_at : 0)}
            {inProgress && ` · ${session.status}...`}
          </div>
        </div>
        <div className="model-toggle">
          <label>
            <span>Model:</span>
            <span style={{textTransform: 'capitalize'}}>{model}</span>
            <label className="switch">
              <input
                type="checkbox"
                checked={model === 'groq'}
                onChange={e => setModel(e.target.checked ? 'groq' : 'local')}
              />
              <span className="slider" />
            </label>
          </label>
          <button
            className="btn btn-primary"
            onClick={handleGenerateNotes}
            disabled={generating || segments.length === 0}
          >
            {generating ? 'Generating...' : notes.length > 0 ? 'Regenerate Notes' : 'Generate Notes'}
          </button>
        </div>
      </div>

      {session.status === 'failed' && (
        <div className="alert">
          <div style={{flex: 1}}>
            <strong>Processing failed.</strong>
            {failureDetail && <div className="alert-detail">{failureDetail}</div>}
          </div>
          <button className="btn btn-primary" onClick={handleRetry} disabled={retrying}>
            {retrying ? 'Retrying...' : 'Retry processing'}
          </button>
        </div>
      )}

      {notesError && <div className="alert"><div className="alert-detail">{notesError}</div></div>}

      <div className="player-container">
        <audio
          ref={audioRef}
          src={getFullAudioUrl(session.id)}
          className="audio-player"
          controls
          preload="metadata"
        />
      </div>

      {notes.length > 0 && (
        <div className="notes-grid">
          {NOTE_SECTIONS.map(({ type, label, color }) => {
            const doc = notes.find(d => d.type === type);
            return (
              <div key={type} className="note-card" style={{borderLeft: `4px solid ${color}`}}>
                <div className="note-header" style={{color}}>{label}</div>
                <div className="note-body">
                  {!doc || doc.items.length === 0 ? (
                    <div className="note-empty">No content</div>
                  ) : (
                    doc.items.map((item, i) => (
                      <NoteLine key={i} item={item} active={isNoteActive(item)} onSeek={seekTo} />
                    ))
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <h3 style={{fontSize: 16, fontWeight: 600}}>Segments</h3>
          <span style={{color: 'var(--text-muted)', fontSize: 13}}>{segments.length} segments</span>
        </div>
        <div className="card-body">
          <div className="segment-list">
            {segments.map(seg => (
              <div
                key={seg.id}
                className={`segment-item ${activeSegmentId === seg.id ? 'playing' : ''}`}
                onClick={() => seekTo(seg.start_ms)}
                style={{borderLeftColor: CATEGORY_COLORS[seg.category]}}
              >
                <div className="segment-color" style={{backgroundColor: CATEGORY_COLORS[seg.category]}} />
                <div className="segment-content">
                  <div className="segment-header">
                    <span
                      className="segment-category"
                      style={{backgroundColor: CATEGORY_COLORS[seg.category] + '20', color: CATEGORY_COLORS[seg.category]}}
                    >
                      {CATEGORY_LABELS[seg.category]}
                    </span>
                    <span className="segment-time">
                      {formatTimeMs(seg.start_ms)} - {formatTimeMs(seg.end_ms)}
                    </span>
                    {seg.speaker_role === 'other' && <span className="segment-speaker">Student</span>}
                    {seg.inferred_deadline !== null && (
                      <span className="segment-deadline">Due {new Date(seg.inferred_deadline).toLocaleDateString()}</span>
                    )}
                  </div>
                  {seg.summary && <div className="segment-summary">{seg.summary}</div>}
                  <div className="segment-text">{seg.source_text}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function NoteLine({ item, active, onSeek }: { item: NoteItem; active: boolean; onSeek: (ms: number) => void }) {
  const className = `note-line note-${item.kind}${active ? ' playing' : ''}${item.start_ms !== null ? ' seekable' : ''}`;
  const content = (
    <>
      {item.start_ms !== null && <span className="note-time">{formatTimeMs(item.start_ms)}</span>}
      <span>{item.text}</span>
    </>
  );
  if (item.start_ms === null) {
    return <div className={className}>{content}</div>;
  }
  return (
    <button type="button" className={className} onClick={() => onSeek(item.start_ms!)}>
      {content}
    </button>
  );
}

function getCourseName(tag: string): string {
  const names: Record<string, string> = {
    cs: 'Control Systems',
    ml: 'Machine Learning',
    ds: 'Data Structures',
    os: 'Operating Systems',
  };
  return names[tag] || tag;
}

function formatTimeMs(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}
