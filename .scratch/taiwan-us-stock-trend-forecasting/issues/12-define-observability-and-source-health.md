# 定義可觀測性、來源健康與異常處理

Type: grilling
Status: resolved
Blocked by: 05, 06, 09, 11

## Question

每個來源與管線步驟的 freshness、coverage、schema、重複率、失敗率、延遲與資料漂移如何量測；指數退避、jitter、隔離區、補跑、熔斷、事件嚴重度、webhook／SMTP 告警和營運儀表板需採哪些狀態與服務目標？

## Comments

### Grilling round 1

使用者以「全部採建議」確認：

- 將來源健康評估、資料集資格、預測資料支援狀態及使用者服務影響分層，禁止以單一紅綠燈或綜合分數互相替代。
- OperationsControl 在 PostgreSQL 保存版本化健康評估、事故生命週期及通知投遞，作為權威狀態；telemetry、Dagster UI 與通知平台只是 projection／adapter。
- 每市場 99% 合格交易日在實際收盤後兩小時內發布完整正式預測批次；原建議的 60 日視窗因離散批次無法容許一次失敗，後由 round 2 修正為正式 250 日視窗與 60 日營運護欄。已發布批次的股票池結果／不可用原因與完整譜系仍是 100% 硬性不變量。
- 研究 REST 30 日可用性至少 99.5%，bounded query p95 不超過 500 ms、p99 不超過 1.5 秒；evidence projection 99% 在 15 分鐘內追上核心 projection。
- 正式批次只有在逐掛牌三期間結果／不可用原因、完整譜系、核心 projection 及 schema／機率／涵蓋／checksum 驗證全部成立時才算完成；資訊截止點仍為收盤後 90 分鐘。
- 來源健康最小評估單位為 source × dataset × market/scope × expected partition × observation window；供應商彙總只作 projection。
- 版本化 expectation policy 固定發布／grace window、預期分區／股票池／附件／欄位、schema、coverage、交易日曆及政策資格；歷史分布只偵測異常，不自行重寫預期。

### Grilling round 2

使用者以「全部採建議」確認：

- 正式批次時效 SLO 改為每市場滾動 250 個合格交易日至少 99%，60 日營運視窗至少 59／60 且不得連續兩日失敗；每次逾時仍立即建立事故。
- Freshness 以 `first_observed_at − expected_available_at` 計算，來源發布時間、業務有效時間與 cutoff data age 分欄保存；on-time／late／expired 由資料集 expectation policy 決定。
- Coverage 以預期鍵集合計算 eligible received、缺失、額外、無效、隔離及未知鍵；硬性資料通常要求 100%，文件有效空集合必須證明所有預期來源均已檢查。
- 相容新增欄位只 warning；刪除、型別／單位／時區／枚舉／語意變更阻止發布並隔離；checksum、容器格式、主鍵及交叉欄位不變量失敗視為 integrity failure。
- 分開量測重複 receipt、相同內容、來源主鍵修訂／衝突、正規化主鍵碰撞及文件近似重複；正常輪詢與內容定址去重不直接告警。
- 分開記錄 attempt outcome、fetch latency、processing latency、end-to-end partition latency、retry 次數與等待；metric label 僅用低基數 source／dataset／market／operation／outcome。
- 健康評估保存 freshness、coverage、schema、integrity、policy、access 六維結果，再依用途推導 ready／degraded／blocked／unknown；不產生可平均的 0–100 分數。
- 動態基線至少八個可比觀測，超過絕對下限且偏離 robust median 3 MAD、連續兩窗才 warning；硬性契約或 SLO 立即告警，基線不得自動修改 expectation policy。

### Grilling round 3

使用者以「全部採建議」確認：

- 來源 adapter 回報結構化 outcome，WorkCoordinator 統一 retry：連線／timeout／408／429／可恢復 5xx 可重試；認證／政策、固定 404、schema／integrity 及無效命令不盲目重試。
- Daily-critical 預設 base 2 秒、cap 2 分鐘、full jitter，最多五次或累計 15 分鐘；預算耗盡結束 attempt，另建 recovery work，maintenance／backfill 使用獨立慢速預算。
- 以 source＋credential＋endpoint group 集中 token bucket／併發；五次連續 transient failure 或最近至少十次中 50% 失敗開 circuit，初次 1 分鐘、half-open 單一探測、反覆失敗 cap 15 分鐘。
- 429 為 rate-limited 並遵守 Retry-After，不與 outage 混同；circuit 只抑制外呼，不能停止 deadline、coverage 或 SLO 評估。
- 隔離紀錄保存原始證據、範圍、原因、時間、owner、處置及事故；修復以新 decoder／身分／政策／輸入產生新資料集版本，舊隔離內容不修改或直接放行。
- 必要資料不允許隱式 last-known-good；可選模態只能依 DataSelection 的最大 age、政策與時間點條件選較早版本，並在特徵快照保存實際版本、age 及降級原因。
- Cutoff 前已取得但內部處理延遲者可發布逾時正式批次並計 SLO breach；首次取得晚於 cutoff 的資料只能進後續正式資料或 retrospective replay，不重寫既有預測。
- Gap detector 產生固定缺失分區、版本及影響範圍的 recovery plan，從 checkpoint／published artifact 繼續，以 maintenance／backfill pool 執行並驗證後再關閉事故。

