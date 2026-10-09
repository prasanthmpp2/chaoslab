import React, { useState } from 'react';
import { usePipelines, usePipeline } from '../api/hooks';
import { api } from '../api/client';
import { Badge, Loading, ErrorState } from '../components/ui';
import { Link } from 'react-router-dom';

const FAULT_OPTIONS: Record<string, { label: string; types: { id: string; label: string; defaultParams: any }[] }> = {
  pumba: {
    label: 'Pumba (Docker Container Chaos)',
    types: [
      { id: 'container-pause', label: 'Container Pause (Freeze container during traffic)', defaultParams: {} },
      { id: 'container-kill', label: 'Container Kill (SIGKILL & Verify recovery)', defaultParams: { signal: 'SIGKILL' } },
      { id: 'cpu-stress', label: 'CPU Stress (Simulate high CPU spike)', defaultParams: { workers: 1 } },
    ],
  },
  toxiproxy: {
    label: 'Toxiproxy (Network Fault Injection)',
    types: [
      { id: 'network-latency', label: 'Network Latency (Inject 500ms packet delay)', defaultParams: { latencyMs: 500, jitterMs: 50 } },
      { id: 'connection-timeout', label: 'Connection Timeout (Simulate blackhole drop)', defaultParams: { timeoutMs: 3000 } },
      { id: 'bandwidth-limit', label: 'Bandwidth Throttle (Rate limit to 100 KB/s)', defaultParams: { rateKbps: 100 } },
    ],
  },
};

