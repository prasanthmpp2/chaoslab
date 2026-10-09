from prometheus_client import Counter, Gauge, Histogram

RUNS_SUBMITTED = Counter("chaos_runs_submitted_total", "Runs submitted")
RUNS_COMPLETED = Counter("chaos_runs_completed_total", "Runs finished", ["status", "outcome"])
RUN_DURATION = Histogram("chaos_run_duration_seconds", "Run duration", buckets=(5, 15, 30, 60, 120, 300, 600, 1800))
INJECTION_FAILURES = Counter("chaos_fault_injection_failures_total", "Injection failures", ["engine"])
CLEANUP_FAILURES = Counter("chaos_cleanup_failures_total", "Cleanup failures", ["engine"])
ACTIVE_FAULTS = Gauge("chaos_active_faults", "Faults currently ACTIVE or PENDING")
FAULTS_NEEDING_ATTENTION = Gauge("chaos_faults_requiring_attention", "Faults needing manual remediation")
QUEUE_DEPTH = Gauge("chaos_queue_depth", "RQ queue depth")
WORKER_LAST_RECONCILE = Gauge("chaos_reconciler_last_run_timestamp", "Unix time of last reconcile pass")
