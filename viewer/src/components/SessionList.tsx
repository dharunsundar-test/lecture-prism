import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchSessions } from '../api/client';
import { Session } from '../types';

export function SessionList() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadSessions();
    const interval = setInterval(loadSessions, 10000);
    return () => clearInterval(interval);
  }, []);

  const loadSessions = async () => {
    try {
      const data = await fetchSessions();
      setSessions(data);
      setError(null);
    } catch (e) {
      setError('Failed to load sessions');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="container">
        <div className="loading"><div className="spinner" /></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="container empty-state">
        <h2>Error</h2>
        <p>{error}</p>
        <button className="btn btn-primary" onClick={loadSessions} style={{marginTop: 16}}>Retry</button>
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="container empty-state">
        <h2>No Sessions Yet</h2>
        <p>Record a lecture on your phone to see it here</p>
      </div>
    );
  }

  return (
    <div className="container">
      <header className="header">
        <h1>Lecture Capture</h1>
        <a className="btn btn-primary" href="/api/pair" target="_blank" rel="noreferrer">Pair phone</a>
      </header>
      <div className="session-grid">
        {sessions.map(session => (
          <Link key={session.id} to={`/session/${session.id}`} style={{textDecoration: 'none', color: 'inherit'}}>
            <div className="session-card">
              <div className="session-card-header">
                <div className="course-badge" style={{backgroundColor: getCourseColor(session.course_tag)}} />
                <div className="session-info">
                  <div className="course-name">{getCourseName(session.course_tag)}</div>
                  <div className="session-meta">
                    {formatTime(session.started_at)} · {formatDuration(session.ended_at ? session.ended_at - session.started_at : 0)}
                  </div>
                </div>
                <span className="status-badge" style={{backgroundColor: getStatusColor(session.status) + '20', color: getStatusColor(session.status)}}>
                  {session.status}
                </span>
              </div>
              <div className="chunk-status">{getChunkSummary(session)}</div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function getChunkSummary(session: Session): string {
  const received = session.chunks.length;
  const processed = session.chunks.filter(c => c.status === 'done').length;
  const failed = session.chunks.filter(c => c.status === 'failed').length;
  const total = session.expected_chunks ?? received;
  const parts = [
    `${received}${session.expected_chunks !== null ? ` / ${total}` : ''} chunks received`,
    `${processed} processed`,
  ];
  if (failed) parts.push(`${failed} failed`);
  return parts.join(' · ');
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

function getCourseColor(tag: string): string {
  const colors: Record<string, string> = {
    cs: '#2563EB',
    ml: '#7C3AED',
    ds: '#059669',
    os: '#DC2626',
  };
  return colors[tag] || '#64748B';
}

function getStatusColor(status: string): string {
  switch (status) {
    case 'capturing': return '#F59E0B';
    case 'synced': return '#3B82F6';
    case 'processing': return '#8B5CF6';
    case 'done': return '#22C55E';
    case 'failed': return '#EF4444';
    default: return '#64748B';
  }
}

function formatDuration(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleString();
}