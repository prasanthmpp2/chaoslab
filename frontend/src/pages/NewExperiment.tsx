import { useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useNavigate } from 'react-router-dom';
import type { ExperimentDocument, ProbeSpec } from '../api/types';
import {
  useCreateExperiment, useEngines, useEnvs, useRetainedPipelineTargets, useStartRun, useValidate,
} from '../api/hooks';
import { ErrorState, Loading } from '../components/ui';

const validJson = (text: string, array = false) => {
  try {
    const parsed = JSON.parse(text);
    return !array || Array.isArray(parsed);
  } catch {
    return false;
  }
};

const schema = z.object({
  name: z.string().min(3, 'Enter at least 3 characters'),
  description: z.string().optional(),
  environment_id: z.string().min(1, 'Select an environment'),
  target: z.string().min(1, 'Select a target'),
  engine: z.string().min(1, 'Select an engine'),
  fault_type: z.string().min(1, 'Select a fault'),
  duration_seconds: z.coerce.number().min(1, 'Minimum 1 s').max(3600, 'Maximum 3600 s'),
  max_error_rate: z.coerce.number().min(0, '0–100').max(100, '0–100'),
  parameters_json: z.string().refine((v) => validJson(v), 'Must be valid JSON'),
  probes_json: z.string().refine((v) => validJson(v, true), 'Must be a JSON array'),
  probe_url: z.string().optional(),
});
type FormValues = z.infer<typeof schema>;
type TargetMode = 'retarget' | 'retained';

const STEPS = ['Basics', 'Target', 'Engine', 'Fault', 'Probes & hypothesis', 'Review'];
const FIELDS: (keyof FormValues)[][] = [
  ['name'], ['environment_id', 'target'], ['engine'],
  ['fault_type', 'duration_seconds', 'parameters_json'],
  ['max_error_rate', 'probes_json'], [],
];
const DEFAULT_PARAMS: Record<string, Record<string, unknown>> = {
  'network-latency': { proxy: 'payment-http', latencyMs: 100, jitterMs: 0 },
  'bandwidth-limit': { proxy: 'payment-http', rateKbps: 1024 },
  'connection-timeout': { proxy: 'payment-http', timeoutMs: 5000 },
  'connection-reset': { proxy: 'payment-http', timeoutMs: 0 },
  'data-limit': { proxy: 'payment-http', bytes: 1048576 },
  'container-kill': { signal: 'SIGKILL' },
  'container-stop': {}, 'container-pause': {},
  'network-delay': { timeMs: 100, jitterMs: 10, latencyMs: 100, mode: 'one' },
  'network-loss': { percent: 10 }, 'cpu-stress': { workers: 1 },
  'pod-kill': { mode: 'one' },
  'native-experiment': { native: { title: 'Steady State Test', description: 'Validate resilience', method: [] } },
};

function slug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 63) || 'experiment';
}

function toForm(doc: ExperimentDocument, retargetName = false): FormValues {
  const firstHttp = doc.spec.probes?.find((probe) => probe.type === 'http');
  return {
    name: retargetName ? `${doc.metadata.name}-retargeted` : doc.metadata.name,
    description: doc.metadata.description ?? '',
    environment_id: '',
    target: '',
    engine: doc.spec.fault.engine,
    fault_type: doc.spec.fault.type,
    duration_seconds: doc.spec.fault.durationSeconds,
    max_error_rate: doc.spec.hypothesis.maxErrorRate * 100,
    parameters_json: JSON.stringify(doc.spec.fault.parameters ?? {}, null, 2),
    probes_json: JSON.stringify(doc.spec.probes ?? [], null, 2),
    probe_url: firstHttp?.url ?? '',
  };
}

