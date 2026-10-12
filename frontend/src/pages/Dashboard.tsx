import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useActiveFaults, useEngines, useExperiments, useRunComparison, useRuns } from '../api/hooks';
import { Badge, Empty, ErrorState, Loading, Table } from '../components/ui';

const ACTIVE = ['QUEUED', 'VALIDATING', 'RUNNING_BASELINE', 'INJECTING', 'OBSERVING', 'ABORTING', 'CLEANING_UP', 'VERIFYING_RECOVERY'];

export default function Dashboard() {
  const ex = useExperiments();
  const runs = useRuns('limit=100');
  const eng = useEngines();
  const faults = useActiveFaults();
  const [baselineId, setBaselineId] = useState('');
  const [candidateId, setCandidateId] = useState('');
  const comparison = useRunComparison(baselineId, candidateId);

  useEffect(() => {
    const all = runs.data ?? [];
    if (!all.length) return;
    if (!candidateId || !all.some((r) => r.id === candidateId)) setCandidateId(all[0].id);
    const candidate = all.find((r) => r.id === candidateId) ?? all[0];
    const previous = all.find((r) => r.id !== candidate.id && r.experiment_id === candidate.experiment_id);
    if ((!baselineId || !all.some((r) => r.id === baselineId)) && previous) setBaselineId(previous.id);
  }, [runs.data, baselineId, candidateId]);

  if (runs.isLoading) return <Loading />;
  if (runs.isError) return <ErrorState e={runs.error} />;
  const rows = runs.data ?? [];
  const count = (predicate: (run: typeof rows[number]) => boolean) => rows.filter(predicate).length;
  const cards: [string, number | string, string][] = [
    ['Total Experiments', ex.data?.length ?? '—', '/experiments'],
    ['Active Runs', count((r) => ACTIVE.includes(r.status)), '/runs/active'],
    ['Successful Runs', count((r) => r.outcome === 'PASSED'), '/runs'],
    ['Failed Resilience Checks', count((r) => r.outcome === 'FAILED_HYPOTHESIS'), '/runs'],
    ['Active Faults', faults.data?.length ?? '—', '/faults'],
    ['Cleanup Issues', count((r) => r.cleanup_status === 'FAILED'), '/faults'],
  ];
  const chartEntries: [string, number][] = [
    ['Passed', count((r) => r.outcome === 'PASSED')],
    ['Hypothesis failed', count((r) => r.outcome === 'FAILED_HYPOTHESIS')],
    ['Aborted', count((r) => r.status === 'ABORTED')],
    ['Execution failed', count((r) => r.status === 'FAILED')],
    ['Cleanup failed', count((r) => r.cleanup_status === 'FAILED')],
  ];
  const chart = chartEntries.map(([name, value]) => ({ name, value }));

  return <>
    <h2>Chaos Engineering Overview</h2>
    <p className="sub">Monitor experiment outcomes, inspect captured probe evidence, and compare repeated executions.</p>
    <p><Link className="btn" to="/experiments/new">Create Experiment</Link>{' '}
      <Link className="btn s" to="/runs/active">View Active Runs</Link>{' '}
      <Link className="btn" to="/pipeline" style={{ background: '#4f46e5', color: '#fff' }}>Upload Project (CI/CD Pipeline)</Link>
    </p>
    <div className="grid">{cards.map(([label, value, path]) => <Link key={label} to={path} className="card" style={{ color: 'inherit', textDecoration: 'none' }}>
      <div className="mu">{label}</div><div className="big">{value}</div>
    </Link>)}</div>
    {!!faults.data?.length && <div className="card warn"><b>{faults.data.length} active fault(s)</b> <Link to="/faults">Review</Link>.</div>}

    <div className="g2">
      <div className="card"><h3>Outcomes (last {rows.length} runs)</h3>
        <div style={{ height: 220 }} role="img" aria-label={chart.map((c) => `${c.name} ${c.value}`).join(', ')}>
          <ResponsiveContainer><BarChart data={chart}><XAxis dataKey="name" fontSize={11} /><YAxis allowDecimals={false} /><Tooltip /><Bar dataKey="value" fill="var(--ac)" /></BarChart></ResponsiveContainer>
        </div>
      </div>
      <div className="card"><h3>Engines</h3>{eng.isError ? <ErrorState e={eng.error} /> : eng.data?.map((engine) => {
        const configured = engine.configured ?? engine.enabled ?? false;
        const reachable = engine.reachable ?? engine.available ?? false;
        return <p key={engine.name}><Link to={`/engines/${engine.name}`}>{engine.name}</Link>{' '}
          <span className={`b ${!configured ? 'mu' : reachable ? 'ok' : 'er'}`}>{!configured ? 'Not configured' : reachable ? 'Reachable' : 'Unreachable'}</span>
        </p>;
      })}</div>
    </div>

    <section className="card">
      <h3>Compare executions</h3>
      <p className="mu">Compare configured synthetic HTTP probe requests and responses across two runs. This does not capture arbitrary application traffic.</p>
      {rows.length < 2 ? <Empty>Complete at least two runs to compare measured probe behavior.</Empty> : <>
        <div className="g2">
          <label>Past execution (baseline)<select value={baselineId} onChange={(event) => setBaselineId(event.target.value)}>
            <option value="">Select baseline</option>{rows.filter((r) => r.id !== candidateId).map((r) => <option key={r.id} value={r.id}>{r.id.slice(0, 8)} · {r.definition_snapshot.metadata.name} · {new Date(r.created_at).toLocaleString()}</option>)}
          </select></label>
          <label>New execution (candidate)<select value={candidateId} onChange={(event) => setCandidateId(event.target.value)}>
            <option value="">Select candidate</option>{rows.filter((r) => r.id !== baselineId).map((r) => <option key={r.id} value={r.id}>{r.id.slice(0, 8)} · {r.definition_snapshot.metadata.name} · {new Date(r.created_at).toLocaleString()}</option>)}
          </select></label>
        </div>
        {comparison.isLoading && <p role="status">Loading comparison…</p>}
        {comparison.isError && <ErrorState e={comparison.error} />}
        {comparison.data && <>
          <div className={`card ${comparison.data.compatible ? '' : 'warn'}`}>
            <b>{comparison.data.compatibility_note}</b>
            {comparison.data.runs.map((run, index) => <span key={run.id}> · {index ? 'Candidate' : 'Baseline'}: <Link to={`/runs/${run.id}`}>{run.id.slice(0, 8)}</Link> · {run.target.environment}:{run.target.service} · {run.fault.engine}/{run.fault.type} · <Badge v={run.status} /> <Badge v={run.outcome} /> · cleanup <Badge v={run.cleanup_status} cleanup /> · recovery {run.recovery_duration_seconds == null ? '—' : `${run.recovery_duration_seconds.toFixed(1)} s`} · score {run.scorecard?.score == null ? '—' : `${run.scorecard.score}/100`}</span>)}
          </div>
          {comparison.data.probe_metrics.length ? <Table head={['Probe', 'Phase', 'Requests', 'Errors', 'HTTP statuses', 'Error rate', 'p95 latency', 'Δ error rate', 'Δ p95']}
            rows={comparison.data.probe_metrics.map((metric) => [metric.probe, metric.phase,
              `${metric.baseline.request_count} → ${metric.candidate.request_count}`,
              `${metric.baseline.errors} → ${metric.candidate.errors}`,
              `${JSON.stringify(metric.baseline.status_codes)} → ${JSON.stringify(metric.candidate.status_codes)}`,
              `${formatPercent(metric.baseline.error_rate)} → ${formatPercent(metric.candidate.error_rate)}`,
              `${formatMs(metric.baseline.p95_ms)} → ${formatMs(metric.candidate.p95_ms)}`,
              formatPercent(metric.delta.error_rate), formatMs(metric.delta.p95_ms)])} />
            : <Empty>No HTTP probe calls were recorded for these executions.</Empty>}
        </>}
      </>}
    </section>

    <div className="card"><h3>Recent runs</h3>{rows.length ? <Table head={['Experiment', 'Engine', 'Target', 'Started', 'Execution', 'Outcome', 'Cleanup', '']}
      rows={rows.slice(0, 8).map((run) => [run.definition_snapshot.metadata.name, run.definition_snapshot.spec.fault.engine,
        `${run.definition_snapshot.spec.target.environment}:${run.definition_snapshot.spec.target.service}`,
        run.started_at ? new Date(run.started_at).toLocaleString() : 'Pending', <Badge v={run.status} />,
        <Badge v={run.outcome} />, <Badge v={run.cleanup_status} cleanup />, <Link to={`/runs/${run.id}`}>View details</Link>])} />
      : <p>No runs yet. Create an experiment to start.</p>}</div>
  </>;
}

function formatPercent(value: number | null | undefined) { return value == null ? '—' : `${(value * 100).toFixed(1)}%`; }
function formatMs(value: number | null | undefined) { return value == null ? '—' : `${value.toFixed(1)} ms`; }