### Grilling round 4

使用者以「全部採建議」確認：

- Alert evaluation、外部通知及營運事故分開；同一根因的來源、特徵與預測訊號關聯到一個具 owner、severity 及生命週期的事故，不為 retry／掛牌逐一通知。
- SEV1 為已服務錯誤／損壞／無譜系預測、兩市場中斷、禁止內容外洩或錯誤模型指派無安全回退；SEV2 為單市場逾時、必要來源／批次阻斷、API 大範圍故障、超過 5% 股票池異常不可預測或 promotion／rollback 失敗。
- SEV3 涵蓋可選模態、evidence projection、非關鍵來源及 maintenance／backfill 降級；SEV4 為尚無使用者影響的統計／容量異常。發布前擋住的 integrity 問題通常 SEV2，已污染結果升 SEV1。
- 每市場以 T+90 cutoff readiness、T+105 FeatureSnapshot、T+115 ForecastBatch 驗證及 T+120 SLO breach 建立同一批次事故的階段訊號。
- Alert fingerprint 包含 rule、source／workflow、market／scope 與 batch／window；下游通知可被上游事故抑制但評估證據仍保存，連續兩次成功或一個完整正式批次才恢復。
- SEV1 webhook／SMTP 立即通知且 5 分鐘未確認升級；SEV2 立即通知且 15 分鐘未確認升級；SEV3 小時聚合、SEV4 dashboard／每日摘要。通知使用 durable outbox、冪等、delivery status、dead-letter 與每週合成測試。
- 事故採 open → acknowledged → mitigating → monitoring → resolved，保存 actor、時間、理由、owner、影響、runbook、恢復條件及 work；SEV1、重複 SEV2 或 budget 耗盡要求事後檢討。
- 維護只能有期限地抑制通知，不能停止健康／SLO 量測；使用者影響仍計入 SLO，integrity／policy／SEV1 不得被一般維護抑制。
- 每市場 250 日最多兩次批次逾時，API 30 日約 216 分鐘 budget，evidence projection 允許 1% 超過 15 分鐘；消耗 50% 限制高風險變更，耗盡時凍結非修復部署、promotion 與大型回補，安全／政策／事故修復除外。

### Grilling round 5

使用者以「全部採建議」確認：

- 應用只輸出 OpenTelemetry／OTLP、Prometheus／OpenMetrics、結構化 stdout 與 W3C Trace Context；OTel Collector 為統一 seam，Compose 預設 Prometheus＋Alertmanager、Grafana、Loki、Tempo，AGPL 未獲組織核准時經相同 seam 改用 OpenSearch／Jaeger。
- Telemetry 以 trace、work／attempt、forecast batch、dataset、feature、model、incident ID 關聯；metric labels 僅使用低基數維度，不含 listing／ticker／document／URL／work ID，並禁止敏感 payload。
- Canonical 健康／SLO／事故／通知／品質／漂移結果保存七年；高解析 metrics 30 日、低解析彙總 15 個月、一般 logs 30 日、一般 traces 7 日，SEV1／2 redacted bundle 七年，錯誤／治理／正式 EOD traces 30 日。
- 錯誤、SEV、核准／升版及正式 EOD 100% trace，正常研究 API 10%、例行 maintenance 1%；telemetry 不作來源證據或模型復現資料。
- SQL／Python、GX、Evidently 只經共同 QualityCheckResult／DriftCheckResult interface 產生具 check／版本／scope／window／reference／observed／threshold／status／evidence／tool 的結果；OperationsControl 決定連續窗、事故與通知。
- EOD 檢查 schema／range／missing／coverage／OOD／支援／預測與 gate 摘要；每週比較 20／60 sessions 的特徵／可用性及成熟標籤品質，每月執行完整切片、文件品質、校準、gate collapse、經濟與長期趨勢；樣本不足回報 insufficient-data。
- 沿用既有兩個週窗漂移門檻建立 drift-early TrainingIntent，仍走完整 gate／shadow／人工核准；來源 outage／schema／coverage／policy 先修資料，單一 drift 或 gate collapse 不自動回退。
- 儀表板分為服務總覽、來源健康、關鍵路徑、品質／漂移、模型營運及事故／通知六層，均可下鑽到權威 ID，Grafana URL 不作稽核證據。

