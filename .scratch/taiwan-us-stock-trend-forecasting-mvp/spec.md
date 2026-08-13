# phase-1-dual-market-engineering-spine

Status: ready-for-agent

Trace IDs: `P1-ENTRY-01`, `P1-TRACE-TW-01`, `P1-TRACE-US-01`, `P1-TRACE-OUTBOX-01`, `P1-TRACE-AUTH-01`, `P1-EXIT-01`, `GATE-POLICY-01`, `GATE-PIT-01`, `GATE-DATA-01`, `GATE-MODEL-01`, `GATE-SEC-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

## Problem Statement

研究團隊尚未擁有一條能在本機完整執行、同時證明台灣與美國市場共用領域語意的工程路徑。若先分別建資料平台、模型、介面或監控，市場差異、時間點證據、來源使用資格、部分失敗與權威帳本之間的問題會延後到整合期才暴露，也會誘使實作者以 ticker、可變 `latest`、跨模組資料表寫入或內部 HTTP 暫時串接。團隊需要先以 fixture 建立一條可重播、可稽核、可從研究介面觀察的雙市場工程脊柱，但不能把 fixture 誤認為正式來源、正式模型或正式預測。

## Solution

交付一個可由單一 Compose 命令啟動、由單一 acceptance runner 驗收的 1＋1 雙市場垂直切片。每個市場以一個內容豐富且決定性的 fixture 掛牌，穿過合法擷取語意、時間點證據、資料集版本、特徵快照、固定用途的 fixture 推論、權威預測帳本、transactional outbox、研究 REST、繁中比較矩陣／標的研究頁，以及健康、事故與安全稽核。所有正式語意從第一階段固定，但輸出清楚標為 `fixture`，不能進入 production 路徑、不能升版，也不能解除任何外部來源依賴。

## User Stories

1. As a platform developer, I want one command to start the complete engineering spine, so that I can reproduce the same system boundary without manually assembling services.
2. As a platform developer, I want the same application modules to run on Windows Docker Desktop and Linux CI, so that local development and automated verification exercise the same behavior.
3. As a data engineer, I want one XTAI fixture and one United States exchange fixture to use the same internal issuer, security, and listing concepts, so that market-specific aliases do not become permanent identities.
4. As a data engineer, I want each fixture listing to include at least 253 unadjusted sessions, so that feature-window and company-action behavior can be exercised realistically.
5. As a data engineer, I want fixture ticker validity periods, so that a ticker change can be tested without changing the listing identity.
6. As a data engineer, I want fixture company actions and versioned adjustment results, so that price comparability is not delegated to a provider's adjusted close.
7. As a data engineer, I want normal, late, duplicate, corrected, missing, and withdrawn fixture versions, so that append-only point-in-time behavior is observable before formal sources are connected.
8. As a data operator, I want a retrieval receipt and coverage report for every fixture collection, so that HTTP-like success cannot be confused with complete data.
9. As a data operator, I want checkpoints to advance only after original evidence is durable, so that retries cannot skip unpersisted content.
10. As a researcher, I want fixture predictions for 1, 5, and 20 trading sessions, so that the research workflow can be evaluated before a trainable model exists.
11. As a researcher, I want named up, flat, and down probabilities plus a separate confidence score and data-support status, so that probability concentration is not confused with data quality.
12. As a researcher, I want unavailable results to omit probabilities and explain the blocking reason, so that missing necessary data is never presented as a neutral forecast.
13. As a researcher, I want a two-row Traditional Chinese comparison matrix, so that the dual-market shape is visible from the first usable increment.
14. As a researcher, I want to open a listing research page from the matrix, so that I can inspect the feature snapshot, model artifact, service assignment, dataset, and original-evidence identifiers behind a result.
15. As a researcher, I want URL reloads to preserve the selected listing, information cutoff, horizon focus, filters, sort order, and detail tab, so that a research view is reproducible.
16. As a researcher, I want fixture results to carry an unmistakable fixture badge, so that I cannot mistake engineering evidence for a formal prediction.
17. As a security administrator, I want a trusted local SecurityContext with narrowly scoped action grants, so that local convenience does not bypass authorization semantics.
18. As a source steward, I want each fixture source to have an explicit source policy version and source entitlement, so that source rights are represented even when the content is synthetic.
19. As a source steward, I want the same operation to be allowed or denied by the intersection of action grants and source entitlements, so that administrative ability never creates source rights.
20. As an auditor, I want authorization decisions, work transitions, published artifacts, and prediction publication to be append-only and traceable, so that the spine can be reviewed after a failure.
21. As an operations engineer, I want stable work outcomes, source-health assessments, incidents, and audit evidence for partial failures, so that a failed market or listing is not hidden by a successful batch status.
22. As an operations engineer, I want an outbox relay that can restart after a crash, so that committed authoritative state eventually reaches projections without duplication or loss.
23. As a model governor, I want FixtureTrendForecaster artifacts to be structurally incapable of production promotion, so that engineering adapters cannot enter the formal model lifecycle.
24. As a delivery owner, I want an immutable P1 acceptance bundle with all trace IDs and digests, so that later phases can depend on a concrete, reproducible result rather than a presentation.

## Implementation Decisions

- Implement only the deep behavior needed in DataSupply, FeatureFactory, ForecastExecution, ModelGovernance, ResearchQuery, and OperationsControl. Do not create empty DocumentIntelligence or ForecastLab modules.
- Use one application image with role-specific commands. Dagster invokes ordinary application workflows; application modules communicate through in-process interfaces, immutable identifiers and artifacts, small outcomes, and transactional outbox events, not internal HTTP.
- Use PostgreSQL as the authoritative store for identities, source policies, work state, dataset manifests, prediction records, research projections, outbox events, incidents, and audit evidence. Use the local filesystem ObjectRepository adapter for immutable fixture objects and large artifacts.
- Represent every target as an issuer, security, and listing with non-reused internal identifiers. External identifiers and ticker aliases are versioned identity assertions, never authoritative keys.
- Preserve RawArtifact, SourceRecordVersion, NormalizedRecordVersion, and RetrievalReceipt as separate append-only evidence. The platform, not an adapter, owns first-observed time, observation ordering, internal version identifiers, checkpoints, and identity resolution.
- Each market fixture includes its own versioned trading calendar, market timezone, session identifiers, at least 253 unadjusted sessions, company actions, adjustment version, source policy, receipts, coverage, and normal plus adversarial point-in-time scenarios.
- FeatureFactory resolves an immutable data selection and produces one immutable feature snapshot. Training-serving mutation and calls to a mutable latest dataset are prohibited.
- FixtureTrendForecaster is a deterministic test adapter behind the stable TrendForecaster prediction contract. Its model artifact can be referenced only by fixture service assignments and can never become a production assignment.
- Prediction execution purpose is explicit and fixed to `fixture`. Fixture, shadow, production, and retrospective replay results remain mutually exclusive in storage, query, routing, and display.
- Publish each prediction record and its core research projection in one PostgreSQL transaction. Evidence enrichment may follow through the outbox, but both core and evidence projection versions remain visible.
- Implement the first research surface as a Traditional Chinese comparison matrix and listing research page. Paths and queries use immutable listing identifiers; ticker is a display alias. All states remain understandable without color.
- REST uses the `/api/v1` contract, named 0–1 probabilities, RFC 3339 UTC instants with market calendar context, snapshot-bound opaque cursors, ETags, and RFC 9457 problem details. A blocked prediction omits probabilities and returns a stable machine-readable reason.
- Local API keys are permitted only on loopback local/development runtime and produce a trusted SecurityContext. AuthorizationPolicy evaluates action grants, source entitlements, purpose, environment, source policy, and data-protection class together and fails closed.
- Work uses `requested → leased → running → succeeded | failed | blocked | cancelled`; retry creates a new attempt. Stable outcomes distinguish invalid, not found, conflict, blocked, policy denied, transient failure, permanent failure, and unavailable.
- The P1 acceptance bundle records trace IDs, source-policy and fixture manifests, deployment and migration digests, contract results, end-to-end identifiers, failure evidence, UI／REST goldens, restart behavior, resource smoke results, and approval. It states that P1 proves only the engineering spine.

## Testing Decisions

- The primary and highest test seam is the deployed Compose acceptance runner: start from a clean environment, execute both market EOD fixture workflows, then verify externally observable REST, Traditional Chinese UI, immutable records, incidents, and audit results. Tests assert behavior, not internal call order or tables.
- Exercise `P1-TRACE-TW-01` and `P1-TRACE-US-01` through the same workflow and query contracts while preserving calendar, timezone, ticker-validity, and company-action differences.
- Exercise `P1-TRACE-OUTBOX-01` by terminating after the authoritative transaction commits but before delivery. Restarting must produce exactly one consumer effect, no lost projection, and the same event identity.
- Exercise `P1-TRACE-AUTH-01` with identical action grants but active versus inactive source entitlement. REST, workflow, projection creation, and audit must agree on the decision and fail closed.
- Verify duplicate collection, late data, correction, withdrawal, missing calendar or company action, necessary versus optional modality absence, checksum failure, stale fencing token, outbox redelivery, fixture-promotion attempt, and one-market failure.
- Run provider contract tests against real PostgreSQL and the filesystem ObjectRepository, including content-addressed duplicate writes, corruption detection, lease and fencing behavior, transaction rollback, and relay crash recovery. Mocks alone do not prove a seam.
- Verify that direct CLI invocation and Dagster orchestration of the same workflow produce the same application outcome and immutable identifiers.
- Verify REST schema, problem details, ETag, URL-stable UI state, keyboard access, text alternatives to color, and the absence of probabilities for unavailable results.
- Verify that no module-to-module HTTP, mutable latest lookup, manual database mutation, or fixture result in a production route is required for the acceptance run.
- Run the complete acceptance runner on Windows Docker Desktop and Linux CI, preserving the resulting content-addressed P1 bundle.

## Out of Scope

- Formal market-data ingestion, seven-year historical qualification, trainable class-prior or logistic models, model promotion, and production prediction records.
- DocumentIntelligence, ForecastLab, news, filings, fundamentals, macro vintages, embeddings, and neural forecasting.
- Formal OIDC, external SecretProvider, SeaweedFS, MLflow, the complete telemetry stack, Kubernetes production topology, HA, and disaster recovery.
- Any claim that fixture evidence satisfies a source contract, unlocks a formal route, or demonstrates predictive value.
- Trading execution, brokerage connectivity, personalized advice, intraday forecasting, public anonymous access, and multi-tenant delivery.

## Further Notes

- `P1-ENTRY-01` is satisfied only when Compose, PostgreSQL, the filesystem ObjectRepository, versioned fixture policies, loopback identity, and the fixed 1＋1 stock pool are available.
- The previously confirmed testing seam is intentionally a single vertical acceptance seam, supplemented by provider contracts at real replaceable boundaries.
- `ready-for-agent` means this phase is specified. It does not grant production status to its data, model, or predictions.
- P2 may begin only from a passing immutable `P1-EXIT-01` bundle.

# phase-2-qualified-price-baseline

Status: ready-for-agent

Trace IDs: `P2-ENTRY-01`, `P2-ENTRY-02`, `P2-TRACE-TW-01`, `P2-TRACE-US-01`, `P2-TRACE-PIT-01`, `P2-TRACE-MODEL-01`, `P2-TRACE-EOD-01`, `P2-EXIT-01`, `GATE-POLICY-01`, `GATE-PIT-01`, `GATE-DATA-01`, `GATE-MODEL-01`, `GATE-SEC-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

