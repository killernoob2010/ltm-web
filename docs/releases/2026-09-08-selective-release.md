# 2026-09-08 Selective production release

Authorization: user explicitly approved implementation, production deployment and subsequent verification in this task. No second permission required for this frozen scope.

Scope: REL-1 spot/trade ledger; REL-2 effective positions/trades/live FIFO valuation; REL-3 existing visualization (main already equals pre-V2 backend); REL-4 unchanged collectors -> authenticated server replication with durable progress and deduplication; REL-5 verified missing settlement baseline and initial business-source data. Exclude weekly reports, V2, all new financing/lifecycle, research/backtest and closing agents. Do not alter local collector, pairing or real trading terminal.

Assessment D3/T3/R3/C1: bounded existing business modules and their data integration; no new independent system or cross-module business workflow. Whole-module regression for released modules; source-to-follower integration for replication. Single primary agent.

Implementation: main-based isolated worktree; selectively import approved modules and shared adapters; test atomic cursor/retry, schema/environment/account guards, source conflicts, baseline and duplicate replay. Protect endpoints and tables. Candidate real browser acceptance before main push. Existing staging features must be restored after candidate acceptance and source replication integrated without deleting other developers' work.

Acceptance: unchanged 0.3.2 source devices continue heartbeats/ingest; follower imports source observations with verified counts/keys and retries idempotently; official settlement baseline checked; actual ledger/positions/trades/chart pages work; excluded modules remain main baseline. No claim of complete WH6 position-file ingestion (currently zero snapshots).

Rollback: main 2e9f3b5; staging 1379f03. Back up exact affected tables/schema before DB mutation; verify backup and restore in isolation. New additive tables can remain on code rollback; no whole database overwrite. Disable replication before data rollback. Record actual deployed commits and verification after deployment.

Current stage: development. Next action: implement and test authenticated replication in isolated candidate, then staging acceptance.

Local evidence: Python 738 passed; two financing tests fail identically on untouched origin/main (dated business-status fixture and outdated Excel sheet fixture), excluded from this release regression. Node 165 passed before final pre-V2 DV CSS selection; DV-specific regression 30 passed. PostgreSQL production full dump 26,804,223 bytes, readable catalog 1,264 entries; public schema restored into isolated local UTF8 database. Candidate schema init verified after PostgreSQL-specific compatibility fixes. Real source replay: 829 observations -> 649 fills / 7 existing conflicts; second replay imports 0. Conflicts concern missing zero fees/close profit versus richer first observations, with same core trade quantities/prices. Original August statement hash matches Staging, continuity passed with zero lot difference; five September daily originals also hash-match and pass continuity. Restored candidate positions: 3,758 lots / 16 groups. No production mutations yet.

Staging candidate 4f5f5a0 dispatched through Render manual specific-commit deployment. This automatically disables Staging auto-deploy; restore On Commit after candidate acceptance and restoration of full Staging plus replication. Production deployment remains pending; current user authorization is valid. Pending business decision: spot ledger production source credentials absent, source-field replication versus direct source configuration asked asynchronously.
