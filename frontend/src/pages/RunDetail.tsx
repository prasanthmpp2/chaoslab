import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useCancelRun, useProbeCalls, useProbes, useRetryCleanup, useRun, useRunEvents, useRunReport } from '../api/hooks';
import { api, apiBase } from '../api/client';
import type { ProbeCall } from '../api/types';
import { Badge, Confirm, ErrorState, Loading, RecoveryScorecard, Table } from '../components/ui';

export default function RunDetail() {
  const [artifactError, setArtifactError] = useState('');
  const { runId = '' } = useParams();
  const run = useRun(runId);
  const active = !!run.data && ['QUEUED', 'VALIDATING', 'RUNNING_BASELINE', 'INJECTING', 'OBSERVING', 'ABORTING', 'CLEANING_UP', 'VERIFYING_RECOVERY'].includes(run.data.status);
  const events = useRunEvents(runId, active);
  const probes = useProbes(runId);
  const calls = useProbeCalls(runId);
  const report = useRunReport(runId);
  const cancel = useCancelRun();
  const retry = useRetryCleanup();

  if (run.isLoading) return <Loading />;
  if (run.isError) return <ErrorState e={run.error} />;
  const result = run.data!;
  const definition = result.definition_snapshot;
  const target = `${definition.spec.target.environment}:${definition.spec.target.service}`;
  const callRows = calls.data?.pages.flat() ?? [];

  function downloadReport() {
    if (!report.data) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report.data, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `run-${result.id}-evidence.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function downloadArtifact(artifactId: string, filename: string) {
    setArtifactError('');
    try {
      const key = localStorage.getItem('api_key');
      const response = await fetch(`${apiBase}/api/v1/runs/${encodeURIComponent(result.id)}/artifacts/${encodeURIComponent(artifactId)}/download`, {
        headers: key ? { 'X-API-Key': key } : {},
      });
      if (!response.ok) throw new Error(`Artifact download failed (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setArtifactError((error as Error).message);
    }
  }

  async function exportProbeCalls(format: 'json' | 'csv') {
    setArtifactError('');
    try {
      const records: ProbeCall[] = [];
      let afterId = 0;
      while (true) {
        const page = await api<ProbeCall[]>(`/api/v1/runs/${encodeURIComponent(result.id)}/probe-calls?limit=500&after_id=${afterId}`);
        records.push(...page);
        if (page.length < 500) break;
        afterId = page[page.length - 1].id;
      }
      let content: string;
      let mime: string;
      if (format === 'json') {
        content = JSON.stringify({ run_id: result.id, capture_scope: 'synthetic_http_probes', calls: records }, null, 2);
        mime = 'application/json';
      } else {
        const quote = (value: unknown) => `"${String(value ?? '').replace(/"/g, '""')}"`;
        content = [['id', 'captured_at', 'probe', 'phase', 'duration_ms', 'ok', 'request', 'response'],
          ...records.map((call) => [call.id, call.captured_at, call.probe_name, call.phase, call.duration_ms,
            call.ok, JSON.stringify(call.request), JSON.stringify(call.response)])]
          .map((row) => row.map(quote).join(',')).join('\n');
        mime = 'text/csv';
      }
      const url = URL.createObjectURL(new Blob([content], { type: mime }));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `run-${result.id}-probe-calls.${format}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setArtifactError((error as Error).message);
    }
  }

  return <>
    <h2>Run {result.id}</h2>
    <p className="sub">{definition.metadata.name} · {definition.spec.fault.engine} · {target}</p>
    <div className="grid">
      <div className="card"><b>Execution status</b><br /><Badge v={result.status} /></div>
      <div className="card"><b>Resilience verdict</b><br /><Badge v={result.outcome} /></div>
      <div className="card"><b>Cleanup status</b><br /><Badge v={result.cleanup_status} cleanup /></div>
      <div className="card"><b>Recovery duration</b><br />{result.recovery_duration_seconds == null ? 'Not measured' : `${result.recovery_duration_seconds.toFixed(1)} s`}</div>
    </div>
    {result.error_summary && <div className="card warn"><b>Run notes</b><p>{result.error_summary}</p></div>}
    {result.cleanup_status === 'FAILED' && <div className="card warn">
      <b>Cleanup needs attention.</b> The fault may still be active on {target}.{' '}
      <Confirm title="Retry cleanup?" text="Requests fault removal again. Status stays pending until the backend verifies recovery." action="Retry cleanup" onYes={() => retry.mutate(result.id)}>Retry cleanup</Confirm>
    </div>}
    {active && <p>Current stage: <Badge v={result.status} />{' '}
      {result.status === 'ABORTING' ? <span className="b wn">Cancellation pending</span> :
        <Confirm title="Cancel this run?" text="The run remains tracked while cleanup is performed and verified." action="Cancel run" onYes={() => cancel.mutate(result.id)}>Cancel run</Confirm>}
    </p>}

    <RecoveryScorecard card={result.scorecard ?? report.data?.run.scorecard} />
    <div className="card">
      <div className="section-head"><div><h3>Measured probe evidence</h3><p className="mu">Raw phase measurements, tolerances, and saved sample artifact references.</p></div>
      <button className="btn s" type="button" disabled={!report.data} onClick={downloadReport}>{report.isLoading ? 'Loading report…' : 'Export run evidence (JSON)'}</button></div>
      {probes.isError ? <ErrorState e={probes.error} /> : probes.data?.length ? <Table
        head={['Probe', 'Phase', 'Measurement', 'Tolerance', 'Result', 'Details', 'Evidence']}
        rows={probes.data.map((item) => [item.probe_name, item.phase, item.measurement ?? '—', item.tolerance ?? '—',
          <Badge v={item.status} />, JSON.stringify(item.detail), item.evidence_ref ?? '—'])}
      /> : <p className="mu">No probe measurements were collected.</p>}
    </div>
    <div className="card">
      <h3>HTTP probe request and response evidence</h3>
      <p className="mu">These are synthetic requests generated by this experiment’s probes. Response bodies are omitted unless body capture is enabled in backend settings.</p>
      <p><button className="btn s" type="button" onClick={() => exportProbeCalls('json')}>Export all calls (JSON)</button>{' '}
        <button className="btn s" type="button" onClick={() => exportProbeCalls('csv')}>Export all calls (CSV)</button></p>
      {artifactError && <p className="err" role="alert">{artifactError}</p>}
      {calls.isError ? <ErrorState e={calls.error} /> : callRows.length ? <Table
        head={['Time', 'Probe', 'Phase', 'Request', 'Response', 'Duration', 'Result']}
        rows={callRows.map((call) => [new Date(call.captured_at).toLocaleString(), call.probe_name, call.phase,
          `${call.request.method ?? 'HTTP'} ${call.request.url ?? ''}`,
          call.response.status_code ?? call.response.error ?? 'No response',
          `${call.duration_ms.toFixed(1)} ms`, <Badge v={call.ok ? 'PASSED' : 'FAILED'} />])}
      /> : <p className="mu">No HTTP probe request records are available. Older runs and container-state probes do not have request/response records.</p>}
      {calls.hasNextPage && <button className="btn s" disabled={calls.isFetchingNextPage} onClick={() => calls.fetchNextPage()}>{calls.isFetchingNextPage ? 'Loading…' : 'Load more probe calls'}</button>}
      {callRows.some((call) => call.response.body) && <details><summary>Captured response body snippets</summary>
        {callRows.filter((call) => call.response.body).map((call) => <details key={call.id}>
          <summary>{call.probe_name} · {call.phase} · HTTP {call.response.status_code ?? '—'}{call.response.body_truncated ? ' · truncated' : ''}</summary>
          <pre className="log">{call.response.body}</pre>
        </details>)}
      </details>}
    </div>
    <div className="card"><h3>Saved evidence files</h3>
      {artifactError && <p className="err" role="alert">{artifactError}</p>}
      {report.data?.artifacts.length ? <Table head={['File', 'Size', 'SHA-256', 'Download']}
        rows={report.data.artifacts.map((artifact) => [artifact.name, `${artifact.size_bytes} bytes`, artifact.sha256,
          <button className="btn s" type="button" onClick={() => downloadArtifact(artifact.id, artifact.name)}>Download</button>])}
      /> : <p className="mu">No evidence files were saved for this run.</p>}
    </div>
    <div className="g2">
      <div className="card"><h3>Fault events</h3>{report.data?.faults.length ? <Table
        head={['Engine', 'Fault', 'Target', 'State', 'Cleanup error']}
        rows={report.data.faults.map((fault) => [fault.engine, fault.type, fault.target_id, <Badge v={fault.state} />, fault.cleanup_error ?? '—'])}
      /> : <p className="mu">No fault was registered for this run.</p>}</div>
      <div className="card"><h3>Run timeline</h3>{events.isError ? <ErrorState e={events.error} /> : <div className="log">
        {events.data?.map((event) => `${event.created_at} [${event.event_type}] ${event.message}`).join('\n') || 'No events yet.'}
      </div>}</div>
    </div>
    <p><Link to="/runs">Back to history</Link></p>
  </>;
}
