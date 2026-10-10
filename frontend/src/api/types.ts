export type ExecutionStatus = 'VALIDATING' | 'REJECTED' | 'QUEUED' | 'RUNNING_BASELINE' | 'INJECTING' | 'OBSERVING' | 'ABORTING' | 'CLEANING_UP' | 'VERIFYING_RECOVERY' | 'SUCCEEDED' | 'FAILED' | 'ABORTED' | 'CLEANUP_FAILED';
export type Outcome = 'PENDING' | 'PASSED' | 'FAILED_HYPOTHESIS' | 'INCONCLUSIVE' | 'DRY_RUN' | 'ERROR';
export type CleanupStatus = 'NOT_STARTED' | 'NOT_REQUIRED' | 'SUCCEEDED' | 'FAILED';
export type EngineName = 'chaos-toolkit' | 'pumba' | 'toxiproxy' | 'chaos-mesh' | 'chaos_toolkit' | 'chaos_mesh';

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Engine { name: EngineName | string; configured: boolean; reachable: boolean; enabled?: boolean; available?: boolean; version?: string; capabilities: string[]; last_checked_at?: string }
export interface Environment { id: string; name: string; runtime: string; healthy: boolean; engines: (EngineName | string)[]; targets: string[]; active_runs: number }

export interface Metadata { name: string; version: string; description: string; owner?: string; tags: string[] }
export interface Target { environment: string; service: string }
export interface FaultDef { engine: string; type: string; parameters: Record<string, any>; durationSeconds: number }
export interface Hypothesis { maxErrorRate: number; maxP95LatencyMs?: number; requireRecovery: boolean }
export interface Safety { environmentAllowlist: string[]; maxDurationSeconds: number; abortOnErrorRate?: number }
export interface Cleanup { removeFaults: boolean; verifyRecovery: boolean }
export interface Limits { runTimeoutSeconds: number }
export interface ProbeSpec {
    name: string;
    type: 'http' | 'container-state';
    phases: ('baseline' | 'during' | 'recovery')[];
    url?: string;
    expectStatus?: number;
    timeoutSeconds?: number;
    samples?: number;
    intervalSeconds?: number;
    container?: string;
}
export interface Spec { target: Target; fault: FaultDef; hypothesis: Hypothesis; safety: Safety; cleanup: Cleanup; probes: ProbeSpec[]; limits: Limits }
export interface ExperimentDocument { apiVersion: "chaos.example.io/v1"; kind: "Experiment"; metadata: Metadata; spec: Spec }

export interface Experiment {
    id: string;
    name: string;
    description: string;
    current_version: number;
    approved_version: number | null;
    definition: ExperimentDocument;
    owner: string;
    created_at: string;
    updated_at: string;
    archived: boolean;
}

export type ExperimentCreateRequest = ExperimentDocument;

export interface Run {
    id: string;
    experiment_id: string;
    experiment_version: number;
    correlation_id: string;
    status: ExecutionStatus;
    outcome: Outcome;
    cleanup_status: CleanupStatus;
    recovery_duration_seconds: number | null;
    scorecard: RecoveryScorecard | null;
    dry_run: boolean;
    requested_by: string;
    cancel_requested: boolean;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
    worker_id: string | null;
    error_summary: string | null;
    requires_attention: boolean;
    definition_snapshot: ExperimentDocument;
}

export interface ActionResponse { run_id: string; status: string; message: string }
export interface RunSubmission { run_id: string; status: ExecutionStatus; dry_run: boolean }

export interface RunEvent { id: number; event_type: string; message: string; details: any; created_at: string }
export interface ProbeResult { id: number; probe_name: string; phase: string; measurement: number | null; tolerance: number | null; status: string; detail: any; created_at: string; evidence_ref: string | null }
export interface Fault { id: string; run_id: string; engine: string; native_id: string | null; target_id: string; fault_type: string; parameters: any; state: string; created_at: string; expires_at: string; last_cleanup_attempt: string | null; cleanup_error: string | null; requires_attention: boolean }