## Problem Statement

The engineering spine cannot produce a formal prediction until Taiwan and United States price history, symbol history, company actions, calendars, mature labels, model evidence, and serving assignment are legally qualified and point-in-time correct. A convenient final-value backfill, provider adjusted close, random train/test split, uncalibrated classifier, or mutable model alias would create plausible output without proving what was known at each cutoff. Researchers need the first real 10＋10 daily price baseline, while operators and governors need it to fail closed whenever external rights or evidence are incomplete.

## Solution

Qualify a representative 10＋10 stock pool and two formal market-data paths, build internal adjustment versions and immutable feature snapshots, train both class-prior and regularized multinomial logistic TrendForecaster adapters, and govern the first eligible logistic model through a one-time BootstrapGatePolicy, human approval, five shadow EOD cycles, atomic service assignment, and formal prediction publication. The daily workflow resolves data and assignment once at T+90, publishes complete results or machine-readable unavailability by T+120, and exposes history, lineage, health, and policy state through the same research interfaces established in P1.

## User Stories

1. As a source steward, I want Taiwan and United States market-data contracts represented as versioned source entitlements, so that formal ingestion starts only when retention, modeling, derivation, and display rights are proven.
2. As a source steward, I want an unqualified source to remain disabled or policy blocked, so that engineering progress never substitutes an unlicensed website or test key.
3. As a data engineer, I want a Taiwan current-data adapter and a contract historical adapter to share Collector and Decoder contracts, so that source-specific SDK types do not enter DataSupply.
4. As a data engineer, I want a contracted United States EOD adapter with company actions and symbol history to use the same contracts, so that market differences remain behind real adapters.
5. As a data engineer, I want a 10＋10 representative stock-pool manifest including ordinary shares, share classes or ADRs, ticker changes, half days, suspensions, company actions, and historical delistings, so that qualification is not biased to surviving large issuers.
6. As a data engineer, I want raw unadjusted prices and versioned company actions to produce an internal adjustment version, so that labels and features can be reproduced without provider latest adjusted close.
7. As a data engineer, I want platform-observed and archive-attested history distinguished from published-current-only extracts, so that only adequately attested history enters formal training and backtests.
8. As an auditor, I want a backfill qualification report for every market and listing, so that excluded sessions, revisions, endpoints, identities, policies, and evidence levels are explicit.
9. As a model developer, I want mature 1, 5, and 20-session trend labels generated on realized exchange sessions, so that holidays and temporary closures do not become natural-day mistakes.
10. As a model developer, I want missing exact target endpoints to remain invalid rather than shift forward, so that all listings preserve the same economic horizon.
11. As a model developer, I want the approved volatility-aware label rule and market floors fixed by version, so that test-period class proportions cannot redefine the target.
12. As a model developer, I want quarterly walk-forward folds with seven-year training, one-year validation／calibration, 20-session purge, 20-session embargo, and one-quarter tests, so that model selection cannot see future labels.
13. As a model developer, I want class-prior and regularized multinomial logistic adapters behind TrendForecaster, so that the first formal model is simple, reproducible, and permanently available as a baseline.
14. As a model governor, I want three preregistered seeds, six market-by-horizon calibrators, baselines, cost scenarios, and immutable evaluation reports, so that the first candidate has complete comparison evidence.
15. As a model governor, I want the first logistic candidate to beat class prior by at least one macro-F1 percentage point and pass every absolute gate, so that bootstrap status is not a waiver of quality.
16. As a model governor, I want BootstrapGatePolicy to disable permanently after the first production assignment, so that later candidates cannot avoid incumbent comparison.
17. As a model approver, I want artifact, evaluation, gate policy, expected current assignment, and approval reason bound together, so that approval cannot drift to different evidence.
18. As a model operator, I want five eligible EOD shadow cycles before formal assignment, so that load, schema, latency, prediction invariants, and comparison behavior are proven on the daily path.
19. As a researcher, I want formal 10＋10 search and listing history, so that I can inspect real predictions rather than fixture-only rows.
20. As a researcher, I want every historical formal prediction to retain its original probabilities, support, model artifact, feature snapshot, and service assignment, so that later labels or replays never overwrite what was produced.
21. As a researcher, I want optional fundamentals, macro, and documents to be explicitly unavailable while price support remains eligible, so that missing modalities are not represented as zeros.
22. As an operations engineer, I want T+90 data selection, feature construction, pinned assignment, forecast, validation, and atomic publication to finish by T+120, so that the daily service objective has a concrete workflow.
23. As an operations engineer, I want each listing and horizon to publish a result or a machine-readable unavailable reason, so that partial success is complete and observable.
24. As an operations engineer, I want source health, retries, circuits, quarantine, recovery, deadline incidents, webhook／SMTP delivery, and clock checks on the formal path, so that production behavior is not inferred from fixture success.
25. As a security administrator, I want formal OIDC, tested secret-provider behavior, and the same AuthorizationPolicy on workflow and research queries, so that formal sources cannot be exposed through a weaker path.
26. As a delivery owner, I want a content-addressed P2 bundle containing market qualification, model governance, shadow, rollback, policy, failure, Compose, and Kubernetes-smoke evidence, so that the first production assignment is independently reviewable.