### Grilling round 6

使用者以「全部採建議」確認：

- Process live 只檢查程序，startup 驗證設定／migration／schema／artifact，ready 驗證 runtime role 能安全服務；外部來源健康不進容器 readiness。每五分鐘以合成 client 驗證代表性台美研究 REST、不可預測、ETag 與延遲。
- 監控 Dagster、outbox、worker pools、OTel Collector、通知 relay heartbeat 及所有 expected evaluations；連續缺少兩次即建立監控缺口事故。
- Duration 使用 monotonic clock、領域 instant 使用同步 UTC；clock offset 超過 500 ms warning，超過 2 秒阻止新正式 cutoff／預測發布並建立 SEV2。
- 正式 alert／health／drift rules 版本控制，包含 rule／version、SLI、scope、window、threshold、severity、owner、runbook、fingerprint、依賴、恢復與抑制；變更需 review、歷史 replay 及七日 shadow，緊急例外綁事故及事後檢討。
- 自動修復只允許既定 retry／circuit、lease recovery、outbox／projection rebuild、保護 daily-critical 資源及已核准回退；禁止放行隔離、改 schema／expectation／政策、使用超齡資料、未核准升版、刪證據或自動 resolve SEV1／2。
- Terms／robots／API 文件／授權頁每週 hash；credentials 每日檢查並於 30／14／7／1 日告警，商業 entitlement／合約於 90／60／30／7 日、TLS 於 30／14／7 日告警。權利不明時 policy-block 新用途，不由監控直接刪除舊證據。
- 所有 SEV1／2 規則及自動修復啟用前須有含確認、權威查詢、安全操作、禁令、升級、恢復與證據的 runbook；每季演練來源、schema、coverage、物件、outbox、機率、projection、通知及時鐘故障。
- Meta-observability 監控 Collector queue／drop、Prometheus scrape／rule／storage、Alertmanager delivery、Loki／Tempo ingest、dashboard freshness，並每週走完整 synthetic incident→webhook／SMTP→關閉流程；exporter 非阻塞有界，監控後端故障不得拖垮正式批次。

## Answer

共有理解由使用者明確確認。首版將來源健康評估、資料集資格、預測資料支援與使用者服務影響分層保存；`OperationsControl` 以 application PostgreSQL 的版本化健康、SLO、事故及通知帳本作權威，telemetry／Dagster／品質工具與通知平台只是 projection／adapter。每市場正式預測批次須在最近 250 個 eligible sessions 達成 99% 的 T+120 完成率，60-session 護欄至少 59／60 且不能連續 miss；批次股票池完整、三期間結果／不可用原因、譜系、schema、機率及 checksum 永遠是 100% 硬性不變量。

來源健康以 source×dataset×market/scope×expected partition×window 評估 freshness、coverage、schema、integrity、policy、access 六維結果；freshness 依 first-observed time，coverage 依預期鍵集合。Retry、full jitter、token bucket、circuit breaker、隔離與 RecoveryPlan 集中於既有 seam，禁止隱式 stale fallback、修改隔離證據或讓 cutoff 後才取得的資料重寫正式預測。

AlertEvaluation、NotificationDelivery 與營運事故分離，採 SEV1–SEV4、因果 fingerprint、`open → acknowledged → mitigating → monitoring → resolved`、durable webhook／SMTP、error-budget 變更凍結、rules-as-code、runbook、季度演練及 meta-observability。品質／漂移工具只產生共同 QualityCheckResult／DriftCheckResult；兩個週窗達既定門檻只建立 `drift_early` TrainingIntent，仍需完整模型升版治理。

應用只輸出 OTLP、OpenMetrics、結構化 logs 與 W3C Trace Context，Compose 預設 Prometheus／Alertmanager 與經授權審查的 Grafana／Loki／Tempo，並保留 OpenSearch／Jaeger adapter。Canonical 健康、SLO、事故、通知與品質／漂移結果保存七年；高容量 telemetry 採分層短期保存。六層 dashboard、合成 REST／通知探測、dead-man、clock-skew 阻斷、自動修復權限與 SEV1／2 人工結案均納入首版。

- Design contract: [`docs/design/observability-source-health-and-incidents.md`](../../../docs/design/observability-source-health-and-incidents.md)
- ADR: [`docs/adr/0011-canonical-operational-ledger-separate-from-telemetry.md`](../../../docs/adr/0011-canonical-operational-ledger-separate-from-telemetry.md)
