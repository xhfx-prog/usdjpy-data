## Deployment now enabled

The deployed v4.1 Worker source is imported at `cloudflare/worker.js`.
Successful deployment and verification are recorded at
`data/deploy_worker_status.json` (run ID 42034158644).

A push to `main` that changes **`cloudflare/worker.js`** triggers deployment.
A push only changing archived data, analysis, documentation or the workflow
does not. A SHA-pinned request in `control/deploy_worker_request.json` also
triggers deployment; workflow_dispatch requires its manual confirmation value.
The pipeline checks source syntax and tool discovery, deploys with
`--keep-vars` to preserve the existing `TWELVE_API_KEY` Cloudflare secret,
and writes an audit record. Never edit Worker source in Cloudflare's dashboard.

**No automatic rollback is implemented.** On failure, consult the Actions
job and audit file; restore the last known-good GitHub source and redeploy
if necessary. Passing tool discovery does not alone verify market-data
freshness or data quality.

# Cloudflare Worker deployment (not yet activated)

The existing Worker `usdjpy-chart` runs the USDJPY MCP service at
`https://usdjpy-chart.xxhappyfrenzyxx.workers.dev/mcp`.

The `worker.js` source was imported from the previously deployed Worker.

## Separation of concerns

- `.github/workflows/update.yml`: hourly archival data collection and
  on-demand refresh. It does not deploy Workers.
- `.github/workflows/deploy-worker.yml`: **manual-only** deployment of the
  Cloudflare Worker. No push/schedule trigger. It does not fetch market data
  or modify OHLC archives.

## One-time setup

1. Import the **current deployed** `worker.js` into this directory.
   Confirm it is identical to the deployed version before first CI deploy.
2. In GitHub repository Settings > Secrets and variables > Actions, add
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.
   Use a narrowly scoped Cloudflare token permitting **Editor** access to
   existing Worker `usdjpy-chart` only where supported.
   Never commit the token, account secrets, or `TWELVE_API_KEY`.
3. Trigger **Deploy USDJPY MCP Worker (manual, guarded)** with confirmation
   `DEPLOY_USDJPY_CHART`.
4. Verify `tools/list`, 1-minute freshness, six-timeframe bundle,
   GitHub archive reads, and no excess Twelve Data calls.

The existing `TWELVE_API_KEY` remains in Cloudflare Workers Secrets.
The deployment workflow is not armed without the exact source and credentials.

References:
https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/
https://developers.cloudflare.com/workers/authorization/workers/