## Implementation Decisions

- P2 formal work is gated by `DEP-MKT-TW-01` and `DEP-MKT-US-01`. Each dependency must prove enough unadjusted EOD history, company actions, and symbol history to build the latest eight quarterly folds with a seven-year training window, and must permit at least seven-year retention plus internal modeling and derivation.
- If either market dependency is not qualified, that formal market path remains `policy_blocked`; P1 fixture paths and adapter contract work may continue, but no substitute website, scraper, manual download, or test credential is allowed.
- The 10＋10 stock-pool manifest is versioned and includes identity and lifecycle edge cases, half days and suspensions, corporate actions, historical delistings, and market-specific session behavior.
- Taiwan combines an allowed TWSE current adapter with a contract historical adapter. The United States uses a contracted EOD adapter. Both implement the same Collector／Decoder, checkpoint, rate, policy, coverage, and reference-graph contracts.
- A HistoricalAvailabilityClaim is created only by the platform qualification workflow. `platform_observed` may support daily production and formal history; `archive_attested` may support only explicit historical reconstruction; `published_current_only` remains isolated research.
- Backfill qualification verifies exact session membership, listing lifecycle, unadjusted prices, company actions, adjustment results, endpoints, corrections, source policy, coverage, and historical evidence. Ineligible samples are excluded with reasons, never imputed into eligibility.
- Trend labels use exact realized sessions and the approved rule `max(market floor, 0.35 × prior-20-session volatility × sqrt(horizon))`, with Taiwan and United States floors of 0.60% and 0.25%. Exact missing target prices are invalid endpoints.
- Quarterly folds use seven years of training, one year split chronologically for model selection and calibration, a fixed 20-session label purge, a fixed 20-session test embargo, and a one-quarter once-only test; the latest eight complete test quarters form governance evidence.
- ForecastLab implements class-prior and regularized multinomial logistic TrendForecaster adapters using the same immutable FeatureBatch, folds, three preregistered seeds, six calibrators, and versioned transaction-cost scenarios.
- Model artifacts are immutable, content addressed, self-contained, offline loadable, and bind feature schema, normalizer, calibrators, data and fold manifests, source policies, runtime, seed, and code provenance.
- BootstrapGatePolicyVersion applies only to the first logistic production candidate. It requires at least a one percentage-point equal-cell macro-F1 improvement over class prior and all absolute calibration, stability, coverage, reproducibility, security, and operational gates. It is permanently disabled after the first production assignment.
- Model governance uses an append-only lifecycle ledger, a human approval with separation of duties and expected-assignment precondition, five EOD shadow cycles, staged cold load, atomic compare-and-swap service assignment, and a verified rollback target. Registry and MLflow remain rebuildable projections.
- Each formal EOD run fixes one market, one information cutoff, one stock-pool version, one data selection, and one service assignment. It resolves at T+90, builds the feature snapshot, predicts all three horizons, validates completeness and probabilities, and transactionally publishes prediction records and the core research projection by T+120.
- Late content never rewrites a completed formal prediction. It may enter the next formal cutoff or an explicitly marked retrospective replay.
- P2 adds formal OIDC, a production-shaped SecretProvider test adapter, SeaweedFS provider verification, OTLP／OpenMetrics telemetry, Prometheus／Alertmanager, sandbox webhook／SMTP delivery, optional MLflow projection, and continuing Helm／Kubernetes smoke tests while Compose remains the primary delivery profile.
- The P2 acceptance bundle binds the P1 bundle, dependency evidence, qualification manifests, backtest folds, model and gate evidence, approvals, shadow runs, assignment／rollback, EOD SLO, REST／UI results, failure recovery, and deployment smoke results.

## Testing Decisions

- The highest seam is the formal EOD workflow observed through immutable prediction records, research REST／UI, source-health and incident state, audit, and the pinned service assignment. Tests avoid asserting model-library internals or workflow call order.
- Run `P2-TRACE-TW-01` and `P2-TRACE-US-01` with real qualified source-adapter contract fixtures, proving checkpoint, rate-limit, policy, coverage, corrections, identity, company-action, and reference-graph behavior.
- Run `P2-TRACE-PIT-01` across platform-observed, archive-attested, published-current-only, late, corrected, and withdrawn versions. Only eligible evidence may enter the appropriate production or historical-reconstruction selection.
- Verify XTAI and United States session mapping, half days, temporary closures, ticker changes, delistings, exact t+h endpoints, corporate-action adjustment, label maturity, and the prohibition on provider adjusted close as truth.
- Rebuild every fold manifest and verify sample membership, labels, purge, embargo, preprocessing fit boundaries, calibrators, cost scenarios, baselines, and metrics without accessing test results during selection.
- Execute the same TrendForecaster contract against class-prior and logistic adapters. Verify request-order and batch-composition invariance, one result or structured reason per listing and horizon, probability sums, confidence semantics, and offline artifact loading.
- Verify BootstrapGatePolicy passes only when logistic exceeds class prior by the required amount and every absolute gate passes; any failure preserves blocked serving. Verify the policy cannot be reused after the first assignment.
- Execute five market-eligible EOD shadow cycles, including staged load, schema compatibility, ten-minute inference boundary, prediction invariants, comparison report, human approval, atomic promotion, and rollback-target verification.
- Inject late data, missing company action, incomplete coverage, schema drift, checksum corruption, identity ambiguity, source-entitlement expiry, calibrator insufficiency, approval conflict, assignment race, relay failure, and rollback conditions. Each produces the stable blocked／failure and incident behavior without rewriting prior results.
- Verify T+90 selection and pinning, T+120 completion, complete result-or-reason coverage, projection consistency, REST history and lineage, stale-state disclosure, notification dead-letter behavior, and clock-skew blocking.
- Run Compose small smoke and Kubernetes provider smoke from a clean environment and preserve a reproducible P2 acceptance bundle.

## Out of Scope

- Documents, filings, fundamentals, macro vintage features, news, text embeddings, market-impact assessments, and multimodal model replacement.
- Neural encoders, gated fusion, Integrated Gradients, HPO, drift-triggered candidates, and the full recurring model-lifecycle schedule.
- A stock pool larger than 10＋10, production-capacity claims, Kubernetes HA, cross-region disaster recovery, and final product go-live.
- Acquiring or signing market-data contracts. Software provides provider contracts and fail-closed states; procurement and legal approval remain external dependencies.
- Trading execution, intraday forecasts, personalized investment advice, and public access.

## Further Notes

- `P2-ENTRY-02` also requires a passing P1 acceptance bundle and the versioned 10＋10 manifest.
- A passing P2 may assign logistic or, if the first logistic cannot pass, remain formally blocked. Class prior is a comparison baseline, not an automatic production fallback.
- `ready-for-agent` means the implementation work is fully specified; `DEP-MKT-TW-01` and `DEP-MKT-US-01` still control whether formal market traces can exit.
- P3 may begin only from a passing immutable `P2-EXIT-01` bundle.

# phase-3-multimodal-research-pilot

Status: ready-for-agent

