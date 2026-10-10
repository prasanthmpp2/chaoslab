import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useCancelRun, useProbes, useRetryCleanup, useRun, useRunEvents, useRunReport } from '../api/hooks';
import { apiBase } from '../api/client';
import { Badge, Confirm, ErrorState, Loading, RecoveryScorecard, Table } from '../components/ui';

export default function RunDetail() {
  const [artifactError, setArtifactError] = useState('');
  const { runId = '' } = useParams();
  const run = useRun(runId);
  const active = !!run.data && ['QUEUED', 'VALIDATING', 'RUNNING_BASELINE', 'INJECTING', 'OBSERVING', 'ABORTING', 'CLEANING_UP', 'VERIFYING_RECOVERY'].includes(run.data.status);
  const events = useRunEvents(runId, active);
  const probes = useProbes(runId);
  const report = useRunReport(runId);
  const cancel = useCancelRun();
  const retry = useRetryCleanup();

  if (run.isLoading) return <Loading />;
  if (run.isError) return <ErrorState e={run.error} />;
  const result = run.data!;
  const definition = result.definition_snapshot;
  const target = `${definition.spec.target.environment}:${definition.spec.target.service}`;

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