export default function Pipeline() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState('order-service');
  const [containerPort, setContainerPort] = useState(80);
  const [healthPath, setHealthPath] = useState('/');
  const [engine, setEngine] = useState('pumba');
  const [faultType, setFaultType] = useState('container-pause');
  const [duration, setDuration] = useState(10);
  const [maxErrorRate, setMaxErrorRate] = useState(10);
  const [autoCleanup, setAutoCleanup] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitErr, setSubmitErr] = useState<string | null>(null);

  const pipelines = usePipelines();
  const currentPipeline = usePipeline(selectedId || (pipelines.data?.[0]?.id ?? ''));

  const activeData = currentPipeline.data;

  async function handleSampleRun(sampleType: 'nginx' | 'python') {
    setSubmitting(true);
    setSubmitErr(null);
    try {
      const res = await api<{ pipeline_id: string }>(
        `/api/v1/pipeline/sample?sample_type=${sampleType}&fault_engine=${engine}&fault_type=${faultType}&fault_duration=${duration}&auto_cleanup=${autoCleanup}`,
        { method: 'POST' }
      );
      setSelectedId(res.pipeline_id);
      pipelines.refetch();
    } catch (e: any) {
      setSubmitErr(e.message || 'Failed to start sample pipeline');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleUploadRun(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitErr(null);

    try {
      let arrayBuffer: ArrayBuffer;
      if (file) {
        arrayBuffer = await file.arrayBuffer();
      } else {
        // Fallback: create minimal web app payload
        const dummy = "<!DOCTYPE html><html><body><h1>Resilient Service</h1></body></html>";
        arrayBuffer = new TextEncoder().encode(dummy).buffer;
      }

      const params = new URLSearchParams({
        project_name: projectName,
        container_port: String(containerPort),
        health_path: healthPath,
        fault_engine: engine,
        fault_type: faultType,
        fault_duration: String(duration),
        auto_cleanup: String(autoCleanup),
        max_error_rate: String(maxErrorRate / 100),
      });

      const res = await api<{ pipeline_id: string }>(
        `/api/v1/pipeline/upload?${params.toString()}`,
        {
          method: 'POST',
          body: arrayBuffer,
          headers: { 'Content-Type': 'application/octet-stream' },
        }
      );

      setSelectedId(res.pipeline_id);
      pipelines.refetch();
    } catch (err: any) {
      setSubmitErr(err.message || 'Failed to upload and start pipeline');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleTeardown(pipelineId: string) {
    try {
      await api(`/api/v1/pipeline/${pipelineId}/teardown`, { method: 'POST' });
      currentPipeline.refetch();
      pipelines.refetch();
    } catch (err: any) {
      alert(err.message || 'Teardown failed');
    }
  }

  return (
    <div>
      <h2>CI/CD Chaos Pipeline</h2>
      <p className="sub">
        Upload application projects, automatically build Docker containers, and test resilience under real chaos fault injection.
      </p>

      <div className="g2">
        {/* Form Column */}
        <div className="card">
          <h3 style={{ marginTop: 0 }}>🚀 Launch New Pipeline</h3>

          <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
            <button
              type="button"
              className="btn s"
              onClick={() => {
                setProjectName('nginx-web-app');
                setContainerPort(80);
                setHealthPath('/');
                handleSampleRun('nginx');
              }}
              disabled={submitting}
            >
              ⚡ Quick Run (Nginx Service)
            </button>
            <button
              type="button"
              className="btn s"
              onClick={() => {
                setProjectName('python-api');
                setContainerPort(8000);
                setHealthPath('/');
                handleSampleRun('python');
              }}
              disabled={submitting}
            >
              🐍 Quick Run (Python API)
            </button>
          </div>

          <form onSubmit={handleUploadRun}>
            <label>Project Name</label>
            <input
              type="text"
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              placeholder="e.g. order-service"
              required
            />

            <label>Project Source Archive (.zip / .tar.gz)</label>
            <input
              type="file"
              accept=".zip,.tar,.gz,.tar.gz"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
            <small style={{ color: 'var(--mu)', display: 'block', marginTop: '4px' }}>
              Upload source code with a Dockerfile, or let ChaosLab auto-generate one for static/Python projects.
            </small>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginTop: '8px' }}>
              <div>
                <label>Container Port</label>
                <input
                  type="number"
                  value={containerPort}
                  onChange={(e) => setContainerPort(Number(e.target.value))}
                  required
                />
              </div>
              <div>
                <label>Healthcheck Path</label>
                <input
                  type="text"
                  value={healthPath}
                  onChange={(e) => setHealthPath(e.target.value)}
                  placeholder="/"
                  required
                />
              </div>
            </div>

            <label>Chaos Engine</label>
            <select
              value={engine}
              onChange={(e) => {
                const eng = e.target.value;
                setEngine(eng);
                setFaultType(FAULT_OPTIONS[eng]?.types[0]?.id || '');
              }}
            >
              {Object.entries(FAULT_OPTIONS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v.label}
                </option>
              ))}
            </select>

            <label>Fault Injection Type</label>
            <select
              value={faultType}
              onChange={(e) => setFaultType(e.target.value)}
            >
              {FAULT_OPTIONS[engine]?.types.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginTop: '8px' }}>
              <div>
                <label>Fault Duration (seconds)</label>
                <input
                  type="number"
                  min="3"
                  max="60"
                  value={duration}
                  onChange={(e) => setDuration(Number(e.target.value))}
                />
              </div>
              <div>
                <label>Max Error Tolerance (%)</label>
                <input
                  type="number"
                  min="1"
                  max="100"
                  value={maxErrorRate}
                  onChange={(e) => setMaxErrorRate(Number(e.target.value))}
                />
              </div>
            </div>

            <div style={{ marginTop: '16px' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  style={{ width: 'auto' }}
                  checked={autoCleanup}
                  onChange={(e) => setAutoCleanup(e.target.checked)}
                />
                Auto-remove test container &amp; proxies when complete
              </label>
            </div>

            {submitErr && <p className="err" style={{ marginTop: '12px' }}>{submitErr}</p>}

            <button
              type="submit"
              className="btn"
              disabled={submitting}
              style={{ width: '100%', marginTop: '16px' }}
            >
              {submitting ? 'Containerizing & Testing...' : '🚀 Build & Run Chaos Pipeline'}
            </button>
          </form>
        </div>

        {/* Live Pipeline View Column */}
        <div>
          {activeData ? (
            <div className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                <div>
                  <h3 style={{ margin: 0 }}>{activeData.project_name}</h3>
                  <small style={{ color: 'var(--mu)' }}>Pipeline ID: {activeData.id}</small>
                </div>
                <div>
                  <Badge status={activeData.status} />
                </div>
              </div>

              {/* Stepper */}
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '16px' }}>
                {activeData.stages?.map((s: any) => {
                  const isDone = s.status === 'completed';
                  const isRun = s.status === 'running';
                  const isFail = s.status === 'failed';
                  return (
                    <span
                      key={s.id}
                      style={{
                        padding: '4px 10px',
                        borderRadius: '99px',
                        fontSize: '12px',
                        border: '1px solid var(--bd)',
                        background: isRun ? 'var(--run)' : isDone ? 'var(--ok)' : isFail ? 'var(--er)' : 'transparent',
                        color: isRun || isDone || isFail ? '#fff' : 'var(--mu)',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                      }}
                    >
                      {isDone && '✓'} {isRun && '●'} {isFail && '✕'} {s.name}
                    </span>
                  );
                })}
              </div>

              {/* Verdict Summary Card */}
              {activeData.verdict && (
                <div
                  style={{
                    padding: '12px 16px',
                    borderRadius: '8px',
                    marginBottom: '16px',
                    background: activeData.verdict.passed ? 'rgba(52, 211, 153, 0.15)' : 'rgba(248, 113, 113, 0.15)',
                    border: `1px solid ${activeData.verdict.passed ? 'var(--ok)' : 'var(--er)'}`,
                  }}
                >
                  <b style={{ color: activeData.verdict.passed ? 'var(--ok)' : 'var(--er)' }}>
                    {activeData.verdict.passed ? '✅ VERDICT: RESILIENCE VERIFIED' : '❌ VERDICT: HYPOTHESIS FAILED'}
                  </b>
                  <p style={{ margin: '6px 0 0', fontSize: '13px' }}>{activeData.verdict.summary}</p>

                  <div style={{ display: 'flex', gap: '16px', marginTop: '10px', fontSize: '12px' }}>
                    <span>Baseline: <b>{(activeData.verdict.baseline_error_rate * 100).toFixed(1)}% err</b></span>
                    <span>During Chaos: <b>{(activeData.verdict.during_error_rate * 100).toFixed(1)}% err</b></span>
                    <span>Recovery: <b>{(activeData.verdict.recovery_error_rate * 100).toFixed(1)}% err</b></span>
                  </div>

                  {activeData.experiment_run_id && (
                    <div style={{ marginTop: '8px' }}>
                      <Link to={`/runs/${activeData.experiment_run_id}`} style={{ fontSize: '12px' }}>
                        View Deep Experiment Run Telemetry &rarr;
                      </Link>
                    </div>
                  )}
                </div>
              )}

              {/* Container Details */}
              <div style={{ fontSize: '12px', color: 'var(--mu)', marginBottom: '12px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  Container: <code>{activeData.container?.name || 'N/A'}</code> | Status: <b>{activeData.container?.status}</b>
                </div>
                {activeData.container?.status === 'running' && (
                  <button
                    className="btn s"
                    style={{ fontSize: '11px', padding: '2px 8px' }}
                    onClick={() => handleTeardown(activeData.id)}
                  >
                    Stop Container
                  </button>
                )}
              </div>

              {/* Execution Console Logs */}
              <label>Build &amp; Test Execution Console</label>
              <div
                className="log"
                style={{
                  background: 'var(--bg)',
                  padding: '12px',
                  borderRadius: '6px',
                  border: '1px solid var(--bd)',
                  maxHeight: '280px',
                  overflowY: 'auto',
                }}
              >
                {activeData.logs?.map((l: any, i: number) => (
                  <div key={i} style={{ color: l.level === 'ERROR' ? 'var(--er)' : l.level === 'WARNING' ? 'var(--wn)' : 'var(--tx)' }}>
                    <span style={{ color: 'var(--mu)', marginRight: '6px' }}>[{l.timestamp?.slice(11, 19)}]</span>
                    <b>[{l.level}]</b> {l.message}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="card">
              <p style={{ color: 'var(--mu)', textAlign: 'center', margin: '40px 0' }}>
                Select a pipeline or launch a new run to view real-time container build and chaos test logs.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Pipeline History Table */}
      <div className="card" style={{ marginTop: '24px' }}>
        <h3 style={{ marginTop: 0 }}>Pipeline History</h3>
        {pipelines.isLoading ? (
          <Loading />
        ) : pipelines.isError ? (
          <ErrorState message="Could not load pipeline history" />
        ) : !pipelines.data?.length ? (
          <p style={{ color: 'var(--mu)' }}>No pipeline runs yet. Start your first run above!</p>
        ) : (
          <div className="tw">
            <table>
              <thead>
                <tr>
                  <th>Pipeline ID</th>
                  <th>Project</th>
                  <th>Status</th>
                  <th>Chaos Engine</th>
                  <th>Created At</th>
                  <th>Verdict</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {pipelines.data.map((p: any) => (
                  <tr key={p.id}>
                    <td>
                      <code>{p.id}</code>
                    </td>
                    <td><b>{p.project_name}</b></td>
                    <td>
                      <Badge status={p.status} />
                    </td>
                    <td>
                      {p.config?.fault_engine} / {p.config?.fault_type}
                    </td>
                    <td>{new Date(p.created_at).toLocaleString()}</td>
                    <td>
                      {p.verdict ? (
                        <span style={{ color: p.verdict.passed ? 'var(--ok)' : 'var(--er)', fontWeight: 600 }}>
                          {p.verdict.passed ? 'PASSED' : 'FAILED'}
                        </span>
                      ) : (
                        '-'
                      )}
                    </td>
                    <td>
                      <button
                        className="btn s"
                        style={{ padding: '3px 8px', fontSize: '12px' }}
                        onClick={() => setSelectedId(p.id)}
                      >
                        Inspect
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
