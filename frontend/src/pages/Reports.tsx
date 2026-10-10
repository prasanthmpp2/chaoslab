import { useEffect, useState } from 'react';
import { Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Link } from 'react-router-dom';
import { useExperiments, useRecoveryScorecardReport, useRuns } from '../api/hooks';
import { Badge, Empty, ErrorState, Loading, RecoveryScorecard, Table } from '../components/ui';
import type { ScorecardReportRun } from '../api/types';

function saveFile(filename: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function Reports() {
  const [experimentId, setExperimentId] = useState('');
  const experiments = useExperiments();
  const runs = useRuns('limit=200');
  const report = useRecoveryScorecardReport(experimentId);
  useEffect(() => {
    if (!experimentId && experiments.data?.length) setExperimentId(experiments.data[0].id);
  }, [experimentId, experiments.data]);

  if (experiments.isLoading || runs.isLoading) return <Loading />;
  if (experiments.isError) return <ErrorState e={experiments.error} />;
  if (runs.isError) return <ErrorState e={runs.error} />;
  const recentRuns = runs.data ?? [];
  const selected = experiments.data?.find((experiment) => experiment.id === experimentId);

  function exportScorecardJson() {
    if (report.data) saveFile(`recovery-scorecard-${experimentId}.json`, JSON.stringify(report.data, null, 2), 'application/json');
  }

  function exportScorecardCsv() {
    if (!report.data) return;
    const quote = (value: unknown) => `"${String(value ?? '').replace(/"/g, '""')}"`;
    const rows = [
      ['run_id', 'created_at', 'experiment_version', 'engine', 'target', 'fault', 'max_error_rate', 'execution_status', 'verdict', 'cleanup_status', 'recovery_duration_seconds', 'score_status', 'score', 'fault_impact_points', 'recovery_points', 'cleanup_points', 'probe_and_event_evidence'],
      ...report.data.runs.map((item) => [
        item.run_id, item.created_at, item.experiment_version, item.engine,
        `${item.target.environment}:${item.target.service}`, item.fault.type,
        item.hypothesis.maxErrorRate, item.execution_status, item.verdict, item.cleanup_status,
        item.recovery_duration_seconds, item.scorecard.status, item.scorecard.score,
        item.scorecard.components.fault_impact?.score, item.scorecard.components.recovery_speed?.score,
        item.scorecard.components.cleanup?.score, JSON.stringify(item.evidence),
      ]),
    ].map((row) => row.map(quote).join(','));
    saveFile(`recovery-scorecard-${experimentId}.csv`, rows.join('\n'), 'text/csv');
  }

  const scorecardTrend = report.data?.trend;
  const scoreSeries = report.data?.trend.series.filter((point) => point.score != null).map((point, index) => ({
    ...point, run: index + 1,
  })) ?? [];

  return <>
    <h2>Reports</h2>
    <p className="sub">Compare resilience scores across repeated runs and download raw evidence.</p>
    <div className="card">
      <label htmlFor="scorecard-experiment">Experiment scorecard trend</label>
      <select id="scorecard-experiment" value={experimentId} onChange={(event) => setExperimentId(event.target.value)}>
        <option value="">Select an experiment</option>
        {experiments.data?.map((experiment) => <option key={experiment.id} value={experiment.id}>{experiment.name}</option>)}
      </select>
      {report.isLoading && experimentId && <p role="status">Loading scorecard trend…</p>}
      {report.isError && <ErrorState e={report.error} />}
      {selected && report.data && <>
        <div className="grid score-stats">
          <div className="card"><span className="mu">Scored runs</span><div className="big">{scorecardTrend?.scored_run_count} / {scorecardTrend?.run_count}</div></div>
          <div className="card"><span className="mu">Average score</span><div className="big">{scorecardTrend?.average_score == null ? '—' : `${scorecardTrend.average_score} / 100`}</div></div>
          <div className="card"><span className="mu">Best score</span><div className="big">{scorecardTrend?.best_score == null ? '—' : `${scorecardTrend.best_score} / 100`}</div></div>
        </div>
        {scoreSeries.length ? <div className="card">
          <h3>Score trend · {selected.name}</h3>
          <div className="trend-chart" role="img" aria-label={`Recovery score trend across ${scoreSeries.length} scored runs`}>
            <ResponsiveContainer><LineChart data={scoreSeries} margin={{ top: 8, right: 16, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--bd)" />
              <XAxis dataKey="run" label={{ value: 'Run order', position: 'insideBottom', offset: -2 }} />
              <YAxis domain={[0, 100]} />
              <Tooltip labelFormatter={(value) => `Run ${value}`} formatter={(value) => [`${value} / 100`, 'Recovery score']} />
              <Line type="monotone" dataKey="score" stroke="var(--ac)" strokeWidth={3} dot={{ r: 4 }} />
            </LineChart></ResponsiveContainer>
          </div>
        </div> : <Empty>No scored runs yet. Completed runs need healthy baseline, during-fault, recovery, and cleanup evidence.</Empty>}
        <div className="section-head">
          <h3>Per-run results and evidence</h3>
          <div><button className="btn s" disabled={!report.data} onClick={exportScorecardJson}>Export evidence (JSON)</button>{' '}
            <button className="btn s" disabled={!report.data} onClick={exportScorecardCsv}>Export evidence (CSV)</button></div>
        </div>
        {report.data.runs.length ? <Table head={['Run', 'Created', 'Target', 'Fault', 'Execution', 'Verdict', 'Cleanup', 'Recovery', 'Score']}
          rows={report.data.runs.map((item: ScorecardReportRun) => [
            <Link to={`/runs/${item.run_id}`}>{item.run_id.slice(0, 8)}</Link>, new Date(item.created_at).toLocaleString(), `${item.target.environment}:${item.target.service}`,
            `${item.engine}/${item.fault.type}`, <Badge v={item.execution_status} />,
            <Badge v={item.verdict} />, <Badge v={item.cleanup_status} cleanup />,
            item.recovery_duration_seconds == null ? '—' : `${item.recovery_duration_seconds.toFixed(1)} s`,
            item.scorecard.status === 'SCORED' ? `${item.scorecard.score} / 100` : <Badge v="NOT_SCORED" />,
          ])} /> : <Empty>No runs exist for this experiment yet.</Empty>}
        {report.data.runs.slice(0, 5).map((item) => <RecoveryScorecard key={item.run_id} card={item.scorecard} />)}
      </>}
      {!experimentId && !experiments.data?.length && <Empty>Create an experiment and complete a run to build a recovery scorecard trend.</Empty>}
    </div>

    <div className="card">
      <h3>Recent run summary</h3>
      <p className="mu">Latest {recentRuns.length} runs · export includes each run score and recovery duration. Detailed probe and event evidence is in the per-experiment JSON/CSV exports above.</p>
      <button className="btn s" onClick={() => {
        const quote = (value: unknown) => `"${String(value ?? '').replace(/"/g, '""')}"`;
        const rows = [
          ['id', 'experiment', 'engine', 'target', 'execution', 'verdict', 'cleanup', 'recovery_seconds', 'score_status', 'score'],
          ...recentRuns.map((item) => [item.id, item.definition_snapshot.metadata.name,
            item.definition_snapshot.spec.fault.engine,
            `${item.definition_snapshot.spec.target.environment}:${item.definition_snapshot.spec.target.service}`,
            item.status, item.outcome, item.cleanup_status, item.recovery_duration_seconds,
            item.scorecard?.status ?? 'NOT_SCORED', item.scorecard?.score]),
        ].map((row) => row.map(quote).join(','));
        saveFile('runs.csv', rows.join('\n'), 'text/csv');
      }}>Export recent runs (CSV)</button>
      <div className="mu" style={{ marginTop: 16 }}>Outcome remains based on each experiment’s configured thresholds; the score is a separate comparison aid.</div>
    </div>
  </>;
}
