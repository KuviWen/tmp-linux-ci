# 06 — 台股合格行情到研究資格狀態

**What to build:** 將 TWSE 明確允許的當期來源與一個契約歷史行情 adapter 接入相同 DataSupply 契約，從未調整 EOD、公司行動與 symbol history 建立可追溯台股資料集、內部調整版本及研究／營運資格狀態；外部權利未成立時，完整路徑必須以 `policy_blocked` 結束而不使用替代網站。

**Blocked by:** 05 — 發布 P1 雙市場工程脊柱 acceptance bundle

**External gate:** `DEP-MKT-TW-01`

**Trace IDs:** `P2-ENTRY-01`, `P2-ENTRY-02`, `P2-TRACE-TW-01`

Status: ready-for-agent

- [ ] 版本化 10＋10 manifest 中的台股涵蓋普通股、ticker 變更、公司行動、暫停／半日市及訓練歷史中的下市案例。
- [x] TWSE 當期與契約歷史 adapter 通過共同 Collector／Decoder、checkpoint、rate、policy、coverage、revision、identity 及 reference-graph contracts，供應商原生型別不外洩。
- [x] 原始未調整價格、公司行動及掛牌生命週期建立不可變資料集與內部 AdjustmentVersion，供應商 adjusted close 只能交叉驗證。
- [ ] 來源權利完整時，資料集資格、涵蓋、schema、integrity、政策及歷史深度能由營運查詢與研究支援狀態追溯到原始證據。
- [x] `DEP-MKT-TW-01` 未核實、到期或用途／保存不足時，adapter 維持 disabled／policy-blocked，REST／UI 顯示穩定原因且不暗用爬蟲、測試 key、人工下載或其他免費來源。
- [x] Late、correction、withdrawal、身分歧義、缺公司行動與不完整涵蓋分別產生新版本、隔離或阻斷證據，不覆寫已發布資料。
- [x] 台股資料路徑可由外部展示一個合格或受阻掛牌從來源政策、資料集與調整版本到研究／營運狀態的完整譜系。

## Implementation notes

- Public seams: `DataSupply.materialize(SourcePartitionRequest)`, `GET /api/v1/research/listings/{listing_id}/price-eligibility`, `GET /api/v1/operations/sources`, the listing eligibility UI, and the `ticket-06-acceptance` Compose profile.
- The source boundary is the shared `SourceCollector`／`SourceDecoder` adapter. Authorization checks all six required source uses before the adapter can be contacted; canonical objects, authorization audit, eligibility, dataset, and adjustment evidence publish transactionally.
- Durable source checkpoints advance only with atomic evidence publication. A provider rate-limit response yields a retryable `deferred` record with `retry_after_seconds` and never advances the checkpoint. Raw content identity is separate from append-only retrieval receipts.
- Historical publication accepts platform-observed or archive-attested evidence for historical training／backtest research, but only through a content-addressed `HistoricalAvailabilityClaim` minted by `TaiwanPriceQualificationWorkflow`; the workflow requires the dedicated `price_qualification.govern` action, current source policy／entitlement, all six required uses, and an atomic authorization audit. Caller-supplied claim mappings and current-only publication claims fail closed.
- Formal research qualification additionally requires the versioned manifest's exact current／historical source lineage plus verified governance artifacts for the historical claim, `DEP-MKT-TW-01` approval provenance, and formal gate. Artifact kind, content hash, approval／contract IDs, policy／entitlement versions, evidence status, source IDs, manifest ID, dependency ID, and permitted use must all match. Synthetic provider-contract rows therefore remain non-formal.
- Every listing and operations eligibility read re-evaluates the currently resolved policy／entitlement and six required uses for published sources; revoke, suspend, replacement, or use removal overlays `policy_blocked` on the current projection before the materialization decision's `valid_until`, without rewriting its historical evidence. Provider rate limiting is an independent `deferred` state: policy remains passed, no raw／dataset is claimed, and UI reports that checkpoint did not advance.
- Qualification governance writes allowed authorization decisions atomically with either the successful claim／gate or a content-addressed rejection record. Rejection evidence contains only the operation and stable reason code, not the rejected claim payload.
- The manifest is deliberately `qualification_candidate`, so the first criterion remains unchecked until real listing-selection evidence is supplied. The qualified lineage branch is covered by synthetic provider-contract evidence only; without verified `DEP-MKT-TW-01` rights it is not formal evidence, so the fourth criterion also remains unchecked.
- Verified: `python -m pytest -m "not postgresql" -q` (163 passed, 1 deselected), ticket／authorization-focused pytest (52 passed), `python -m mypy src tests`, `python -m ruff check src tests`, `python -m ruff format --check src tests`, and an isolated `pip wheel --no-deps --no-build-isolation` build.
- Environment-limited: the PostgreSQL-marked test could not connect to a local server, and the Compose acceptance command could not start because Docker is not installed. Neither result is reported as passed.
