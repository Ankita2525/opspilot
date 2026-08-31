# PostgreSQL Diagnostics

## Description

Diagnose PostgreSQL/database connection or query failures.

## Diagnostic Steps

- Inspect connection pool timeout/exhaustion evidence
- Inspect database-related errors
- Correlate DB failures with service latency/error spikes
- Inspect recent configuration/deployment changes
- Distinguish pool exhaustion from general database unavailability

## Safety Rules

- Do not change DB config automatically
- Restart/rollback/config changes require policy approval
- Avoid destructive SQL

## Verification Steps

- Verify request latency/error rate recovery
- Verify DB-related timeout evidence stops
