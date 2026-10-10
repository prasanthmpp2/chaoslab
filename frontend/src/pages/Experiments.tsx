import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useExperiments, useStartRun, useValidate } from '../api/hooks';
import { Confirm, Empty, ErrorState, Loading, Table } from '../components/ui';

export default function Experiments() {
  const [query, setQuery] = useState('');
  const experiments = useExperiments(query);
  const validate = useValidate();
  const startRun = useStartRun();
  const navigate = useNavigate();

  const visibleExperiments = (experiments.data ?? []).filter((experiment) => {
    const name = experiment.definition.metadata.name.toLowerCase();
    const description = (experiment.definition.metadata.description ?? '').toLowerCase();
    const search = query.toLowerCase();
    return !search || name.includes(search) || description.includes(search);
  });

  async function runExperiment(experimentId: string) {
    const validation = await validate.mutateAsync(experimentId);
    if (!validation.valid) {
      window.alert('Experiment validation failed. Review the definition and safety settings.');
      return;
    }

    const run = await startRun.mutateAsync(experimentId);
    navigate(`/runs/${run.run_id}`);
  }

  if (experiments.isLoading) return <Loading />;
  if (experiments.isError) return <ErrorState e={experiments.error} />;

  return (
    <>
      <h2>Experiments</h2>
      <p className="sub">Saved experiment definitions.</p>
      <p><Link className="btn" to="/experiments/new">Create Experiment</Link></p>

      <label htmlFor="experiment-search">Search by name or description</label>
      <input
        id="experiment-search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <br /><br />

      {visibleExperiments.length === 0 ? (
        <Empty>No experiments found. Create your first experiment to start testing recovery.</Empty>
      ) : (
        <div className="card">
          <Table
            head={['Name', 'Engine', 'Target', 'Fault', 'Duration', 'Version', '']}
            rows={visibleExperiments.map((experiment) => [
              experiment.definition.metadata.name,
              experiment.definition.spec.fault.engine,
              `${experiment.definition.spec.target.environment}:${experiment.definition.spec.target.service}`,
              experiment.definition.spec.fault.type,
              `${experiment.definition.spec.fault.durationSeconds} s`,
              `v${experiment.current_version}`,
              <Confirm
                key={experiment.id}
                title={`Run ${experiment.definition.metadata.name}?`}
                text={`This requests ${experiment.definition.spec.fault.type} on ${experiment.definition.spec.target.environment}:${experiment.definition.spec.target.service} for ${experiment.definition.spec.fault.durationSeconds} seconds. The backend rechecks safety policies before starting.`}
                action="Run experiment"
                onYes={() => runExperiment(experiment.id).catch((error: Error) => window.alert(error.message))}
              >
                Run
              </Confirm>,
            ])}
          />
        </div>
      )}
    </>
  );
}
