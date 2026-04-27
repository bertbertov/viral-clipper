'use client';

import { useEffect, useState, useCallback } from 'react';

const API = '/clips-api';
const TOKEN_KEY = 'clips_admin_token';

type Clip = {
  id: number;
  filename: string;
  title: string;
  caption: string;
  score: number;
  reason: string;
  duration: number;
  approved: number;
  posted_to: string | null;
};

type Job = {
  id: string;
  youtube_url: string;
  style_preset: string; // niche tag (ai_business, movies, default)
  status: string;
  message: string | null;
  created_at: number;
  updated_at: number;
  clip_count: number;
};

function fmtTime(ts: number) {
  return new Date(ts * 1000).toLocaleString();
}

function statusColor(s: string) {
  if (s === 'done') return '#3b7a3a';
  if (s === 'failed') return '#a13434';
  if (s === 'queued') return '#8B8278';
  return '#C9A96E'; // running / uploading
}

export default function ClipsPage() {
  const [token, setToken] = useState('');
  const [authed, setAuthed] = useState(false);
  const [url, setUrl] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<string | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [error, setError] = useState('');
  const [autoApprove, setAutoApprove] = useState(false);
  const [autoMinScore, setAutoMinScore] = useState(85);
  const [niche, setNiche] = useState('ai_business');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [maxClips, setMaxClips] = useState(6);
  const [extraTags, setExtraTags] = useState('');
  const [skipPosting, setSkipPosting] = useState(false);
  const [forceManual, setForceManual] = useState(false);

  // Load token from localStorage
  useEffect(() => {
    const t = localStorage.getItem(TOKEN_KEY);
    if (t) {
      setToken(t);
      setAuthed(true);
    }
  }, []);

  const headers = useCallback(
    () => ({ 'X-Admin-Token': token, 'Content-Type': 'application/json' }),
    [token]
  );

  const loadJobs = useCallback(async () => {
    if (!authed) return;
    try {
      const r = await fetch(`${API}/jobs`, { headers: headers() });
      if (r.status === 401) {
        setAuthed(false);
        localStorage.removeItem(TOKEN_KEY);
        return;
      }
      const data = await r.json();
      setJobs(data.jobs || []);
    } catch (e) {
      setError(String(e));
    }
  }, [authed, headers]);

  const loadClips = useCallback(
    async (jobId: string) => {
      if (!authed) return;
      const r = await fetch(`${API}/jobs/${jobId}`, { headers: headers() });
      if (r.ok) {
        const data = await r.json();
        setClips(data.clips || []);
      }
    },
    [authed, headers]
  );

  // Poll jobs every 5s
  useEffect(() => {
    if (!authed) return;
    loadJobs();
    const id = setInterval(loadJobs, 5000);
    return () => clearInterval(id);
  }, [authed, loadJobs]);

  // Load settings once
  useEffect(() => {
    if (!authed) return;
    fetch(`${API}/settings`, { headers: headers() })
      .then((r) => r.json())
      .then((s) => {
        setAutoApprove(s.auto_approve === 'true');
        setAutoMinScore(parseInt(s.auto_approve_min_score || '85'));
      })
      .catch(() => {});
  }, [authed, headers]);

  const updateSetting = async (key: string, value: string) => {
    await fetch(`${API}/settings/${key}?value=${encodeURIComponent(value)}`, {
      method: 'POST',
      headers: headers(),
    });
  };

  // Poll clips for selected job every 5s
  useEffect(() => {
    if (!selectedJob) {
      setClips([]);
      return;
    }
    loadClips(selectedJob);
    const id = setInterval(() => loadClips(selectedJob), 5000);
    return () => clearInterval(id);
  }, [selectedJob, loadClips]);

  const handleAuth = (e: React.FormEvent) => {
    e.preventDefault();
    if (token) {
      localStorage.setItem(TOKEN_KEY, token);
      setAuthed(true);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem(TOKEN_KEY);
    setToken('');
    setAuthed(false);
    setJobs([]);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url) return;
    setSubmitting(true);
    setError('');
    try {
      const body: Record<string, unknown> = { youtube_url: url, style_preset: niche };
      if (showAdvanced) {
        if (maxClips !== 6)         body.max_clips = maxClips;
        if (extraTags.trim())       body.extra_hashtags = extraTags.trim();
        if (skipPosting)            body.skip_posting = true;
        if (forceManual)            body.force_manual_approve = true;
      }
      const r = await fetch(`${API}/jobs`, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await r.text());
      setUrl('');
      loadJobs();
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const handleApprove = async (clipId: number) => {
    await fetch(`${API}/clips/${clipId}/approve`, {
      method: 'POST',
      headers: headers(),
    });
    if (selectedJob) loadClips(selectedJob);
  };

  const handleDelete = async (jobId: string) => {
    if (!confirm('Delete job and all its clips?')) return;
    await fetch(`${API}/jobs/${jobId}`, { method: 'DELETE', headers: headers() });
    if (selectedJob === jobId) setSelectedJob(null);
    loadJobs();
  };

  if (!authed) {
    return (
      <main style={{ minHeight: '100vh', background: '#F5F0EB', padding: '8rem 1rem' }}>
        <div style={{ maxWidth: 400, margin: '0 auto', textAlign: 'center' }}>
          <h1 style={{ fontFamily: 'var(--font-cormorant)', fontSize: '2.5rem', color: '#1A1A18', marginBottom: '2rem' }}>
            Clips
          </h1>
          <form onSubmit={handleAuth} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <input
              type="password"
              placeholder="Admin token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              style={{
                padding: '0.75rem 1rem',
                border: '1px solid #8B8278',
                background: 'transparent',
                fontSize: '1rem',
                color: '#1A1A18',
              }}
              autoFocus
            />
            <button
              type="submit"
              style={{
                padding: '0.75rem 1.5rem',
                background: '#1A1A18',
                color: '#F5F0EB',
                border: 'none',
                cursor: 'pointer',
                fontSize: '0.9rem',
                letterSpacing: '0.1em',
                textTransform: 'uppercase',
              }}
            >
              Sign in
            </button>
          </form>
        </div>
      </main>
    );
  }

  return (
    <main style={{ minHeight: '100vh', background: '#F5F0EB', padding: '6rem 1rem 4rem' }}>
      <div style={{ maxWidth: 1280, margin: '0 auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '2rem' }}>
          <h1 style={{ fontFamily: 'var(--font-cormorant)', fontSize: '3rem', color: '#1A1A18', margin: 0 }}>
            Clips
          </h1>
          <button
            onClick={handleLogout}
            style={{ background: 'none', border: 'none', color: '#8B8278', cursor: 'pointer', fontSize: '0.85rem' }}
          >
            Sign out
          </button>
        </div>

        {/* Submit form */}
        <form onSubmit={handleSubmit} style={{ marginBottom: '3rem' }}>
          <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'stretch' }}>
            <input
              type="url"
              placeholder="https://youtube.com/watch?v=..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              style={{ flex: 1, padding: '0.85rem 1rem', border: '1px solid #8B8278', background: '#fff', fontSize: '1rem', color: '#1A1A18' }}
            />
            <select
              value={niche}
              onChange={(e) => setNiche(e.target.value)}
              style={{ padding: '0.85rem 1rem', border: '1px solid #8B8278', background: '#fff', fontSize: '0.9rem', color: '#1A1A18', cursor: 'pointer' }}
              title="Which YouTube channel to post clips to"
            >
              <option value="ai_business">→ AI / Business channel</option>
              <option value="movies">→ Movies channel</option>
            </select>
            <button
              type="submit"
              disabled={submitting}
              style={{ padding: '0.85rem 2rem', background: '#C9A96E', color: '#1A1A18', border: 'none', cursor: submitting ? 'wait' : 'pointer', fontSize: '0.9rem', letterSpacing: '0.1em', textTransform: 'uppercase', opacity: submitting ? 0.6 : 1 }}
            >
              {submitting ? 'Submitting…' : 'Submit'}
            </button>
          </div>

          <div style={{ marginTop: '0.5rem', textAlign: 'right' }}>
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              style={{ background: 'none', border: 'none', color: '#8B8278', fontSize: '0.75rem', cursor: 'pointer', textDecoration: 'underline' }}
            >
              {showAdvanced ? '▲ hide advanced options' : '▼ advanced options'}
            </button>
          </div>

          {showAdvanced && (
            <div style={{ marginTop: '1rem', padding: '1.5rem', background: '#fff', border: '1px solid #dcd5cb', display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '1.25rem' }}>
              <label style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', fontSize: '0.85rem', color: '#1A1A18' }}>
                <span style={{ color: '#8B8278', textTransform: 'uppercase', letterSpacing: '0.05em', fontSize: '0.7rem' }}>Number of clips</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={maxClips}
                  onChange={(e) => setMaxClips(parseInt(e.target.value || '6'))}
                  style={{ padding: '0.5rem 0.75rem', border: '1px solid #8B8278', background: 'transparent', fontSize: '0.9rem' }}
                />
                <span style={{ color: '#8B8278', fontSize: '0.7rem' }}>Default: 6. More = longer to render.</span>
              </label>

              <label style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', fontSize: '0.85rem', color: '#1A1A18' }}>
                <span style={{ color: '#8B8278', textTransform: 'uppercase', letterSpacing: '0.05em', fontSize: '0.7rem' }}>Extra hashtags</span>
                <input
                  type="text"
                  placeholder="#yourTag #another"
                  value={extraTags}
                  onChange={(e) => setExtraTags(e.target.value)}
                  style={{ padding: '0.5rem 0.75rem', border: '1px solid #8B8278', background: 'transparent', fontSize: '0.9rem' }}
                />
                <span style={{ color: '#8B8278', fontSize: '0.7rem' }}>Appended to every clip's caption.</span>
              </label>

              <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem', color: '#1A1A18', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={skipPosting}
                  onChange={(e) => setSkipPosting(e.target.checked)}
                />
                <span>
                  <strong>Render only — never post</strong>
                  <br/><span style={{ color: '#8B8278', fontSize: '0.7rem' }}>Generate clips for download, don't push to YouTube.</span>
                </span>
              </label>

              <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem', color: '#1A1A18', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={forceManual}
                  onChange={(e) => setForceManual(e.target.checked)}
                />
                <span>
                  <strong>Force manual approval</strong>
                  <br/><span style={{ color: '#8B8278', fontSize: '0.7rem' }}>Override auto-approve for this job — review before posting.</span>
                </span>
              </label>
            </div>
          )}
        </form>

        {error && (
          <div style={{ padding: '1rem', background: '#fee', color: '#a13434', marginBottom: '1.5rem' }}>
            {error}
          </div>
        )}

        {/* Settings */}
        <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'center', padding: '1rem', background: '#fff', border: '1px solid #dcd5cb', marginBottom: '2rem', fontSize: '0.85rem' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={autoApprove}
              onChange={async (e) => {
                setAutoApprove(e.target.checked);
                await updateSetting('auto_approve', e.target.checked ? 'true' : 'false');
              }}
            />
            <span style={{ color: '#1A1A18' }}>Auto-approve & post (no manual review)</span>
          </label>
          {autoApprove && (
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span style={{ color: '#8B8278' }}>Min virality score:</span>
              <input
                type="number"
                min={0}
                max={100}
                value={autoMinScore}
                onChange={async (e) => {
                  const v = parseInt(e.target.value || '0');
                  setAutoMinScore(v);
                  await updateSetting('auto_approve_min_score', String(v));
                }}
                style={{ width: 60, padding: '0.25rem 0.5rem', border: '1px solid #8B8278', background: 'transparent' }}
              />
            </label>
          )}
        </div>

        {/* Jobs grid */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: '2rem' }}>
          {/* Jobs list */}
          <div>
            <h2 style={{ fontSize: '1rem', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#8B8278', marginBottom: '1rem' }}>
              Jobs ({jobs.length})
            </h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {jobs.length === 0 && <p style={{ color: '#8B8278' }}>No jobs yet.</p>}
              {jobs.map((j) => (
                <div
                  key={j.id}
                  onClick={() => setSelectedJob(j.id)}
                  style={{
                    padding: '0.75rem 1rem',
                    background: selectedJob === j.id ? '#fff' : 'transparent',
                    border: `1px solid ${selectedJob === j.id ? '#C9A96E' : '#dcd5cb'}`,
                    cursor: 'pointer',
                    transition: 'all 0.2s',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem' }}>
                    <span style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                      <span style={{ fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: statusColor(j.status), fontWeight: 600 }}>
                        {j.status}
                      </span>
                      {j.style_preset && j.style_preset !== 'default' && (
                        <span style={{ fontSize: '0.65rem', padding: '1px 6px', background: '#C9A96E', color: '#1A1A18', letterSpacing: '0.05em', textTransform: 'uppercase' }}>
                          {j.style_preset}
                        </span>
                      )}
                    </span>
                    <span style={{ fontSize: '0.7rem', color: '#8B8278' }}>{fmtTime(j.created_at)}</span>
                  </div>
                  <div style={{ fontSize: '0.85rem', color: '#1A1A18', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {j.youtube_url}
                  </div>
                  {j.message && (
                    <div style={{ fontSize: '0.7rem', color: '#8B8278', marginTop: '0.25rem' }}>{j.message}</div>
                  )}
                  <div style={{ fontSize: '0.75rem', color: '#8B8278', marginTop: '0.25rem' }}>
                    {j.clip_count} clips
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDelete(j.id); }}
                      style={{ float: 'right', background: 'none', border: 'none', color: '#a13434', cursor: 'pointer', fontSize: '0.7rem' }}
                    >
                      delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Clips view */}
          <div>
            <h2 style={{ fontSize: '1rem', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#8B8278', marginBottom: '1rem' }}>
              {selectedJob ? `Clips (${clips.length})` : 'Select a job'}
            </h2>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '1.5rem' }}>
              {selectedJob && clips.length === 0 && (
                <p style={{ color: '#8B8278' }}>No clips yet — job may still be running.</p>
              )}
              {clips.map((c) => (
                <div key={c.id} style={{ background: '#fff', padding: '1rem', border: '1px solid #dcd5cb' }}>
                  <video
                    src={`${API}/jobs/${selectedJob}/clips/${c.id}/file?token=${encodeURIComponent(token)}`}
                    controls
                    style={{ width: '100%', aspectRatio: '9/16', background: '#000' }}
                    preload="metadata"
                  />
                  <div style={{ marginTop: '0.75rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                      <strong style={{ fontSize: '0.95rem', color: '#1A1A18' }}>{c.title}</strong>
                      <span style={{ fontSize: '0.85rem', color: '#C9A96E', fontWeight: 600 }}>{c.score}</span>
                    </div>
                    <p style={{ fontSize: '0.8rem', color: '#8B8278', marginTop: '0.5rem', lineHeight: 1.4 }}>{c.caption}</p>
                    <p style={{ fontSize: '0.75rem', color: '#8B8278', marginTop: '0.5rem', fontStyle: 'italic' }}>{c.reason}</p>
                    <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem' }}>
                      <button
                        onClick={() => handleApprove(c.id)}
                        disabled={c.approved === 1}
                        style={{
                          padding: '0.4rem 0.8rem',
                          background: c.approved ? '#3b7a3a' : '#1A1A18',
                          color: '#fff',
                          border: 'none',
                          fontSize: '0.7rem',
                          letterSpacing: '0.05em',
                          textTransform: 'uppercase',
                          cursor: c.approved ? 'default' : 'pointer',
                        }}
                      >
                        {c.approved ? 'Approved' : 'Approve'}
                      </button>
                      <a
                        href={`${API}/jobs/${selectedJob}/clips/${c.id}/file?token=${encodeURIComponent(token)}`}
                        download={c.filename}
                        style={{
                          padding: '0.4rem 0.8rem',
                          background: 'transparent',
                          color: '#1A1A18',
                          border: '1px solid #1A1A18',
                          fontSize: '0.7rem',
                          letterSpacing: '0.05em',
                          textTransform: 'uppercase',
                          textDecoration: 'none',
                        }}
                      >
                        Download
                      </a>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
