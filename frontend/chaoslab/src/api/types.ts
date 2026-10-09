export type ExecutionStatus = 'VALIDATING' | 'REJECTED' | 'QUEUED' | 'RUNNING_BASELINE' | 'INJECTING' | 'OBSERVING' | 'ABORTING' | 'CLEANING_UP' | 'VERIFYING_RECOVERY' | 'SUCCEEDED' | 'FAILED' | 'ABORTED' | 'CLEANUP_FAILED';
export type Outcome = 'PENDING' | 'PASSED' | 'FAILED_HYPOTHESIS' | 'INCONCLUSIVE' | 'DRY_RUN' | 'ERROR';
export type CleanupStatus = 'NOT_STARTED' | 'NOT_REQUIRED' | 'SUCCEEDED' | 'FAILED';
export type EngineName = 'chaos-toolkit' | 'pumba' | 'toxiproxy' | 'chaos-mesh';

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Engine { name: EngineName; configured: boolean; reachable: boolean; version?: string; capabilities: string[]; last_checked_at?: string }
export interface Environment { id: string; name: string; runtime: string; healthy: boolean; engines: EngineName[]; targets: string[]; active_runs: number }

export interface Metadata { name: string; version: string; description: string; owner?: string; tags: string[] }
export interface Target { environment: string; service: string }
export interface FaultDef { engine: string; type: string; parameters: Record<string, any>; durationSeconds: number }
export interface Hypothesis { maxErrorRate: number; maxP95LatencyMs?: number; requireRecovery: boolean }
export interface Safety { environmentAllowlist: string[]; maxDurationSeconds: number; abortOnErrorRate?: number }
export interface Cleanup { removeFaults: boolean; verifyRecovery: boolean }
export interface Limits { runTimeoutSeconds: number }
export interface Spec { target: Target; fault: FaultDef; hypothesis: Hypothesis; safety: Safety; cleanup: Cleanup; probes: any[]; limits: Limits }
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

export interface RunEvent { id: number; event_type: string; message: string; details: any; created_at: string }
export interface ProbeResult { id: number; probe_name: string; phase: string; measurement: number | null; tolerance: number | null; status: string; detail: any; created_at: string; evidence_ref: string | null }
export interface Fault { id: string; run_id: string; engine: string; native_id: string | null; target_id: string; fault_type: string; parameters: any; state: string; created_at: string; expires_at: string; last_cleanup_attempt: string | null; cleanup_error: string | null; requires_attention: boolean }