Trace IDs: `P3-ENTRY-01`, `P3-ENTRY-02`, `P3-TRACE-DOC-TW-01`, `P3-TRACE-DOC-US-01`, `P3-TRACE-MACRO-01`, `P3-TRACE-NEWS-01`, `P3-TRACE-NLP-01`, `P3-TRACE-MODEL-01`, `P3-EXIT-01`, `GATE-POLICY-01`, `GATE-PIT-01`, `GATE-DATA-01`, `GATE-MODEL-01`, `GATE-SEC-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

## Problem Statement

Price-only forecasts do not let researchers inspect how filings, official announcements, fundamentals, macro vintages, and licensed news relate to a prediction. Naively adding text would risk treating URLs as document identity, merging away source rights, leaking later corrections or retrospective annotations into historical folds, mislinking ticker mentions, executing hostile files, or presenting general tone as causal market impact. The product needs a 100＋100 multimodal research pilot whose evidence is bilingual, point-in-time correct, policy-aware, and useful even when news contracts are incomplete.

## Solution

Add the deep DocumentIntelligence implementation and qualified Taiwan／United States document, financial-fact, and macro paths. Preserve immutable document evidence separately from versioned derived intelligence, process untrusted content in a constrained sandbox, require confirmed target links for listing features, and publish versioned annotations, events, embeddings, and market-impact assessments. Build multimodal logistic candidates with ablations against the P2 price baseline, expand the research experience to the full Traditional Chinese MVP, and keep the product explicitly `official-documents-only` until both markets have contract-qualified news sources.

## User Stories

1. As a source steward, I want every document source to declare full-content, summary-only, metadata-link-only, or disabled mode, so that parsers and models cannot expand source rights.
2. As a source steward, I want the pilot to operate in official-documents-only mode when news rights are incomplete, so that useful work can continue without a false news-integration claim.
3. As a data engineer, I want Taiwan MOPS／OGDL announcements, monthly revenue, and financial summaries to form versioned document and FinancialFact datasets, so that official Taiwan evidence is reproducible.
4. As a data engineer, I want SEC 8-K, 6-K, 10-Q, 10-K, and company facts to use the same deep document contract, so that United States filings do not create a separate pipeline vocabulary.
5. As a data engineer, I want Taiwan CBC／DGBAS, United States BLS／BEA, and an approved OECD Economic Outlook dataflow to preserve release and vintage, so that later revisions do not rewrite old feature snapshots.
6. As a source steward, I want FRED series allowlisted individually and unqualified IMF data held, so that public availability is not mistaken for blanket model rights.
7. As a document operator, I want Document, DocumentVersion, Rendition, and Segment identities separated, so that corrections, formats, attachments, and source rights remain traceable.
8. As a document operator, I want hostile or malformed documents parsed without network, secrets, macros, scripts, or unbounded resources, so that one object cannot compromise the formal pipeline.
9. As a document operator, I want extraction failures, low OCR quality, missing required attachments, and archive bombs to abstain or quarantine one object, so that coverage pressure never fabricates evidence.
10. As a researcher, I want official XBRL／iXBRL facts to outrank OCR or language-model guesses, so that financial values retain authoritative context, unit, period, and dimensions.
11. As a researcher, I want Traditional Chinese, English, mixed-language, and official-translation relationships preserved, so that bilingual evidence is not flattened into an untraceable translation.
12. As a researcher, I want exact and near-duplicate relations without deleting source documents, so that repeated reporting does not multiply features or erase publication and entitlement differences.
13. As a researcher, I want target links to carry evidence, role, validity, confidence, and confirmation status, so that ambiguous ticker or name matches do not enter listing features.
14. As a researcher, I want taxonomy, embeddings, event mentions, market events, and market-impact assessments to point back to document segments, so that every derived result can be reviewed.
15. As a researcher, I want general tone separated from 1／5／20-session market-impact probabilities, so that a positive article is not automatically presented as an upward forecast cause.
16. As a researcher, I want the pipeline to abstain on low-confidence classification, linking, or impact, so that precision and policy are not sacrificed for coverage.
17. As a reviewer, I want versioned confirm／reject, merge／split, and event-adjudication decisions with guidelines and reasons, so that human review creates new evidence rather than editing history.
18. As a model developer, I want common FinancialFact and macro-vintage features across markets, so that provider-native fields remain inside decoders.
19. As a model developer, I want multimodal logistic candidates compared with price-only and each single modality, so that new data earns its place through ablation evidence.
20. As a model governor, I want a multimodal candidate to use the same folds, calibration, hard gates, approval, and shadow path as the price baseline, so that a richer feature set does not receive easier governance.
21. As a researcher, I want a 100＋100 matrix with search, filtering, sorting, support states, and all three horizons, so that I can find cross-market disagreement and data gaps.
22. As a researcher, I want the listing research page to show allowed evidence, fundamentals, macro vintages, main positive／negative influences, prediction history, backtests, and lineage, so that the forecast can be investigated in one place.
23. As a researcher, I want unavailable, late, policy-blocked, and valid-empty document states distinguished, so that absence of news is not confused with failed or prohibited collection.
24. As an accessibility user, I want a Traditional Chinese, keyboard-operable, text-complete, narrow-screen-capable experience, so that state is not encoded only by layout or color.
25. As an operations engineer, I want document health, sandbox, quality, review, deletion, projection, and model-ablation evidence in canonical operations state, so that telemetry is not the sole record.
26. As a delivery owner, I want the P3 bundle to preserve product blockers when a news dependency is missing, so that the pilot can pass an official-document scope without claiming the complete product.

## Implementation Decisions

- P3 requires a passing P2 bundle plus qualified Taiwan and United States official documents, fundamentals, and macro sources. News dependencies may remain incomplete only if all research surfaces and the acceptance bundle display `official-documents-only`.
- Implement DocumentIntelligence for the first time as a deep `DocumentPipeline.process` behavior. Document assembly, sandbox parsing, language detection, target linking, clustering, classification, embedding, event extraction, market-impact assessment, and review adapters remain internal.
- Use immutable Document, DocumentVersion, Rendition, and Segment identities. URL and content hash are evidence or addressing mechanisms, not permanent document identity.
- Each document version binds the active source policy and one content mode: full content, summary only, metadata plus link, or disabled. Processing, feature use, display, export, backup, and deletion enforce the same mode.
- Process every untrusted rendition in a no-network, no-secret, read-only, resource-bounded sandbox. Reject macros, scripts, external resources, unsafe deserialization, archive bombs, and unexpected media types; isolate only the affected object.
- Preserve original rendition, conservative StandardText with coordinate mapping, and aggressive matching text separately. XBRL／iXBRL FinancialFact is authoritative for formal structured values; OCR or model extraction never overwrites it.
- Exact duplication shares content-addressed bytes but preserves each source document and receipt. Near-duplicate DocumentCluster is a versioned, rebuildable projection and never replaces source identity.
- Only confirmed target links may enter listing features. Links preserve evidence segment, role, method, model／rule version, confidence, candidate set, validity, and status. Ambiguous identities remain unresolved or quarantined.
- Document annotations use a common immutable envelope with evidence span, full probability or confidence, abstention, processing bundle, training cutoff, computation time, source policy, and prospective versus retrospective mode.
- EventMention remains an extracted claim; MarketEvent is a versioned projection that may retain disputed facts. MarketImpactAssessment is a non-causal association for a confirmed target, segment, and horizon.
- ProcessingBundleVersion fixes parser, normalization, segmentation, language, linking, duplication, taxonomy, classifier, embedding, event, impact, quality, source-priority, runtime, code, and policy versions. Production work never resolves a latest model at run time.
- Historical document reconstruction requires platform-observed or qualified archive-attested evidence plus a reconstruction bundle whose training cutoff, rules, models, review knowledge, and feature-freeze budget do not leak future information.
- Taiwan paths cover MOPS／OGDL material information, monthly revenue, and financial summaries. United States paths cover SEC forms and company facts. Macro paths cover CBC＋DGBAS, BLS＋BEA, and an approved OECD forecast dataflow; FRED is per-series allowlist and IMF stays on legal hold until qualified.
- A news adapter is contract required. It becomes formally active only when retention, NLP／embedding, model, internal display, attribution, and deletion rights are all proven. Missing Taiwan or United States news rights preserve a full-product blocker.
- Expand the stock pool to a versioned 100＋100 manifest stratified by industry, size, liquidity, listing age, share class／ADR, reporting regime, document density, and support status.
- Train multimodal logistic candidates against price-only and each single-modality ablation using the same point-in-time folds, calibrators, cost scenarios, governance ledger, human approval, and five shadow cycles. Failure to improve leaves the P2 baseline assigned.
- Complete the research MVP with comparison matrix, listing page, support details, allowed evidence, named influences, fundamentals, macro vintages, formal history, backtests, lineage, Traditional Chinese accessibility, and policy display.
- Publish a content-addressed P3 bundle containing source and processing policies, corpora, qualification, document datasets, sandbox and quality evidence, review decisions, ablations, shadow runs, SLO, deletion, UI／REST, and dependency blockers.

## Testing Decisions

- The highest seam is a licensed-or-official document entering DataSupply and DocumentIntelligence, contributing to an immutable feature snapshot and shadow forecast, and becoming visible through policy-filtered research REST／UI plus canonical health and audit evidence.
- Run Taiwan and United States official-document traces through the same DocumentPipeline behavior and verify market-specific identifiers, formats, languages, calendars, and evidence without separate public contracts.
- Verify document identity and versioning for initial, corrected, withdrawn, late attachment, alternate rendition, official translation, exact duplicate, near duplicate, and conflicting-source cases.
- Use a real constrained parser sandbox to test malicious PDFs, Office macros, HTML external resources, archive bombs, media-type deception, timeouts, memory／output limits, and parser crashes. One object is quarantined and the remaining batch continues with complete evidence.
- Verify all four content modes across collection, persistence, NLP, features, display, export, backup, and policy deletion. No memory-only, cache, or derived representation may bypass a restriction.
- Test point-in-time reconstruction with platform-observed, archive-attested, late, corrected, retrospective annotation, and post-cutoff processing. Ineligible future knowledge must not enter formal folds.
- Validate the bilingual golden corpus with structured-fact exact match `>=99.5%`, confirmed-link precision `>=99%`, taxonomy macro-F1 `>=0.80` and ECE `<=0.10`, and event macro-F1 `>=0.75`. Wrong target, wrong evidence, or policy bypass is a hard failure.
- Verify StandardText coordinate mapping, XBRL context, OCR abstention, table units and periods, source-specific duplicates, cluster merge／split, and review decisions that create new dataset versions without changing source evidence.
- Verify multimodal logistic, price-only, and every single-modality ablation through the same TrendForecaster contract, fold manifests, calibrators, gates, approval, and five shadow cycles.
- Verify valid empty, uncovered, late, policy blocked, parsing failure, and unavailable modality semantics end to end. Necessary price evidence still controls whether probabilities may exist.
- Verify the 100＋100 research experience, policy-filtered evidence, main influences as non-causal attribution, URL-stable state, keyboard navigation, text status, narrow layout, REST snapshot consistency, T+120 batch SLO, and REST latency SLO.
- Exercise correction, withdrawal, policy deletion, derived-impact deletion, backup restore then re-delete, news-dependency absence, review queue, evidence-projection lag, and source outage without rewriting prior predictions.

## Out of Scope

- A production neural forecaster, gated fusion, HPO, Integrated Gradients, recurring drift-triggered training, and neural promotion.
- A complete-product news claim while either Taiwan or United States news entitlement is missing.
- Social media, forums, arbitrary investor-relations crawling, unlicensed reports, general web scraping, and external AI processing without explicit policy qualification.
- Vector databases, online feature stores, evidence dossiers, PDF／email export, watchlists, collaboration, personalized layouts, and user-edited model inputs.
- The 2,000-listing production capacity, Kubernetes HA, cross-region disaster recovery, and final go-live.

## Further Notes

- P3 may exit in an `official-documents-only` pilot state if news dependencies remain unqualified, but the bundle must retain the full-product blocker and cannot satisfy P5 entry.
- A multimodal candidate does not have to replace the price baseline. Passing governance with the incumbent retained is an acceptable phase result when the candidate fails improvement gates.
- Testing intentionally concentrates on the DocumentPipeline-to-research vertical seam; parser, linker, and model internals are covered only through their observable deep-module outcomes and golden evidence.
- P4 may begin only from a passing immutable `P3-EXIT-01` bundle.

# phase-4-governed-neural-forecaster

Status: ready-for-agent

Trace IDs: `P4-ENTRY-01`, `P4-TRACE-NEURAL-01`, `P4-TRACE-MARKETS-01`, `P4-TRACE-TEXT-01`, `P4-TRACE-REPRO-01`, `P4-TRACE-HPO-01`, `P4-TRACE-ATTR-01`, `P4-TRACE-DRIFT-01`, `P4-EXIT-01`, `GATE-POLICY-01`, `GATE-PIT-01`, `GATE-DATA-01`, `GATE-MODEL-01`, `GATE-SEC-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

