# Cloudflare Worker deployment (not yet activated)

The existing Worker `usdjpy-chart` runs the USDJPY MCP service at
`https://usdjpy-chart.xxhappyfrenzyxx.workers.dev/mcp`.

This directory is reserved for the **exact running Worker source**. Do not
deploy a reconstructed or stale copy. The source file `worker.js` is
deliberately NOT included until the live v4.1 implementation is imported and
checked against its `tools/list` response and OHLC behavior.

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