export default function NewExperiment() {
  const [step, setStep] = useState(0);
  const [message, setMessage] = useState('');
  const [sourceDoc, setSourceDoc] = useState<ExperimentDocument | null>(null);
  const [isPipelineImport, setIsPipelineImport] = useState(false);
  const [targetMode, setTargetMode] = useState<TargetMode>('retarget');
  const [retainedPipelineId, setRetainedPipelineId] = useState('');
  const engines = useEngines();
  const environments = useEnvs();
  const retainedTargets = useRetainedPipelineTargets();
  const create = useCreateExperiment();
  const validate = useValidate();
  const startRun = useStartRun();
  const navigate = useNavigate();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: '', description: '', environment_id: '', target: '', engine: 'pumba',
      fault_type: 'container-pause', duration_seconds: 15, max_error_rate: 5,
      parameters_json: '{}', probes_json: '[]', probe_url: '',
    },
  });
  const values = form.watch();
  const errors = form.formState.errors;
  const environment = environments.data?.find((item) => item.id === values.environment_id);
  const engine = engines.data?.find((item) => item.name === values.engine
    || item.name.replace('_', '-') === values.engine
    || item.name === values.engine.replace('-', '_'));
  const pipelineSuffix = sourceDoc?.metadata.name.match(/^pipe-exp-([a-f0-9]{8})$/)?.[1];
  const matchingRetainedTarget = useMemo(
    () => retainedTargets.data?.find((target) => target.pipeline_id === `pipe-${pipelineSuffix}`),
    [retainedTargets.data, pipelineSuffix],
  );
  const isRetained = isPipelineImport && targetMode === 'retained';

  if (engines.isLoading || environments.isLoading) return <Loading />;
  if (engines.isError || environments.isError) return <ErrorState e={engines.error ?? environments.error} />;

  async function importFile(file?: File) {
    setMessage('');
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as ExperimentDocument;
      if (parsed?.apiVersion !== 'chaos.example.io/v1' || parsed?.kind !== 'Experiment'
        || !parsed?.metadata?.name || !parsed?.spec?.target || !parsed?.spec?.fault) {
        throw new Error('This file does not look like a ChaosLab Experiment JSON definition.');
      }
      const pipelineDoc = parsed.spec.target.environment === 'docker-test'
        && /^pipe-exp-[a-f0-9]{8}$/.test(parsed.metadata.name);
      setSourceDoc(parsed);
      setIsPipelineImport(pipelineDoc);
      setTargetMode('retarget');
      setRetainedPipelineId('');
      form.reset(toForm(parsed, pipelineDoc));
      setStep(0);
      setMessage('Definition loaded. Review the selected target and probe URL before saving or running. The backend validates the full document and safety rules.');
    } catch (error) {
      setMessage(`Could not import JSON: ${(error as Error).message}`);
    }
  }

  function changeTargetMode(mode: TargetMode) {
    setTargetMode(mode);
    if (mode === 'retained' && matchingRetainedTarget) {
      setRetainedPipelineId(matchingRetainedTarget.pipeline_id);
      if (sourceDoc) {
        form.setValue('environment_id', sourceDoc.spec.target.environment);
        form.setValue('target', sourceDoc.spec.target.service);
      }
    } else {
      setRetainedPipelineId('');
      if (isPipelineImport) {
        form.setValue('environment_id', '');
        form.setValue('target', '');
      }
    }
  }

  async function next() {
    if (step === 1 && isRetained) {
      setStep(step + 1);
      return;
    }
    if (await form.trigger(FIELDS[step])) setStep(step + 1);
  }

  function onFaultChange(faultType: string, selectedEngine = values.engine) {
    form.setValue('fault_type', faultType);
    let parameters = { ...(DEFAULT_PARAMS[faultType] ?? {}) };
    if (selectedEngine === 'pumba' && faultType === 'network-delay') parameters = { timeMs: 100, jitterMs: 10 };
    if ((selectedEngine === 'chaos_mesh' || selectedEngine === 'chaos-mesh') && faultType === 'network-delay') {
      parameters = { latencyMs: 100, jitterMs: 10, mode: 'one' };
    }
    form.setValue('parameters_json', JSON.stringify(parameters, null, 2));
  }

  function buildDefinition(data: FormValues): ExperimentDocument {
    if (isRetained) {
      if (!sourceDoc || !matchingRetainedTarget || retainedPipelineId !== matchingRetainedTarget.pipeline_id) {
        throw new Error('Choose the currently retained pipeline target that created this definition.');
      }
      return sourceDoc;
    }

    const probes = JSON.parse(data.probes_json) as ProbeSpec[];
    for (const probe of probes) {
      probe.type ||= 'http';
      probe.phases ||= ['baseline', 'during', 'recovery'];
    }
    const probeUrl = data.probe_url?.trim();
    const firstHttp = probes.find((probe) => probe.type === 'http');
    if (probeUrl && firstHttp) firstHttp.url = probeUrl;
    else if (probeUrl) probes.push({
      name: 'service-health', type: 'http', phases: ['baseline', 'during', 'recovery'],
      url: probeUrl, expectStatus: 200, timeoutSeconds: 5, intervalSeconds: 1, samples: 3,
    });

    const definition = sourceDoc ? structuredClone(sourceDoc) : {
      apiVersion: 'chaos.example.io/v1', kind: 'Experiment',
      metadata: { name: '', version: '1', description: '', tags: [] },
      spec: {
        target: { environment: '', service: '' },
        fault: { engine: '', type: '', parameters: {}, durationSeconds: 15 },
        hypothesis: { maxErrorRate: 0.05, requireRecovery: true },
        safety: { environmentAllowlist: [], maxDurationSeconds: 300 },
        cleanup: { removeFaults: true, verifyRecovery: true }, probes: [],
        limits: { runTimeoutSeconds: 300 },
      },
    } as ExperimentDocument;

    definition.metadata.name = slug(data.name);
    definition.metadata.description = data.description ?? '';
    definition.spec.target = { environment: data.environment_id, service: data.target };
    definition.spec.fault = {
      engine: data.engine,
      type: data.fault_type,
      parameters: JSON.parse(data.parameters_json),
      durationSeconds: data.duration_seconds,
    };
    definition.spec.hypothesis = {
      ...definition.spec.hypothesis,
      maxErrorRate: data.max_error_rate / 100,
      requireRecovery: true,
    };
    definition.spec.safety = {
      ...definition.spec.safety,
      environmentAllowlist: [data.environment_id],
      maxDurationSeconds: Math.max(data.duration_seconds, 1),
    };
    definition.spec.cleanup = { removeFaults: true, verifyRecovery: true };
    definition.spec.probes = probes;
    return definition;
  }

  async function save(data: FormValues, andRun: boolean) {
    setMessage('');
    try {
      const definition = buildDefinition(data);
      const targetLabel = `${definition.spec.target.environment}:${definition.spec.target.service}`;
      if (andRun && !window.confirm(
        `Run ${definition.metadata.name} against ${targetLabel}? This injects ${definition.spec.fault.type} for ${definition.spec.fault.durationSeconds} seconds. Cleanup is configured to remove the fault afterward.`,
      )) return;

      const experiment = await create.mutateAsync(definition);
      const result = await validate.mutateAsync(experiment.id);
      if (!result.valid) {
        setMessage(`Validation failed: ${(result.errors ?? []).join(', ')}`);
        return;
      }
      if (!andRun) {
        navigate('/experiments');
        return;
      }
      const run = await startRun.mutateAsync(experiment.id);
      navigate(`/runs/${run.run_id}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  function fieldError(name: keyof FormValues) {
    return errors[name] ? <div className="err" role="alert">{errors[name]?.message as string}</div> : null;
  }

  const reviewedProbes = (() => {
    try {
      const probeList = JSON.parse(values.probes_json || '[]') as ProbeSpec[];
      const httpProbes = probeList.filter((probe) => probe.type === 'http');
      if (values.probe_url?.trim() && httpProbes.length) httpProbes[0].url = values.probe_url.trim();
      if (values.probe_url?.trim() && !httpProbes.length) probeList.push({
        name: 'service-health', type: 'http', phases: ['baseline', 'during', 'recovery'], url: values.probe_url.trim(),
      });
      return probeList;
    } catch {
      return [];
    }
  })();

  return <>
    <h2>Create Experiment</h2>
    <p className="sub">Define a repeatable fault, target, probes, and recovery expectation.</p>
    <div className="card import-card">
      <div><b>Start from an experiment definition</b><p className="mu">Import an experiment.json file to prefill this wizard.</p></div>
      <label className="btn s file-button" htmlFor="experiment-json">Import JSON</label>
      <input id="experiment-json" type="file" accept="application/json,.json" onChange={(event) => importFile(event.target.files?.[0])} />
    </div>
    {message && <div className={message.startsWith('Could not') || message.startsWith('Validation') ? 'card warn' : 'card'} role="status">{message}</div>}
    {isPipelineImport && <div className="card">
      <b>Pipeline experiment target</b>
      <p className="mu">Choose an allowlisted service for a portable experiment, or reuse the original service only while that pipeline deliberately retains its container.</p>
      <label className="radio-row"><input type="radio" checked={targetMode === 'retarget'} onChange={() => changeTargetMode('retarget')} /> Retarget to an allowlisted environment and service</label>
      <label className="radio-row"><input type="radio" checked={targetMode === 'retained'} disabled={!matchingRetainedTarget} onChange={() => changeTargetMode('retained')} /> Use retained pipeline service
        {matchingRetainedTarget ? ` — ${matchingRetainedTarget.project_name} (${matchingRetainedTarget.service_name})` : ' — original pipeline is not retained or is no longer available'}
      </label>
      {targetMode === 'retained' && retainedTargets.isError && <ErrorState e={retainedTargets.error} />}
    </div>}
    <div className="steps">{STEPS.map((label, index) => <span key={label} className={index === step ? 'on' : ''} aria-current={index === step ? 'step' : undefined}>{label}</span>)}</div>
    <form className="card" onSubmit={form.handleSubmit((data) => save(data, false))}>
      {step === 0 && <>
        <label htmlFor="experiment-name">Experiment name</label><input id="experiment-name" {...form.register('name')} disabled={isRetained} />{fieldError('name')}
        <label htmlFor="experiment-description">Description</label><input id="experiment-description" {...form.register('description')} disabled={isRetained} />
      </>}
      {step === 1 && (isRetained ? <div className="review-box">
        <b>Retained pipeline target</b><p>{matchingRetainedTarget?.project_name} / {matchingRetainedTarget?.service_name}</p>
        <p>Container: <code>{matchingRetainedTarget?.container}</code></p><p>Target reference: <code>{sourceDoc?.spec.target.environment}:{sourceDoc?.spec.target.service}</code></p>
        <p className="mu">The imported definition will be submitted unchanged so the backend can verify its pipeline fingerprint and live container.</p>
      </div> : <>
        <label htmlFor="environment">Environment</label>
        <select id="environment" {...form.register('environment_id')}>
          <option value="">Select an allowlisted environment</option>
          {environments.data!.map((item) => <option key={item.id} value={item.id}>{item.name} ({item.runtime})</option>)}
        </select>{fieldError('environment_id')}
        <label htmlFor="target-service">Service target</label>
        <select id="target-service" {...form.register('target')}>
          <option value="">Select a service</option>{environment?.targets.map((target) => <option key={target} value={target}>{target}</option>)}
        </select>{fieldError('target')}
        {sourceDoc && <div className="review-box"><b>Imported target (reference only)</b><p>{sourceDoc.spec.target.environment}:{sourceDoc.spec.target.service}</p><p>The experiment will use the allowlisted target selected above.</p></div>}
      </>)}
      {step === 2 && <div className="g2">{engines.data!.map((item) => {
        const configured = item.configured ?? item.enabled ?? false;
        const reachable = item.reachable ?? item.available ?? false;
        const allowed = (environment?.engines ?? []).flatMap((value) => [value, value.replace('_', '-'), value.replace('-', '_')]);
        const selectable = isRetained || (configured && reachable && (!environment || allowed.includes(item.name)));
        return <button type="button" key={item.name} disabled={!selectable || isRetained} className={`eng ${values.engine === item.name || values.engine === item.name.replace('_', '-') ? 'sel' : ''}`} onClick={() => {
          form.setValue('engine', item.name);
          if (item.capabilities[0]) onFaultChange(item.capabilities[0], item.name);
        }}><b>{item.name}</b><div className="mu">{selectable ? `Capabilities: ${item.capabilities.join(', ')}` : 'Not configured, reachable, or enabled for this environment.'}</div></button>;
      })}{fieldError('engine')}</div>}
      {step === 3 && <>
        <label htmlFor="fault-type">Fault type</label>
        <select id="fault-type" value={values.fault_type || ''} disabled={isRetained} onChange={(event) => onFaultChange(event.target.value)}>
          <option value="">Select a fault</option>{engine?.capabilities.map((capability) => <option key={capability} value={capability}>{capability}</option>)}
        </select>{fieldError('fault_type')}
        <label htmlFor="fault-duration">Duration (seconds)</label><input id="fault-duration" type="number" {...form.register('duration_seconds')} disabled={isRetained} />{fieldError('duration_seconds')}
        <label htmlFor="fault-parameters">Fault parameters (JSON)</label><textarea id="fault-parameters" rows={5} {...form.register('parameters_json')} disabled={isRetained} />{fieldError('parameters_json')}
      </>}
      {step === 4 && <>
        <label htmlFor="error-tolerance">Maximum error rate during fault (%)</label><input id="error-tolerance" type="number" {...form.register('max_error_rate')} disabled={isRetained} />{fieldError('max_error_rate')}
        <label htmlFor="probe-url">HTTP probe URL</label><input id="probe-url" {...form.register('probe_url')} disabled={isRetained} placeholder="http://service:8000/health" />
        <p className="mu">The probe URL is reviewed here and checked against the backend host allowlist. Retargeted pipeline definitions need a probe URL reachable from the selected environment.</p>
        <label htmlFor="probes-json">Probe definitions (JSON array)</label><textarea id="probes-json" rows={8} {...form.register('probes_json')} disabled={isRetained} />{fieldError('probes_json')}
        <p className="mu">An HTTP URL with no probe definition adds a health probe for baseline, during-fault, and recovery phases. You can edit all probe settings in the JSON array.</p>
      </>}
      {step === 5 && <div className="review-box">
        <h3>Review before saving</h3>
        <dl>
          <dt>Name</dt><dd>{isRetained ? sourceDoc?.metadata.name : slug(values.name)}</dd>
          <dt>Target</dt><dd>{isRetained ? `${sourceDoc?.spec.target.environment}:${sourceDoc?.spec.target.service}` : `${values.environment_id || '—'}:${values.target || '—'}`}</dd>
          <dt>Fault</dt><dd>{values.engine} / {values.fault_type} for {values.duration_seconds} seconds</dd>
          <dt>Threshold</dt><dd>Error rate ≤ {values.max_error_rate}% · recovery required</dd>
          <dt>Probes</dt><dd>{reviewedProbes.length ? <ul>{reviewedProbes.map((probe, index) => <li key={`${probe.name}-${index}`}>
            {probe.name} ({probe.type || 'http'}) — {(probe.type || 'http') === 'http' ? <code>{probe.url || 'URL required'}</code> : `container ${probe.container || 'required'}`} · phases: {probe.phases?.join(', ') || 'baseline, during, recovery'}
          </li>)}</ul> : 'No probes configured; the run will not have a recovery score.'}</dd>
          <dt>Cleanup</dt><dd>Remove the injected fault and verify recovery</dd>
        </dl>
        {isPipelineImport && <p className="mu">Target mode: {isRetained ? 'retained pipeline service' : 'allowlisted retarget'}.</p>}
        <p className="mu">Backend validation remains authoritative for fault parameters, target allowlists, probe URLs, duration limits, and cleanup rules.</p>
      </div>}
      {message && <div className="err" role="alert">{message}</div>}
      <div className="form-actions">
        <button type="button" className="btn s" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>
        {step < STEPS.length - 1 ? <button type="button" className="btn" onClick={next}>Next</button> : <>
          <button type="button" className="btn s" disabled={create.isPending || validate.isPending} onClick={form.handleSubmit((data) => save(data, false))}>Save and validate</button>
          <button type="button" className="btn" disabled={create.isPending || validate.isPending || startRun.isPending} onClick={form.handleSubmit((data) => save(data, true))}>Save and run</button>
        </>}
      </div>
    </form>
  </>;
}