export interface RecoveryScorecard {
    version: number;
    status: 'SCORED' | 'NOT_SCORED' | string;
    score: number | null;
    weights: Record<string, number>;
    components: Record<string, {
        weight: number;
        score: number | null;
        measured_error_rate?: number | null;
        max_error_rate?: number;
        samples?: number;
        duration_seconds?: number | null;
        deadline_seconds?: number;
        error_rate?: number | null;
        recovered?: boolean | null;
        status?: string;
    }>;
    reasons: string[];
}

export interface ScorecardReportRun {
    run_id: string;
    created_at: string;
    experiment_version: number;
    target: Target;
    engine: string;
    fault: FaultDef;
    hypothesis: Hypothesis;
    execution_status: ExecutionStatus;
    verdict: Outcome;
    cleanup_status: CleanupStatus;
    recovery_duration_seconds: number | null;
    scorecard: RecoveryScorecard;
    evidence: {
        probes: ProbeResult[] | Record<string, unknown>[];
        faults: Record<string, unknown>[];
        events: Record<string, unknown>[];
        artifacts: { id: string; name: string; size_bytes: number; sha256: string }[];
    };
}
export interface ScorecardReport {
    experiment: { id: string; name: string };
    trend: { run_count: number; scored_run_count: number; average_score: number | null; best_score: number | null; lowest_score: number | null; series: { run_id: string; created_at: string; score: number | null; status: string }[] };
    runs: ScorecardReportRun[];
}
export interface RunReport {
    run: {
        id: string; status: ExecutionStatus; outcome: Outcome; cleanup_status: CleanupStatus;
        recovery_duration_seconds: number | null; scorecard: RecoveryScorecard; dry_run: boolean;
        started_at: string | null; finished_at: string | null; error_summary: string | null;
    };
    definition_snapshot: ExperimentDocument;
    baseline: { probe: string; measurement: number | null; tolerance: number | null; status: string; detail: Record<string, unknown>; evidence: string | null }[];
    during_fault: { probe: string; measurement: number | null; tolerance: number | null; status: string; detail: Record<string, unknown>; evidence: string | null }[];
    recovery: { probe: string; measurement: number | null; tolerance: number | null; status: string; detail: Record<string, unknown>; evidence: string | null }[];
    faults: { id: string; engine: string; target_id: string; type: string; parameters: Record<string, unknown>; state: string; cleanup_error: string | null; expires_at: string }[];
    events: { at: string; type: string; message: string; details: Record<string, unknown> }[];
    artifacts: { id: string; name: string; size_bytes: number; sha256: string }[];
    notes: string;
}

export interface PipelineSummary {
    id: string;
    project_name: string;
    status: string;
    created_at: string;
    config?: { auto_cleanup?: boolean; fault_engine?: string; fault_type?: string } & Record<string, any>;
    container?: { name?: string; status?: string };
    experiment_id?: string | null;
    target_service_key?: string;
    services?: PipelineService[];
    verdict?: { passed: boolean; summary: string; baseline_error_rate?: number; during_error_rate?: number; recovery_error_rate?: number };
}
export interface PipelineService {
    name: string;
    dockerfile?: string;
    image?: string;
    build_path?: string;
    port?: number;
    status?: string;
    primary?: boolean;
    container_name?: string;
    is_prebuilt?: boolean;
}
export interface PipelineDetail extends PipelineSummary {
    current_stage?: string;
    config?: Record<string, any>;
    services?: PipelineService[];
    stages?: { id: string; name: string; status: string }[];
    logs?: { timestamp?: string; level?: string; message: string }[];
    verdict?: { passed: boolean; summary: string; run_status?: string; run_outcome?: string; baseline_error_rate?: number; during_error_rate?: number; recovery_error_rate?: number };
    experiment_run_id?: string | null;
    error?: string;
}
export interface RetainedPipelineTarget {
    pipeline_id: string;
    project_name: string;
    experiment_id: string;
    status: string;
    service: string;
    service_name: string;
    container: string;
}
