# External API Failure

## Description

Diagnose upstream/external provider latency and timeout failures.

## Diagnostic Steps

- Inspect deadline exceeded/upstream timeout evidence
- Correlate provider call duration with service degradation
- Inspect recent integration/deployment changes
- Distinguish provider outage from client regression

## Safety Rules

- Do not silently disable critical provider calls
- Failover/config changes require explicit policy approval

## Verification Steps

- Verify upstream latency/error symptoms recover
- Verify dependent service health returns within threshold