## Problem Statement

The multimodal pilot proves data and research value but does not prove that a neural model can improve on the simpler baseline without hiding market-specific failure, calibration defects, unstable seeds, untraceable text use, unsafe artifacts, excessive compute, or governance shortcuts. Training completion alone must not change formal service. Researchers and operators need a shared Taiwan／United States neural candidate that is built incrementally, reproducible without network access, explainable back to evidence, evaluated on fixed folds and gates, and safe to reject while leaving the logistic baseline in service.

## Solution

Implement NeuralTrendForecaster behind the existing deep TrendForecaster seam, first matching the price-only contract and then adding fundamentals, macro, frozen text representations, and quality-aware gated fusion in controlled steps. Govern every candidate through immutable TrainingIntent, three seeds, limited HPO, eight-quarter walk-forward evidence, six calibrators, baselines and ablations, deterministic attribution, offline reproduction, hard-gate vetoes, human approval, five shadow cycles, atomic promotion, and verified rollback. Add drift assessment and recurring training triggers without allowing the model to update or promote itself.

## User Stories

1. As a model developer, I want a neural price-only implementation to pass the same TrendForecaster contract as logistic, so that neural complexity begins from a known external behavior.
2. As a model developer, I want to add fundamentals, macro, document representation, and fusion one step at a time, so that each change has isolated ablation evidence.
3. As a model developer, I want one shared Taiwan／United States model with market normalization, small adapters, and market-by-horizon calibrators, so that samples are shared without hiding market differences.
4. As a model developer, I want price history to remain the residual anchor for every horizon, so that optional modalities add information without replacing the necessary baseline path.
5. As a model developer, I want optional modality masks, age, coverage, quality, policy, and staleness carried explicitly, so that missing information never looks like a neutral value.
6. As a model developer, I want frozen, licensed, versioned multilingual segment embeddings from DocumentIntelligence, so that TrendForecaster never reads raw text or fine-tunes a large encoder.
7. As a model developer, I want model capacity bounded by 15 million trainable parameters and approved HPO ranges, so that the candidate stays within MVP compute limits.
8. As a model developer, I want six market-by-horizon loss cells weighted equally, so that one large market or short horizon cannot dominate training.
9. As a model developer, I want three preregistered seeds and a predetermined primary seed, so that the best random seed cannot be selected after seeing results.
10. As a model developer, I want HPO to use only validation data and at most 30 trials, so that test quarters remain untouched and resource use remains bounded.
11. As a model developer, I want selected HPO configuration retrained from scratch under a new TrainingIntent, so that a trial checkpoint can never become a formal artifact.
12. As a model governor, I want every ModelArtifact to be immutable, self-contained, signed, policy-qualified, and offline loadable, so that service does not depend on a registry alias or network lookup.
13. As a model governor, I want every candidate compared with class prior, logistic, neural price-only, and per-modality ablations on identical folds, so that additional parameters do not imply automatic preference.
14. As a model governor, I want eligibility, reproducibility, statistical, calibration, economic, stability, coverage, operational, security, and material-improvement gates to be vetoes, so that a strong average cannot offset a critical failure.
15. As a model governor, I want Taiwan and United States plus all three horizons assessed as six equal cells, so that the shared architecture cannot mask one market's deterioration.
16. As a model approver, I want human approval separated from training and bound to exact artifact and gate evidence, so that automated completion cannot alter service.
17. As a model operator, I want five shadow EOD runs with the candidate before assignment, so that schema, load, latency, prediction invariants, and incumbent differences are observed on the formal path.
18. As an operations engineer, I want an atomic production assignment and verified rollback target, so that a serving failure has one authoritative recovery decision.
19. As an operations engineer, I want serving failures to trigger only approved automatic rollback conditions, so that noisy quality metrics cannot cause uncontrolled model switching.
20. As an operations engineer, I want feature, prediction, support, and mature-label drift evaluated on fixed windows, so that a candidate can be proposed from stable evidence.
21. As a model operator, I want drift to create a `drift_early` TrainingIntent only after two qualifying weekly windows, so that drift does not promote, roll back, or retrain the model in place.
22. As a source steward, I want policy withdrawal to quarantine affected artifacts and select only an eligible approved target, so that an unauthorized model cannot continue serving.
23. As a researcher, I want five positive and five negative main influences per horizon mapped to features or document segments, so that predictions can be reviewed without causal claims.
24. As a researcher, I want gate reliance shown separately from attribution and confidence, so that internal modality use is not presented as a reason or accuracy.
25. As an auditor, I want a no-network ReproductionRun to rebuild sample membership, labels, normalizer, primary-seed artifact, and evaluation, so that the manifest is more than descriptive metadata.
26. As an auditor, I want promotion, rollback, replay, stale status, drift, and policy events to remain append-only, so that later operations never rewrite historical predictions.
27. As a delivery owner, I want P4 to pass even when neural does not replace logistic, so that governance capability is evaluated independently from an unproven model improvement.

