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
  const [sourceType, setSourceType] = useState<'github' | 'upload'>('github');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Dynamic user inputs - NO static or test values
  const [repoUrl, setRepoUrl] = useState('');
  const [branch, setBranch] = useState('');
  const [token, setToken] = useState('');
  const [projectName, setProjectName] = useState('');
  const [targetService, setTargetService] = useState('');
  const [containerPort, setContainerPort] = useState<string>('');
  const [healthPath, setHealthPath] = useState('');
  const [engine, setEngine] = useState('pumba');
  const [faultType, setFaultType] = useState('container-pause');
  const [duration, setDuration] = useState(15);
  const [maxErrorRate, setMaxErrorRate] = useState(10);
  const [autoCleanup, setAutoCleanup] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitErr, setSubmitErr] = useState<string | null>(null);

  const pipelines = usePipelines();
  const currentPipeline = usePipeline(selectedId || (pipelines.data?.[0]?.id ?? ''));
  const activeData = currentPipeline.data;

  async function handleLaunchPipeline(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitErr(null);

    try {
      if (sourceType === 'github') {
        if (!repoUrl.trim()) {
          throw new Error('Please enter a GitHub repository URL or owner/repo');
        }

        const params = new URLSearchParams({
          repo_url: repoUrl.trim(),
          branch: branch.trim(),
          project_name: projectName.trim(),
          target_service: targetService.trim(),
          container_port: containerPort ? String(containerPort) : '80',
          health_path: healthPath.trim() || '/',
          fault_engine: engine,
          fault_type: faultType,
          fault_duration: String(duration),
          auto_cleanup: String(autoCleanup),
          max_error_rate: String(maxErrorRate / 100),
        });

        const res = await api<{ pipeline_id: string }>(
          `/api/v1/pipeline/github?${params.toString()}`,
          {
            method: 'POST',
            timeoutMs: 120_000,
            headers: token.trim() ? { 'X-GitHub-Token': token.trim() } : undefined,
          }
        );

        setSelectedId(res.pipeline_id);
        pipelines.refetch();
      } else {
        // Upload mode
        if (!file) {
          throw new Error('Please select a project repository archive (.zip or .tar.gz)');
        }

        const arrayBuffer = await file.arrayBuffer();
        const derivedName = projectName.trim() || file.name.replace(/\.(zip|tar|gz|tar\.gz)$/i, '');

        const params = new URLSearchParams({
          project_name: derivedName,
          target_service: targetService.trim(),
          container_port: containerPort ? String(containerPort) : '80',
          health_path: healthPath.trim() || '/',
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
            timeoutMs: 120_000,
            body: arrayBuffer,
            headers: { 'Content-Type': 'application/octet-stream' },
          }
        );

        setSelectedId(res.pipeline_id);
        pipelines.refetch();
      }
    } catch (err: any) {
      setSubmitErr(err.message || 'Failed to start pipeline');
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

  async function handleApprove(pipelineId: string) {
    try {
      await api(`/api/v1/pipeline/${pipelineId}/approve`, { method: 'POST' });
      currentPipeline.refetch();
      pipelines.refetch();
    } catch (err: any) {
      alert(err.message || 'Pipeline approval failed');
    }
  }

  return (
    <div>
      <h2>CI/CD Multi-Service Chaos Pipeline</h2>
      <p className="sub">
        Build and containerize microservices across your repository in Docker, connect them in an isolated network, and execute automated chaos resilience tests.
      </p>

      <div className="g2">
        {/* Form Column */}
        <div className="card">
          <h3 style={{ marginTop: 0 }}>🚀 Configure &amp; Launch Pipeline</h3>

          {/* Source Type Selector */}
          <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
            <button
              type="button"
              className={`btn ${sourceType === 'github' ? '' : 's'}`}
              onClick={() => setSourceType('github')}
            >
              🌐 GitHub Repository
            </button>
            <button
              type="button"
              className={`btn ${sourceType === 'upload' ? '' : 's'}`}
              onClick={() => setSourceType('upload')}
            >
              📁 Upload Repository Archive
            </button>
          </div>

          <form onSubmit={handleLaunchPipeline}>
            {sourceType === 'github' ? (
              <>
                <label>GitHub Repository URL *</label>
                <input
                  type="text"
                  value={repoUrl}
                  onChange={(e) => setRepoUrl(e.target.value)}
                  placeholder="https://github.com/organization/repository"
                  required
                />
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginTop: '8px' }}>
                  <div>
                    <label>Branch or Ref</label>
                    <input
                      type="text"
                      value={branch}
                      onChange={(e) => setBranch(e.target.value)}
                      placeholder="main (optional)"
                    />
                  </div>
                  <div>
                    <label>Personal Access Token</label>
                    <input
                      type="password"
                      value={token}
                      onChange={(e) => setToken(e.target.value)}
                      placeholder="Optional (for private repos)"
                    />
                  </div>
                </div>
              </>
            ) : (
              <>
                <label>Repository Archive (.zip / .tar.gz) *</label>
                <input
                  type="file"
                  accept=".zip,.tar,.gz,.tar.gz"
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                  required
                />
                <small style={{ color: 'var(--mu)', display: 'block', marginTop: '4px' }}>
                  Select an archive containing your project. If it has a docker-compose.yml or multiple Dockerfiles, all services will be containerized.
                </small>
              </>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginTop: '12px' }}>
              <div>
                <label>Project Name</label>
                <input
                  type="text"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  placeholder="Leave blank to auto-detect"
                />
              </div>
              <div>
                <label>Target Microservice to Attack</label>
                <input
                  type="text"
                  value={targetService}
                  onChange={(e) => setTargetService(e.target.value)}
                  placeholder="Optional: e.g. api, auth, web"
                />
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginTop: '8px' }}>
              <div>
                <label>Default Container Port</label>
                <input
                  type="number"
                  value={containerPort}
                  onChange={(e) => setContainerPort(e.target.value)}
                  placeholder="Auto-detect or e.g. 80, 8000"
                />
              </div>
              <div>
                <label>Healthcheck Path</label>
                <input
                  type="text"
                  value={healthPath}
                  onChange={(e) => setHealthPath(e.target.value)}
                  placeholder="Default /"
                />
              </div>
            </div>

            <label style={{ marginTop: '12px' }}>Chaos Engine</label>
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
                Auto-remove all service containers &amp; proxies when complete
              </label>
            </div>

            {submitErr && <p className="err" style={{ marginTop: '12px' }}>{submitErr}</p>}

            <button
              type="submit"
              className="btn"
              disabled={submitting}
              style={{ width: '100%', marginTop: '16px' }}
            >
              {submitting ? 'Preparing source for approval...' : 'Submit Pipeline For Approval'}
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
                  <Badge v={activeData.status} />
                </div>
              </div>

              {activeData.status === 'PENDING_APPROVAL' && (
                <div style={{ padding: '12px', marginBottom: '16px', border: '1px solid var(--wn)', borderRadius: '8px' }}>
                  <b>Approval required</b>
                  <p style={{ margin: '6px 0 10px', fontSize: '13px' }}>
                    An approver other than the requester must approve before source builds or chaos execution begin.
                  </p>
                  <button className="btn" onClick={() => handleApprove(activeData.id)}>
                    Approve Pipeline &amp; Start
                  </button>
                </div>
              )}

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

              {/* Multi-Service Topology Card */}
              {activeData.services && activeData.services.length > 0 && (
                <div style={{ marginBottom: '16px', padding: '12px', borderRadius: '8px', border: '1px solid var(--bd)', background: 'var(--sf)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                    <b style={{ fontSize: '13px' }}>📦 Discovered Microservices ({activeData.services.length})</b>
                    {activeData.services.some((s: any) => s.status === 'running') && (
                      <button
                        className="btn s"
                        style={{ fontSize: '11px', padding: '2px 8px' }}
                        onClick={() => handleTeardown(activeData.id)}
                      >
                        Stop All Containers
                      </button>
                    )}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {activeData.services.map((svc: any) => (
                      <div
                        key={svc.name}
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          padding: '6px 10px',
                          borderRadius: '6px',
                          background: svc.primary ? 'rgba(99, 102, 241, 0.12)' : 'var(--bg)',
                          border: svc.primary ? '1px solid var(--ac)' : '1px solid var(--bd)',
                          fontSize: '12px',
                        }}
                      >
                        <div>
                          <b>{svc.name}</b> {svc.primary && <span style={{ color: 'var(--ac)', fontWeight: 600 }}>[🎯 Chaos Target]</span>}
                          <span style={{ color: 'var(--mu)', marginLeft: '8px' }}>
                            ({svc.build_path && svc.build_path !== '.' ? svc.build_path + '/' : ''}{svc.dockerfile || svc.image})
                          </span>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ color: 'var(--mu)' }}>Port: {svc.port || 'N/A'}</span>
                          <span
                            style={{
                              padding: '2px 6px',
                              borderRadius: '4px',
                              fontSize: '11px',
                              background: svc.status === 'running' ? 'var(--ok)' : svc.status === 'built' ? 'var(--run)' : 'var(--mu)',
                              color: '#fff',
                            }}
                          >
                            {svc.status}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

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
                Enter a GitHub repository or upload project archive to build microservices in Docker and run chaos tests.
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
          <ErrorState e={pipelines.error} />
        ) : !pipelines.data?.length ? (
          <p style={{ color: 'var(--mu)' }}>No pipeline runs yet. Start your first run above!</p>
        ) : (
          <div className="tw">
            <table>
              <thead>
                <tr>
                  <th>Pipeline ID</th>
                  <th>Project</th>
                  <th>Services</th>
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
                      <span className="b mu">
                        {p.services?.length ? `${p.services.length} services` : '1 service'}
                      </span>
                    </td>
                    <td>
                      <Badge v={p.status} />
                    </td>
                    <td>
                      {p.config?.fault_engine} / {p.config?.fault_type}
                    </td>
                    <td>{new Date(p.created_at).toLocaleString()}</td>
                    <td>
                      {p.verdict ? (
                        <span style={{ color: p.verdict.passed ? 'var(--ok)' : 'var(--er)', fontWeight: 600 }}>
                          {p.status === 'DRY_RUN' ? 'DRY RUN' : p.verdict.passed ? 'PASSED' : 'FAILED'}
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
