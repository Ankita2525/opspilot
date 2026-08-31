# Authentication Failure

## Description

Diagnose token/signature/authentication regressions.

## Diagnostic Steps

- Inspect unexpected 401/403 patterns
- Inspect token/signature verification errors
- Correlate with recent auth-service deployments
- Distinguish expired/invalid tokens from deployment regression

## Safety Rules

- Never disable authentication
- Never weaken signature validation
- Production rollback requires approval

## Verification Steps

- Verify authentication error rate returns to healthy threshold
- Verify legitimate requests authenticate successfully