## Implementation Decisions

- P4 starts only from a passing P3 bundle, fixed 100＋100 formal feature schemas and processing bundles, an approved baseline, a qualified reproduction runtime, and complete production staging.
- Keep one deep TrendForecaster interface for train and predict. TCN, MLP, attention pooling, fusion gates, heads, calibration framework, and checkpoint format remain private implementation details.
- Build NeuralTrendForecaster in the sequence price only → fundamentals → macro → documents → quality-aware gated fusion. Each step must pass contract parity and an ablation before the next is introduced.
- The shared model uses market-specific robust normalization, a small market adapter／embedding, horizon-specific quality-aware fusion, independent horizon heads, and six market-by-horizon calibrators. A price representation is the residual anchor.
- Price uses a causal masked TCN with the approved 253-session receptive field and 128-dimensional common representation. Fundamentals and macro use compact MLP representations; documents use masked attention over at most 64 deterministically selected, authorized segment representations.
- Only eligible price history is necessary. At least 240 valid sessions is full support, 60–239 is degraded with masks／left padding, and fewer than 60 or a missing anchor price is unavailable. Optional modalities carry availability, age, coverage, quality, policy, and staleness separately.
- Large multilingual text encoders are frozen, licensed, versioned, and executed by DocumentIntelligence. TrendForecaster receives embeddings and evidence references, never raw text, remote code, or an opportunity to fine-tune the encoder.
- Training uses equal weighting across six market-by-horizon cells, bounded class weights, no cross-time oversampling, three preregistered seeds, approved optimizer／early-stopping rules, and a total of at most 15 million trainable parameters.
- HPO searches only the approved model-capacity and optimization ranges, uses the model-selection portion of validation data, runs at most 30 early-stoppable trials, and never sees test quarters. The selected configuration creates a separate TrainingIntent and trains three seeds from scratch.
- Every formal TrainingIntent fixes data, feature, label, fold, stock pool, calendar, adjustment, processing, source policy, cost, model, HPO, seed, code, dependency, image, hardware, and precision manifests before execution.
- ModelArtifact uses data-only safe formats such as safetensors／ONNX, JSON, and Parquet statistics. Pickle, joblib, arbitrary callable checkpoints, remote code, download hooks, and unsigned artifacts are prohibited.
- Each candidate receives the latest eight complete quarterly tests, class-prior and logistic baselines, neural price-only and per-modality ablations, six calibrators, three-seed stability, economic cost scenarios, support slices, and the immutable GatePolicyVersion thresholds already approved.
- ReproductionRun executes in an isolated no-network approved runtime and rebuilds manifests, samples, labels, normalizer, primary-seed artifact, and evaluation. CPU probability tolerance is `<=1e-6`; an approved mixed-precision GPU path uses `<=1e-4`.
- Main influences use Integrated Gradients grouped by named feature, modality, and time bucket, with text mapped to document segments. Masked modalities contribute zero; completeness relative error median is `<=5%` and p95 `<=10%`. Gate reliance is separate metadata.
- Inference plus attribution for the approved stock pool remains within ten CPU minutes. A request produces one result or stable unavailable reason per listing and horizon; ordering and batch composition do not change results.
- ModelGovernance keeps the canonical append-only ledger and content-addressed artifacts authoritative. MLflow, registry, and serving cache are rebuildable projections. Promotion stages an artifact, cold-loads it, validates policy and schema, then atomically compare-and-swaps the assignment and outbox at the next unstarted EOD boundary.
- Human approval is separated from TrainingIntent initiation／execution, binds the exact artifact, evaluation, policy, and expected assignment, and expires after seven days or any material evidence change.
- Promotion requires five formal shadow runs and a currently eligible, cold-load-verified rollback target except for the first deployment. Existing predictions are never recomputed; research replay is explicitly retrospective.
- Automatic rollback is limited to immediately verifiable serving failures already defined by the lifecycle contract. Drift and mature-label degradation create incidents or candidates for human action, not automatic model changes.
- Training triggers share one lifecycle: manual rebuild, monthly full, later weekly incremental after two successful full cycles, later quarterly HPO, and two-window drift early. Schedule collisions coalesce by approved priority; no trigger bypasses gates or approval.
- P4 publishes an immutable bundle with every training intent and attempt, evaluation, gate and approval, reproduction, shadow, assignment／rollback, attribution, stale／drift／policy scenario, source-policy effect, SLO, security, and staging result.

## Testing Decisions

- The highest seam is a formal TrainingIntent progressing through ForecastLab and ModelGovernance into shadow predictions, human approval, atomic assignment, formal EOD observation, research projection, and rollback evidence. Tests assert lifecycle outcomes and user-visible behavior rather than neural layer internals.
- Run the same TrendForecaster contract against logistic, neural price-only, incremental modality candidates, and the full neural implementation. Verify schema, masks, support semantics, probability invariants, order／batch invariance, and offline loading.
- For each build step, run identical folds, seeds, calibrators, baselines, cost scenarios, and ablations. A step cannot be hidden inside the final model without its own parity and quality evidence.
- Verify six-cell equal weighting and report every Taiwan／United States × 1／5／20 result, support slice, seed distribution, and quarter. A strong aggregate cannot suppress a failed cell or degraded-support regression.
- Verify HPO isolation: validation-only trial selection, at most 30 trials, no test access, new TrainingIntent for selected configuration, and rejection of trial checkpoints for approval or assignment.
- Reproduce the primary seed in the approved no-network runtime and verify samples, labels, normalizer, artifact, metrics, checksums, and numerical tolerances. Missing manifests, latest lookups, policy blocks, or unsafe formats veto the candidate.
- Verify Integrated Gradients determinism, masked-modality zero contribution, segment evidence mapping, median and p95 completeness thresholds, non-causal labels, and the ten-minute CPU prediction-plus-attribution boundary.
- Execute all hard-gate categories using the immutable GatePolicyVersion, including baseline improvement, incumbent non-inferiority, calibration, economics, seed stability, slice coverage, operational limits, artifact security, and material improvement.
- Test approval separation, seven-day expiry, evidence invalidation, five shadow cycles, staged cold load, compare-and-swap conflicts, pinned EOD assignment, rollback-target daily verification, and the prohibition on break-glass promotion.
- Inject artifact corruption, schema incompatibility, invalid probabilities, increased unavailability, repeated latency breach, source-policy withdrawal, rollback-target invalidation, registry outage, cache disagreement, and outbox redelivery. Canonical ledger and content-addressed objects must win.
- Test drift windows, insufficient mature labels, source outage versus model drift, gate collapse, stale 45／90／120-day behavior, schedule collision, retry attempts, and drift-early creation without automatic promotion or rollback.
- Verify that historical production predictions remain unchanged after promotion, rollback, new calibration, new processing bundle, or retrospective replay.

## Out of Scope

- Separate full Taiwan and United States models, Transformer replacement of the approved TCN, end-to-end text fine-tuning, seed ensembles, listing embeddings, online learning, automated architecture search, and LLM investment reasons.
- GPU production inference, online feature stores, live single-listing model execution through REST, and model self-updates.
- Expanding formal support beyond the qualified 100＋100 pool; additional listings may be isolated OOD or shadow evidence only.
- Lowering or renegotiating already approved GatePolicy, label, fold, calibration, safety, SLO, or source-policy semantics.
- Final 2,000-listing capacity, Kubernetes HA, cross-region DR, independent penetration testing, and product go-live.

