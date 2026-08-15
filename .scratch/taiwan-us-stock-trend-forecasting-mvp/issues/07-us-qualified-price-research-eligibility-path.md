# 07 — 美股零付費認證行情到研究資格狀態

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 將至少一個技術上涵蓋 EOD、公司行動及美股 reference／symbol lifecycle 的零付費 authenticated provider（Alpaca、Tiingo 或同等來源）接入共同 DataSupply，並在 Operations 程式頁面完成來源憑證的 write-only 管理與 provider-owned 重新申請路徑。缺少真實用途資格或有效憑證時，同一垂直路徑發布可驗證的 `credential_required`／`policy_blocked`，但 adapter、REST、UI、workflow 及 acceptance 可完整交付。

**Blocked by:** 05 — 發布 P1 雙市場工程脊柱 acceptance bundle

**Source basis:** 待選零付費方案逐 dataset／distribution 建立來源使用依據；真實 live qualification 取決於部署主體符合方案條款及有效憑證，但不存在付費、採購或 sales gate

**Trace IDs:** `P2-ENTRY-01`, `P2-ENTRY-02`, `P2-TRACE-US-01`, `P2-TRACE-CREDENTIAL-01`

Status: ready-for-agent

- [x] 10＋10 manifest 的美股部分涵蓋主要交易所普通股、股別／ADR、ticker 變更、公司行動、半日市、暫停及歷史下市標的。
- [x] 選定的零付費 authenticated adapter 及其 missing-credential path 通過與台灣相同的 Collector／Decoder、checkpoint、rate、policy、coverage、revision、identity 及 reference-graph interface contracts；provider test double／fixture 只證明工程契約，不冒充正式資料。
- [x] 選定 provider 或版本化 source bundle 的未調整 EOD、公司行動、symbol history 與美國交易日曆可建立不可變資料集及內部 AdjustmentVersion，不以免費網站或 provider latest adjusted close 補足；每個 bundle 成員保留獨立政策、譜系、涵蓋與缺口。
- [x] 合格、`credential_required` 或 unavailable 資料集的 provider、plan、dataset／distribution、條款雜湊、部署主體分類、credential kind、允許用途、涵蓋、schema、integrity、實得歷史深度與譜系能由研究支援及 OperationsControl 查詢驗證，且不回傳 secret value。
- [x] Operations 程式頁面與 REST 可列出來源憑證就緒狀態並執行 set、rotate、revoke、validate；API key／secret／token 是 write-only，只進入 SecretProvider，不進 repository、database、typed configuration、work command、artifact、response、outbox、log 或 trace。
- [x] 「重新申請」顯示版本化 provider-owned registration／key-management URL，使用者完成外部自助流程後可輸入替代憑證；除非提供者另有正式發行 API，程式不宣稱能自動建立帳號、處理 CAPTCHA／email／MFA 或接受條款。
- [x] 缺少、無效、到期或撤銷憑證時，workflow 不呼叫 provider，新的擷取、特徵、訓練及研究展示一致 `credential_required`／`policy_blocked`；此 fail-closed trace 與 adapter contract 可在無真實 secret 的 Compose acceptance 中完整通過。
- [x] 方案條款撤回、限制用途、部署主體不符合方案分類或 dataset-specific 使用依據不足時，新的擷取與下游使用一致 `policy_blocked`；`$0`、持有憑證或連線成功都不會自動形成來源使用資格。
- [x] Correction、symbol reuse、跨掛牌身分衝突、公司行動缺失與不完整分區不會形成合格正式輸入。
- [x] 端到端展示證明美國來源 adapter 可替換但共同資料集、credential readiness、預測支援、REST 與 audit 語意不變；只有 opt-in 提供真實合格帳號與憑證的 live contract 才可建立正式來源證據，沒有時不影響本 ticket 的工程完成狀態。

## Implementation notes

- 公共 seam：共同 `DataSupply.materialize`、研究資格 REST／UI、Operations credential REST／UI，以及隔離的 Ticket 07 Compose acceptance；來源秘密只由可替換 `SecretProvider` 解密並以短生命週期 lease 提供給 validator／collector。
- Alpaca Basic 工程契約涵蓋 raw SIP daily bars、官方 grouped corporate-actions schema、完整分頁、calendar、checkpoint、rate／coverage／revision、永久 listing identity 與內部 AdjustmentVersion；bars、actions 與 calendar 各有獨立 policy、raw object、coverage、schema、gap 與 lineage receipt，provider double 明確只屬 engineering contract。
- Operations 的 set／rotate／revoke／validate 在授權後執行，支援 expiry、stale-validation fencing、補償式 secret write 與持久 cleanup retry；研究資格 REST／UI 明列新擷取、特徵、訓練及研究展示的同一 fail-closed readiness。
- 驗證：`python -m pytest --basetemp=.pytest-tmp/full-final-review`（225 passed）、`python -m mypy src tests`、`python -m ruff check .`、`python -m ruff format --check .`、`docker compose -f compose.yaml config --quiet`、Docker image build，以及隔離 Compose `ticket-07-acceptance`（8/8 checks passed）。
- 本機未提供真實來源憑證，因此 `formal_qualification=false`、`live_validation=not_run`；opt-in credential validate contract 會對 10 檔 manifest ticker、raw SIP daily bars、強制分頁、官方公司行動 schema 與半日曆執行真實 API probe，只保存去識別化 evidence。長期歷史深度與正式 backfill qualification 由 Ticket 08 承接。
