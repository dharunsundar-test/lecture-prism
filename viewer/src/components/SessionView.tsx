import { useEffect, useRef, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { fetchSession, fetchSegments, fetchNotes, getFullAudioUrl, generateNotes, errorMessage } from '../api/client';
import { Session, Segment, NotesDocument, CATEGORY_COLORS, CATEGORY_LABELS } from '../types';

function notesByType(documents: NotesDocument[]): Record<string, string> {
  return Object.fromEntries(documents.map(doc => [doc.type, doc.content]));
}

export function SessionView() {
  const { id } = useParams<{ id: string }>();
  const [session, setSession] = useState<Session | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeSegment, setActiveSegment] = useState<string | null>(null);
  const [model, setModel] = useState<'local' | 'groq'>('local');
  const [generating, setGenerating] = useState(false);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [notesError, setNotesError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const syncInterval = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    if (!id) return;
    loadData();
    return () => {
      if (syncInterval.current) clearInterval(syncInterval.current);
    };
  }, [id]);

  const loadData = async () => {
    try {
      const [sessionData, segmentsData, notesData] = await Promise.all([
        fetchSession(id!),
        fetchSegments(id!),
        fetchNotes(id!),
      ]);
      setSession(sessionData);
      setSegments(segmentsData);
      setNotes(notesByType(notesData));
      setError(null);
    } catch (e) {
      setError('Failed to load session');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!audioRef.current) return;
    const audio = audioRef.current;
    syncInterval.current = setInterval(() => {
      const currentMs = audio.currentTime * 1000;
      const segment = segments.find(s => currentMs >= s.start_ms && currentMs <= s.end_ms);
      setActiveSegment(segment?.id || null);
    }, 100);
    return () => {
      if (syncInterval.current) clearInterval(syncInterval.current);
    };
  }, [segments]);

  const seekToSegment = (segment: Segment) => {
    if (audioRef.current) {
      audioRef.current.currentTime = segment.start_ms / 1000;
    }
  };

  const handleGenerateNotes = async () => {
    setGenerating(true);
    setNotesError(null);
    try {
      setNotes(notesByType(await generateNotes(id!, model)));
    } catch (e) {
      setNotesError(`Could not generate notes: ${errorMessage(e)}`);
    } finally {
      setGenerating(false);
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

  const audioUrl = getFullAudioUrl(session.id);

  return (
    <div className="container">
      <Link to="/" className="back-link">← Back to Sessions</Link>

      <div className="toolbar">
        <div style={{flex: 1}}>
          <h2 style={{fontSize: 20, fontWeight: 700}}>{getCourseName(session.course_tag)}</h2>
          <div style={{color: 'var(--text-muted)', fontSize: 14, marginTop: 4}}>
            {new Date(session.started_at).toLocaleString()} · {formatDuration(session.ended_at ? session.ended_at - session.started_at : 0)}
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
            {generating ? 'Generating...' : Object.keys(notes).length > 0 ? 'Regenerate Notes' : 'Generate Notes'}
          </button>
        </div>
      </div>

      {notesError && (
        <div className="card" style={{borderLeft: '4px solid #EF4444', padding: 12, marginBottom: 16, color: '#FCA5A5'}}>
          {notesError}
        </div>
      )}

      <div className="player-container">
        <audio
          ref={audioRef}
          src={audioUrl}
          className="audio-player"
          controls
          preload="metadata"
        />
      </div>

      {Object.keys(notes).length > 0 && (
        <div className="notes-grid">
          {([
            { type: 'concepts', label: 'Concepts', color: CATEGORY_COLORS.concept },
            { type: 'examples', label: 'Examples', color: CATEGORY_COLORS.example },
            { type: 'announcements', label: 'Announcements', color: CATEGORY_COLORS.announcement },
            { type: 'qa', label: 'Q&A', color: CATEGORY_COLORS.qa },
          ] as const).map(({ type, label, color }) => (
            <div key={type} className="note-card" style={{borderLeft: `4px solid ${color}`}}>
              <div className="note-header" style={{color}}>{label}</div>
              <div className="note-content">{notes[type] || 'No content'}</div>
            </div>
          ))}
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
                className={`segment-item ${activeSegment === seg.id ? 'playing' : ''}`}
                onClick={() => seekToSegment(seg)}
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
                  </div>
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

function getCourseName(tag: string): string {
  const names: Record<string, string> = {
    cs: 'Control Systems',
    ml: 'Machine Learning',
    ds: 'Data Structures',
    os: 'Operating Systems',
  };
  return names[tag] || tag;
}

function formatDuration(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatTimeMs(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}