## Further Notes

- Neural completion is mandatory; neural promotion is not. A passing P4 bundle may retain logistic as the current model when neural does not meet every gate.
- Any source-policy change that adds news or other content creates a new policy, processing bundle, feature schema, qualification, TrainingIntent, backtest, ablation, gate, and shadow sequence; it cannot be inserted into the current artifact.
- The testing seam remains one lifecycle-to-serving vertical path, with TrendForecaster as the only model contract seam visible outside ForecastLab.
- P5 may begin only from a passing immutable `P4-EXIT-01` bundle.

# phase-5-production-baseline-deployment

Status: ready-for-agent

Trace IDs: `P5-ENTRY-01`, `P5-ENTRY-02`, `P5-TRACE-CAPACITY-01`, `P5-TRACE-HA-01`, `P5-TRACE-SEC-01`, `P5-TRACE-DR-01`, `P5-TRACE-CUTOVER-01`, `P5-EXIT-01`, `GATE-POLICY-01`, `GATE-PIT-01`, `GATE-DATA-01`, `GATE-MODEL-01`, `GATE-SEC-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

## Problem Statement

A 100＋100 production-staging result does not prove that the research decision support system can safely serve the intended Taiwan 600＋United States 1,400 stock pool, daily document load, 50 researchers, contractual data categories, HA failures, security threats, backups, regional disaster, or controlled cutover. Replica counts and dashboards are not evidence. The organization needs a signed, capacity-qualified deployment artifact and a jointly approved go-live bundle that proves the full data, model, security, operational, recovery, and research experience under representative load while preserving fail-closed behavior for every listing and source.

## Solution

Qualify all 2,000 listings and complete the required official and commercial source categories; retrain, calibrate, backtest, and govern the best eligible model for that support profile; then deploy the same signed application and contracts to a single-region, three-failure-domain Kubernetes production profile with warm-data／cold-application regional disaster recovery. Prove baseline capacity, fault tolerance, security, policy deletion, restore, failover／failback, and staged cutover through immutable reports and real fault injection. Go live only after all hard-gate owners sign the final acceptance bundle; otherwise remain a restricted pilot.

## User Stories

1. As a source steward, I want Taiwan and United States EOD, company action, symbol history, news, filings／fundamentals, macro vintages, institutional forecasts, and company-level consensus sources qualified, so that the complete product claim has evidence for every promised category.
2. As a source steward, I want any missing commercial category to keep the deployment a restricted pilot, so that infrastructure readiness cannot override data rights.
3. As a data owner, I want all 2,000 listings checked for identity, calendar, symbol history, company actions, sessions, entitlement, and coverage, so that the final stock pool is not a silent allowlist of easy cases.
4. As a researcher, I want listings with 240 or more valid sessions to show full price support, 60–239 to show degraded support, and fewer than 60 or a missing anchor to show unavailable, so that coverage is transparent.
5. As a researcher, I want every expected optional-modality partition to distinguish valid empty, uncovered, late, policy blocked, and processing failure, so that missing content has precise meaning.
6. As a model governor, I want the model retrained, calibrated, backtested, and approved for Taiwan 600＋United States 1,400, so that a 100＋100 artifact cannot silently expand its support profile.
7. As a model governor, I want the best eligible artifact to serve whether it is logistic or neural, so that architecture prestige never overrides complete gate evidence.
8. As a researcher, I want the full Traditional Chinese matrix and listing research experience to remain correct at 2,000-listing scale, so that scale does not reduce lineage, support, policy display, or accessibility.
9. As a platform engineer, I want compose-dev, compose-pilot, and k8s-production to use the same signed deployment artifact and typed configuration, so that deployment profile does not change domain results.
10. As a platform engineer, I want Kubernetes production spread across three failure domains with isolated runtime responsibilities, so that one pod, node, or zone failure does not create a second domain model.
11. As a platform engineer, I want application, orchestration, data, edge, and observability responsibilities separated by identities, network policy, and quotas, so that namespace topology supports operations without becoming module seams.
12. As an operations engineer, I want daily-critical capacity reserved ahead of the first market cutoff, so that training, backfill, or maintenance cannot cause the formal EOD batch to miss its objective.
13. As an operations engineer, I want the baseline workload to finish the worst of three EOD runs by T+105 and inference plus attribution within ten minutes, so that there is measured incident margin before T+120.
14. As a researcher, I want bounded REST queries at p95 `<=500 ms` and p99 `<=1.5 s` under sustained and burst load, so that the research interface remains usable during formal processing.
15. As a capacity owner, I want critical-resource p95 below 70% of approved capacity, so that a passing benchmark contains operational headroom.
16. As an operations engineer, I want actual API, relay, Dagster, worker, pod, node, zone, PostgreSQL, PgBouncer, and object failures injected, so that HA is proven by service results rather than replica counts.
17. As a security administrator, I want OIDC AAL2, WebAuthn step-up, workload identity, default-deny networking, source-restricted egress, secret rotation, and least-privilege storage roles, so that formal access has no development shortcut.
18. As an auditor, I want signed images, models, SBOM, provenance, audit-chain verification, authorization decisions, and policy deletion evidence, so that formal production artifacts and actions can be independently checked.
19. As a source steward, I want a policy deletion to stop use, remove content and derivatives, replay after restore, and produce a deletion certificate, so that seven-year governance does not lock content past its latest deletion deadline.
20. As a recovery owner, I want a versioned recovery set binding database target, object watermark, deletion-ledger sequence, and deployment digests, so that restored service does not pretend missing objects are complete.
21. As a recovery owner, I want database RPO `<=15 minutes`, object RPO `<=24 hours`, and research-service RTO `<=4 hours` proven by failover and failback, so that regional disaster expectations are empirical.
22. As a security administrator, I want only one active deployment generation during failover, cutover, and failback, so that two regions cannot both publish formal state.
23. As an operations engineer, I want source, schema, coverage, object, outbox, probability, model rollback, notification, clock, audit, and deletion runbooks exercised by the receiving owners, so that handoff is operational rather than documentary.
24. As a delivery owner, I want cutover to progress through formal trust and data, read-only research, ingestion／projection, five EOD shadows, atomic assignment, publication, and notification, so that every stage has an observation and rollback point.
25. As a platform owner, I want deployment rollback to restore the last signed compatible artifact without rolling back canonical data or audit, so that application recovery never rewrites authoritative evidence.
26. As a security assessor, I want an independent penetration test with all Critical and High findings remediated and retested, so that go-live is not self-attested.
27. As a governance owner, I want platform, data, model, source, and security owners to sign the final bundle while designated vetoes remain independent, so that majority approval cannot override a hard gate.
28. As an auditor, I want the final acceptance bundle retained for at least seven years, so that product scope, capacity, source rights, model assignment, security, recovery, and go-live decisions remain reproducible.

## Implementation Decisions

- P5 is gated by a passing P4 bundle, qualification of Taiwan 600＋United States 1,400 listings, and active `DEP-NEWS-TW-01`, `DEP-NEWS-US-01`, `DEP-INSTITUTIONAL-01`, and `DEP-CONSENSUS-01`. Missing dependencies permit only a restricted pilot.
- Complete-product data covers qualified Taiwan／United States EOD, company actions and symbol history; both markets' news and filings／fundamentals; both markets' macro vintages; at least one approved institutional forecast dataflow; and one company-level consensus source with historical vintages and model rights.
- Every listing receives a machine-readable qualification and remains visible as full, degraded, or unavailable. Price support uses the approved 240／60-session boundaries; optional modalities do not require daily content but every expected partition has a coverage report and precise absence reason.
- Retrain, calibrate, reproduce, backtest, and govern the eligible model against the 2,000-listing support profile. The current model is the best artifact that passes all gates; neural is not privileged over logistic.
- Keep three deployment profiles: compose-dev for development without capacity claim, compose-pilot on supported Linux for complete small-profile operation without HA, and k8s-production for baseline capacity, HA, and DR. All use the same module interfaces, signed artifacts, typed configuration, and domain semantics.
- Kubernetes production runs in one region across three failure domains. Stateless application roles, PostgreSQL synchronous replication, and replicated object storage provide regional HA; a second region holds warm data and cold application capacity for manual disaster recovery, never active-active.
- Separate edge, application, orchestration, data, and observability operational responsibilities with default-deny network policies, independent service accounts, database roles, object prefixes, quotas, and approved egress. Namespace layout does not redefine application modules.
- The portable reference uses CloudNativePG with three instances, at least one synchronous standby, and two PgBouncer replicas, plus a multi-replica SeaweedFS topology. Managed PostgreSQL and S3-compatible providers may replace them only after the same provider and recovery contracts pass.
- Use separate runtime roles for API／BFF, Dagster webserver／daemon／code location, daily-critical, maintenance, backfill-training, outbox relay, notification relay, migration, backup／restore, and observability. No authoritative local disk state exists outside approved PostgreSQL and object storage.
- Reserve daily-critical quota and Guaranteed QoS during a protection window starting 30 minutes before the first market cutoff. Backfill and training checkpoint, pause, or recreate and cannot consume the reserved baseline.
- The baseline representative load is 2,000 listings, up to 5,000 new／updated document versions per day, seven-year point-in-time data, 50 users, and REST 10 sustained／50 burst RPS. The stretch profile is four times the listing, document, and traffic load and tests safe degradation rather than T+120.
- A capacity report requires three consecutive complete baseline runs, judging deadlines and correctness by the worst run. EOD completes by T+105, inference plus attribution is `<=10 minutes`, REST p95 is `<=500 ms`, p99 is `<=1.5 s`, error rate is below 0.1%, and critical-resource p95 is `<=70%` of approved capacity.
- Any missing listing result／reason, incorrect manifest, probability, assignment, policy, audit, data loss, policy bypass, OOM loop, or erroneous publication fails the benchmark regardless of latency.
- Deployment artifacts are signed, content addressed, and include application／UI images, Compose, Helm and overlays, typed configuration, migrations, SBOM, provenance, dashboards, alert rules, and runbooks. Each work attempt and EOD batch pins one artifact digest; no formal batch mixes versions.
- Rolling releases use role-specific probes, topology spread, canary research checks, expand／contract migrations, protected-window restrictions, and forward fixes for incompatible schema. Rollback never rolls back the canonical ledger or prediction history.
- Security requires production OIDC AAL2, phishing-resistant WebAuthn step-up for high-risk actions, short-lived workload identity, formal SecretProvider, least privilege, default-deny ingress／egress, SSRF defenses, signed artifacts, safe model formats, append-only audit, policy deletion, and independent penetration testing.
- HA acceptance injects application process, pod, node, zone, PostgreSQL, PgBouncer, object, relay, and orchestration failures and verifies topology, PDB, leases, fencing, synchronous commit, repair, projection recovery, and preserved daily-critical capacity.
- Recovery uses a versioned recovery set that binds PostgreSQL recovery target, object inventory／watermark, deletion-ledger sequence, configuration, and deployment digests. Restore replays deletion before serving, validates reference graphs and checksums, rebuilds projections, and opens read-only research before ingestion or publication.
- Regional disaster targets are application PostgreSQL RPO `<=15 minutes`, object／artifact RPO `<=24 hours`, and complete research-service RTO `<=4 hours`. Failover and failback are manual SEV1 actions with dual approval, regional source-entitlement confirmation, fencing, and a monotonically increasing deployment generation.
- Cutover uses formal trust roots and qualified data, then read-only research, ingestion／projection, five eligible dual-market EOD shadow cycles, and finally atomic production assignment, publication, and notification. Each step has smoke checks, observation, and rollback.
- Go／No-Go requires signatures from platform, data, model, source, and security owners. Model approver, source steward, security dual control, and any hard-gate owner's veto cannot be overridden by majority vote.
- The final bundle binds dependencies and contracts, all 2,000 listing qualifications, data／model／prediction lineage, current and rollback models, shadow evidence, capacity, HA, security, deletion, restore／DR, SLO／incidents, UI／REST accessibility, runbook exercises, findings, approvals, and expiry.

## Testing Decisions

- The highest seam is the signed deployment artifact running in a production-shaped three-failure-domain staging environment under the baseline workload, observed through EOD publication, REST／UI, canonical ledgers, security decisions, incidents, recovery, and cutover. Infrastructure component tests supplement but do not replace this seam.
- Qualify all 2,000 listings and every promised source category. Verify policy-blocked restricted-pilot behavior when any commercial dependency is absent, expired, or revoked.
- Run three consecutive baseline workloads covering normal days, half days, daylight-saving transitions, temporary closures, document spikes, schedule overlap, API traffic, replication, and background backfill. Use the worst run for hard deadlines and correctness.
- Verify T+105 EOD, ten-minute inference plus attribution, REST latency and error-rate boundaries, resource headroom, complete result-or-reason coverage, exact assignment and policy, audit completeness, and zero erroneous publication.
- Run the four-times stretch profile and inject background backlog. It may miss T+120, but it must not lose data, bypass policy, publish incorrect results, enter OOM crash loops, harm the simultaneous baseline path, or retain backlog beyond 24 hours.
- Inject API, relay, Dagster, worker, pod, node, zone, PostgreSQL primary, synchronous replica, PgBouncer, and object faults. Verify PDB, topology spread, lease and fencing, failover, repair, idempotency, and reserved daily capacity by external results.
- Execute security decision matrices for identity, action grants, source entitlements, data-protection classes, duties, step-up, secret rotation／revocation, source egress, SSRF, rate limits, malicious files, unsafe model artifacts, audit integrity, and policy deletion.
- Verify image, model, deployment, SBOM, provenance, signature, vulnerability, license, admission, runtime identity, database role, object prefix, and network provider contracts. Critical or High findings remain hard vetoes until remediated and independently retested.
- Perform monthly restore evidence, a complete quarterly prediction-record lineage restore, and a regional failover／failback. Measure database and object RPO, service RTO, deletion-ledger replay, missing-object behavior, audit-chain restoration, and single deployment generation.
- Exercise source-rate, schema, coverage, object corruption, outbox, invalid probability, model rollback, notification, clock, audit gap, policy deletion, backup restore then re-delete, and stale／blocked data runbooks with the named receiving owners.
- Test rolling release, canary, migration rehearsal, protected-window restrictions, mixed-version prevention, rollout-gate failure, compatible rollback, incompatible forward fix, and preservation of immutable work attempts and predictions.
- Execute cutover in the approved order and verify a rollback point at every stage. Neither pilot nor recovery region may publish the same market batch while the formal deployment generation is active.
- Verify full Traditional Chinese research behavior, 2,000-listing search／matrix, listing details, evidence policy, lineage, accessibility, snapshot cursors, ETags, history, and backtests under representative traffic.
- Generate and checksum the final acceptance bundle only after every hard gate and independent approval succeeds; a failed or blocked attempt produces new immutable evidence and never edits a prior bundle.

## Out of Scope

- Active-active multi-region writes, distributed SQL, a message broker, lakehouse formats, online feature stores, vector databases, GPU production inference, and application-module microservices.
- Provider-specific cloud IaC before cloud, region, budget, and managed-service choices are supplied; portable Helm, typed configuration, and provider contracts remain the core deliverable.
- Social／forum crawling, arbitrary investor-relations content, unlicensed reports, public anonymous access, multi-tenant SaaS, and consumer portfolio features.
- Evidence dossier／PDF／email, watchlists, collaboration, personalized dashboards, user-modified model inputs, and complex scenario analysis.
- Automated trading, broker integration, personalized investment advice, intraday low-latency prediction, and high-frequency use.

## Further Notes

- `ready-for-agent` means the production-baseline work is fully specified. It does not imply the external source dependencies, cloud provider inputs, or independent assessment have already been supplied.
- Compose remains a complete semantic profile, but only supported-Linux compose-pilot carries the small-capacity commitment and only k8s-production carries HA／DR commitments.
- Actual absolute hardware and cost become approved only through the immutable capacity report; no unmeasured sizing promise is part of this spec.
- Any change to information cutoff, identity, trend labels, prediction records, service assignment, gates, data ownership, module interfaces, or deployment ownership requires the established core-semantics change control and, when applicable, an ADR.
