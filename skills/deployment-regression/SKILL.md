# Deployment Regression

## Description

Diagnose incidents that begin shortly after a deployment.

## Diagnostic Steps

- Compare incident symptoms with recent deployment timing
- Inspect logs for errors introduced after deployment
- Compare service health before/after change where evidence exists
- Prefer reversible remediation when deployment correlation is strong

## Safety Rules

- Never rollback automatically
- High-risk production changes require human approval
- Do not infer causality from timing alone

## Verification Steps

- Verify service health after remediation
- Confirm latency/error rates return within thresholds
