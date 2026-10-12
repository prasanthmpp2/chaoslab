import { Link, useParams } from 'react-router-dom';
import { useExperiment, usePipeline, usePipelines } from '../api/hooks';
import { api } from '../api/client';
import { Badge, ErrorState, Loading } from '../components/ui';

export default function PipelineDetail() {
  const { pipelineId = '' } = useParams();
  const pipeline = usePipeline(pipelineId);
  const pipelines = usePipelines();
  const data = pipeline.data;
  const experiment = useExperiment(data?.experiment_id || '');

  function downloadExperiment() {
    const definition = experiment.data?.definition;
    if (!definition) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(definition, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'experiment.json';
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function start() {
    if (!window.confirm('Start building this project’s containers and run its configured chaos test?')) return;
    try {
      await api(`/api/v1/pipeline/${encodeURIComponent(pipelineId)}/start`, { method: 'POST' });
      await pipeline.refetch();
      await pipelines.refetch();
    } catch (error) {
      window.alert((error as Error).message || 'Pipeline could not be started');
    }
  }

  async function teardown() {
    if (!window.confirm('Stop and remove the containers created by this pipeline?')) return;
    try {
      await api(`/api/v1/pipeline/${encodeURIComponent(pipelineId)}/teardown`, { method: 'POST' });
      await pipeline.refetch();
      await pipelines.refetch();
    } catch (error) {
      window.alert((error as Error).message || 'Cleanup could not be started');
    }
  }

  if (pipeline.isLoading) return <Loading />;
  if (pipeline.isError) return <ErrorState e={pipeline.error} />;
  if (!data) return <div className="card">Pipeline not found.</div>;

  return (
    <div>
      <p><Link to="/pipeline">← Back to Pipeline Projects</Link></p>
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'start' }}>
          <div>
            <h2 style={{ marginTop: 0 }}>{data.project_name}</h2>
            <p className="sub">Pipeline ID: <code>{data.id}</code></p>
          </div>
          <Badge v={data.status} />
        </div>

        <h3>Project configuration</h3>
        <div className="card">
          <p><b>Repository:</b> {data.config?.repo_url || 'Uploaded archive'}</p>
          <p><b>Branch:</b> {data.config?.branch || 'Default branch'}</p>
          <p><b>Chaos test:</b> {data.config?.fault_engine} / {data.config?.fault_type}</p>
          <p><b>Fault duration:</b> {data.config?.fault_duration ?? '—'} seconds</p>
        <p><b>Automatic cleanup:</b> {data.config?.auto_cleanup === false ? 'Disabled; containers are intended to remain available' : 'Enabled'}</p>
          <p><b>Created:</b> {data.created_at ? new Date(data.created_at).toLocaleString() : '—'}</p>
        {data.experiment_id && <button className="btn s" type="button" onClick={downloadExperiment} disabled={!experiment.data}>
          {experiment.isLoading ? 'Loading definition…' : 'Download experiment.json'}
        </button>}
        {experiment.isError && <p className="err">Experiment definition could not be loaded: {(experiment.error as Error).message}</p>}
        {data.experiment_run_id && <p><Link to={`/runs/${data.experiment_run_id}`}>Inspect experiment run and recovery scorecard</Link></p>}
        </div>

        {data.status === 'PENDING_APPROVAL' && (
          <button className="btn" onClick={start}>Build Containers &amp; Start</button>
        )}
        {data.services?.some((service: any) => service.status === 'running') && (
          <button className="btn d" style={{ marginLeft: 8 }} onClick={teardown}>Stop All Containers</button>
        )}

        <h3>Pipeline stages</h3>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {data.stages?.map((stage) => <span className="b mu" key={stage.id}>{stage.name}: {stage.status}</span>)}
        </div>

        <h3>Discovered services ({data.services?.length ?? 0})</h3>
        {!data.services?.length ? <p>Services will appear after the source has been inspected.</p> : (
          <div className="tw"><table>
            <thead><tr><th>Service</th><th>Build source</th><th>Port</th><th>Status</th><th>Chaos target</th></tr></thead>
            <tbody>{data.services.map((service) => (
              <tr key={service.name}>
                <td>{service.name}</td>
                <td>{service.dockerfile || service.image || 'Dockerfile'}</td>
                <td>{service.port || '—'}</td>
                <td><Badge v={service.status || 'PENDING'} /></td>
                <td>{service.primary ? 'Yes' : 'No'}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}

        {data.verdict && <div className="card">
          <h3>Resilience verdict: {data.verdict.passed ? 'Passed' : 'Failed'}</h3>
          <p>{data.verdict.summary}</p>
        </div>}

        <h3>Build and test logs</h3>
        <div className="log" style={{ maxHeight: 420, overflowY: 'auto' }}>
          {data.logs?.length ? data.logs.map((entry, index) => (
            <div key={`${entry.timestamp}-${index}`}>
              <span style={{ color: 'var(--mu)' }}>[{entry.timestamp?.slice(11, 19)}] </span>
              <b>[{entry.level}]</b> {entry.message}
            </div>
          )) : <p>No pipeline logs yet.</p>}
        </div>
        {data.error && <p className="err">{data.error}</p>}
      </div>
    </div>
  );
}
