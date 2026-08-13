# 選擇部署拓撲與容量邊界

Type: grilling
Status: resolved
Blocked by: 03, 11, 12, 13

## Question

Docker Compose 與 Kubernetes 分別運行哪些程序、持久卷、排程 worker、API、研究介面與監控元件；如何配置水平擴充、備份還原、災難復原、資源限額、健康檢查與 rolling deployment，並以代表性負載界定 MVP 容量？

## Comments

### Grilling round 1

使用者以「全部採建議」確認：

- 定義 `compose-dev`、`compose-pilot`、`k8s-production` 三種部署輪廓；Compose 可完整執行，但只有 Kubernetes 輪廓承諾 HA。
- 正式環境為獨立信任域，不與非正式環境共用叢集／project、資料庫、bucket、KMS、OIDC client、來源憑證或備份。
- Kubernetes 提供 CloudNativePG＋SeaweedFS 的可攜參考 overlay，並允許以通過共同契約及復原驗收的受管 PostgreSQL／S3-compatible adapter 取代。
- 正式輪廓採單一區域、三個 failure domains；無狀態程序跨節點，PostgreSQL 一主多副本，物件資料多副本，另一區域只作災難復原而不做 active-active。
- 正式代表性負載為 2,000 個掛牌（台灣 600、美國 1,400）、每日最多 5,000 個新／更新文件版本、七年時間點資料、每批 6,000 筆三期間結果、50 名研究使用者、研究 REST 持續 10 RPS／突發 50 RPS；另以 8,000 個掛牌及每日 20,000 文件作四倍壓力測試。
- 容量先固定負載、SLO 與 benchmark，不預先承諾未量測的硬體數字；量測後形成 `small／baseline／stretch` sizing profile，正式 release gate 在指定 profile 驗證 T+120 日終批次與十分鐘推論門檻。

### Grilling round 2

使用者以「全部採建議」確認：

- Kubernetes 由 Dagster adapter 建立短生命週期 Job，依 `daily-critical`、`maintenance`、`backfill-training` 分別使用 command、身分、資源與 PriorityClass；Compose dev 可用本機 Docker launcher，pilot 禁止直接掛 Docker socket並改用預先啟動的 pool executor。
- 研究 UI 為 edge reverse proxy 提供的靜態資產；同源 FastAPI runtime 同時承擔 BFF、REST、server-side session 及授權入口，不另拆微服務。Kubernetes API 至少兩個跨 failure domain replicas，session 共享於 PostgreSQL。
- Dagster webserver、daemon 與 application code location 使用獨立 metadata database／角色，只作排程與 projection；工作真相仍在 `WorkCoordinator`、canonical ledger 及 outbox。
- Outbox／notification relay 是獨立常駐程序；Kubernetes 至少兩個 replicas，以 database lease、at-least-once 與冪等 consumer 運作，通知 adapter 有獨立身分及受限 egress。
- MLflow 是建議啟用但可選的 tracking／registry projection，不是正式批次硬依賴；使用獨立 database、物件 prefix 及權限，故障後可由 outbox 補建。
- Compose 使用 OTel Collector、Prometheus、Alertmanager、Grafana、Loki、Tempo；Kubernetes 將 Collector agent／gateway 與監控後端置於獨立 namespace、service account、quota 及優先節點，AGPL 未核准時採 OpenSearch／Jaeger overlay。
- 只有 PostgreSQL、自託管 SeaweedFS、metrics／logs／traces backend 及選配 artifact cache 可持久化；其他程序無本地權威狀態。Kubernetes PVC 需加密、快照、拓撲約束與備份，受管 adapter 不建立對應 PVC。

### Grilling round 3

使用者以「全部採建議」確認：

- Kubernetes API 以 CPU、處理中請求及 p95 latency 組合擴縮，最少 2、基準最多 8 replicas；快速 scale-out、延遲 scale-in，並限制每 pod 的連線、查詢時間與併發。
- 禁止 per-listing Job；擷取依 source×dataset×market×partition、文件依 source×observed-date×shard、特徵／預測依 market×cutoff×stock-pool-version、訓練評估依 intent／fold／trial 分割。
- `daily-critical` 使用保留 quota、最高非系統 PriorityClass 及保證容量；首個市場截止點前 30 分鐘開始保護視窗，停止新 backfill／training，既有低優先工作 checkpoint 後暫停或終止重建，兩市場發布後恢復。
- 所有容器設定 requests、memory limit 及 ephemeral-storage limit；daily-critical 採 Guaranteed QoS，API 有受控 burst，maintenance／backfill 低優先且有硬上限。正式推論走 CPU；選配 GPU pool 只供訓練／HPO，CPU 路徑仍須可驗收。
- 首版不新增 queue 或 KEDA；Dagster concurrency pool、`WorkCoordinator` 容量 metrics、Kubernetes Job 及 cluster autoscaler共同擴縮，但最低常駐容量本身就要能完成日終基準。
- Kubernetes 使用至少兩個 PgBouncer replicas 與每角色 transaction-pool 預算；migration、備份、replication 及需要 session semantics 的管理程序才受限直連。
- 儲存以至少 30 日實測日增量及各保存期、replication、backup、compaction、restore scratch 計算，預留 30% 或 12 個月成長量較大者；70% 告警、80% 停大型回補／HPO、90% 只允許日終、安全修復及政策性刪除。
- 四倍壓力測試不要求 T+120，但必須零資料遺失、錯誤發布、政策繞過及 OOM crash loop；baseline 日終仍守 SLO，其他 backlog 24 小時內排空，只有量測到不可分割瓶頸才擴張架構。

### Grilling round 4

使用者以「全部採建議」確認：

- 區域內以跨 failure domain 的同步 PostgreSQL replica 與多副本物件達成已提交資料 `RPO≈0`；區域災難時 application PostgreSQL `RPO≤15 分鐘`、物件／artifact `RPO≤24 小時`、整體研究服務 `RTO≤4 小時`，所以完整 artifact-dependent 服務的有效 RPO 最差為 24 小時。
- 建立版本化復原集合，固定 database target、object replication inventory／watermark、deletion-ledger sequence、設定及 image digest；保留最新可得 ledger，缺物件引用標為 unavailable／degraded，只有 reference graph 完整的批次可重新服務。
- PostgreSQL 使用持續 WAL、每日基礎備份、35 日 PITR 與 13 個月月度復原點；Dagster／MLflow metadata 每日短期備份。七年 canonical 資料由正式資料庫、物件與簽章 ledger archive 保存，不用七年整機快照替代。
- 來源內容依 source／policy／protection class 分離備份及加密範圍，需支援受控版本刪除或細粒度 cryptographic erasure；只有不含禁止內容的治理證據可不可變保存，還原後必先重播 deletion ledger。
- Kubernetes 正式輪廓使用暖資料、冷應用的次要區域；預建網路、信任、最小控制面及 secret references，剩餘計算資源由 IaC 在一小時內完成，平時不接正式流量。
- 跨區 failover 不自動執行；需綁 SEV1，由 platform admin＋security admin 雙人核准，source steward 確認地域資格，並驗證 OIDC、KMS／secret、egress、DNS、憑證、簽章與通知。
- 還原依序隔離舊區並提升 deployment epoch、恢復信任與金鑰、還原 PostgreSQL、掛物件、重播刪除、驗證 audit／復原集合／reference graph／checksum、重建 projection、合成測試；先開唯讀研究，再開擷取排程，最後開正式發布。
- 每月自動抽樣還原，每季完整還原一筆預測的全證據鏈，每年至少一次跨區 failover＋failback；量測 RPO／RTO並測遺失物件、損壞備份、KMS outage 及還原後重新刪除。
- 任何時刻只有一個 active deployment epoch；failback 前停止新寫入、排空 outbox、複寫增量、驗證 reference graph並撤銷舊 active region 的工作負載身分與 fencing authority，禁止雙區同時寫入或發布。

### Grilling round 5

使用者以「全部採建議」確認：

- 每次 release 產生簽章、內容定址的部署成品，包含 application／UI image、Compose、Helm＋overlays、設定 schema、migration、SBOM、provenance、dashboard／alert rules 與 runbook；GitOps 只能作 adapter。
- API、proxy、relay、Dagster webserver／code location 採 zero-unavailable rolling strategy、readiness 與 topology spread；API 先以單一 canary 驗證台美查詢、授權、ETag、延遲及錯誤率。Dagster daemon 維持單一 active instance。
- 每個 work attempt 與正式批次 manifest 固定 application image、設定及 workflow version；既有 Job 用原 digest 完成，新工作才用新成品，同一正式批次不得混版。安全／完整性事件終止工作時建立新 attempt。
- Migration 是具 advisory lock 的一次性 Job，使用 expand → 雙版本相容 → rollout → drain → contract，至少相容現行與前一 release；設定 DDL timeout並以代表性資料 rehearsal，破壞式 contract 延後到回退窗口與舊工作結束後。
- `/livez` 只證明程序，`/startupz` 驗簽章設定、schema、時鐘、身分及 artifact，`/readyz` 依角色驗證安全承接能力；API 包含 PostgreSQL、session／authorization 與 audit，relay 包含 lease／outbox，外部來源與監控不得進 pod readiness。
- 保護視窗禁止例行應用、資料庫、物件、Dagster、網路政策及容量型 rollout；進入視窗時停止擴大但不殺健康 pod。只有綁事故的安全／復原修補可例外且不得使正式批次混版。
- Rollout gate 失敗立即停止；schema 向後相容時恢復上一個已簽成品，但 canonical ledger、資料及 attempt 不回滾。無法向後相容的 migration 禁止一般 image rollback並須 forward fix。
- CloudNativePG、SeaweedFS 與監控後端各自使用具狀態升級程序；major upgrade 前完成備份、隔離還原及相容測試、不跳版。Compose pilot 可接受文件化停機。
- 非 secret 設定是 immutable、具 schema／digest 的部署成品，變更以 rollout 生效；來源政策／來源使用資格仍由 ledger 管理。Secret 以 provider version／reference及短效 lease 輪替。
- Release gate 必須通過供應鏈安全、migration rehearsal、Compose E2E、Kubernetes policy／probe／network／RBAC、provider contract、近期 restore、baseline 容量 benchmark及 canary；critical security、integrity、policy 或容量硬門檻皆為 veto。

### Grilling round 6

使用者以「全部採建議」確認：

- Kubernetes 依營運責任分 `edge`、`application`、`orchestration`、`data`、`observability` 五個 namespace；namespace 只承擔 quota、NetworkPolicy、service account 及維護責任，不等同 module seam。
- 只有同源研究 UI／REST 可經 identity-aware ingress 暴露於私有網路／VPN；Dagster、Grafana、MLflow 走受角色限制的內部管理入口，所有資料／telemetry endpoint 無外部 ingress。Compose dev 只綁 loopback，pilot 經私有 TLS proxy。
- 正式 default-deny egress；來源工作、通知 relay、OIDC／KMS／secret／time 各有獨立核准出口，訓練、推論與文件 sandbox 預設無網路，容器不得在啟動時下載程式或模型。
- API、各 pool、Dagster launcher／UI／daemon、relay、migration、backup／restore及監控各用獨立 service account、database role、object prefix；預設不 automount token，只有確有 control-plane 需求者取得最小短效權限。
- CloudNativePG 參考 overlay 使用三個跨 failure domain instances、至少一個同步 standby及兩個 PgBouncer；application、Dagster、MLflow 使用獨立 database／owner／migration／連線預算，WAL／備份／replication 有獨立資源。
- SeaweedFS 參考 overlay 使用三 master、至少三個跨 failure domain volume server、兩個 filer／S3 gateway及跨域副本 placement；filer metadata 獨立，並執行 S3 行為、完整性、刪除、故障及還原契約測試。
- Alert path 採至少兩個 OTel gateway、兩個 Prometheus、三個 Alertmanager、兩個 Grafana；Collector agent 為 DaemonSet。Logs／traces 可重建且僅需 1–2 replicas，canonical 營運證據仍在 application ledger。
- 關鍵程序使用 zone／host topology spread、anti-affinity 與 PDB；API、relay、PgBouncer、OTel gateway、Dagster webserver／code location 至少保留一個 ready replica，Dagster daemon維持 singleton＋surge replacement。
- Compose 核心清單固定 edge、API、Dagster 三程序、relay、三種 pool executor、PostgreSQL、SeaweedFS、OTel Collector、Prometheus、Alertmanager與選定的 dashboard／logs／traces；migration、backup、restore、benchmark 為 one-shot。Dev 可用本機物件 adapter，MLflow／完整 telemetry UI 可由 profile 關閉，但 canonical 治理能力不可關。

### Grilling round 7

使用者以「全部採建議」確認：

- Benchmark 以版本化 manifest 固定；公開 CI 使用同 schema／分布／邊界案例的合成資料，正式容量 gate 在授權環境使用獲准代表性快照，報告只保存聚合量測、manifest hash 與政策證據。
- `small` 為 500 掛牌、每日 1,250 文件、10 人、2 RPS，供 Compose pilot 驗收；`baseline` 為 2,000 掛牌、5,000 文件、50 人、持續 10／突發 50 RPS，是 Kubernetes 正式硬承諾；`stretch` 為 8,000 掛牌、20,000 文件與四倍流量，只驗安全降級及擴充性。Compose dev 無容量承諾。
- Benchmark 涵蓋冷啟動、穩態日終、文件／API／複寫併發、低優先回補、rolling deployment、pod／node／AZ、PostgreSQL switchover、物件故障與 backlog recovery；每個情境都驗資料及政策正確性。
- Manifest 包含台美一般日、半日市、DST、臨時休市、文件尖峰及排程重疊；可控時鐘只重現 deadline，不壓縮工作量或依賴，且使用版本化交易日曆。
- 完整 baseline 至少連續成功三次，以最差一次判 deadline／正確性並報告 median／p95／p99／變異；API 穩態至少 30 分鐘後加 burst，關鍵階段變異超過 15% 必須調查。
- Baseline 最差日終須於 T+105 完成、保留 15 分鐘事故餘裕，推論＋歸因最差不超過 10 分鐘；關鍵資源 p95 不超過核准容量 70%，不得 OOM、swap thrash 或持續 throttling。
- API p95≤500 ms、p99≤1.5 秒、錯誤率低於 0.1%；每掛牌具三期間結果或機器可讀不可用原因，manifest、checksum、機率、服務指派、政策與 audit 100% 正確，任何錯誤發布即整次失敗。
- 每個部署成品跑 Compose E2E、API、migration、probe及縮小日終 smoke；影響 worker／資料／模型／資源者跑完整 baseline，模型／特徵變更加七年訓練及八季回測，資料／平台變更加故障 restore；完整容量＋DR 至少每季。
- 容量報告記錄 node／CPU／GPU hours、database／object／telemetry 成長、backup與 egress；相同負載成本或資源需求回歸超過 20% 需明確核准，不虛構尚未提供的絕對預算。
- 不可變容量報告綁定部署成品、容量輪廓、benchmark manifest、政策、硬體／節點／儲存、autoscaling、量測、失敗、成本及核准，進 canonical governance ledger 保存至少七年；dashboard／CI log 只是 projection。

### Grilling round 8

使用者以「全部採建議」確認：

- 核心 repo 擁有 Compose、Helm、values schema、可攜 overlays、NetworkPolicy、RBAC、dashboard及驗收工具；雲商帳號／網路／control plane／受管 DB／object／KMS 由獨立 provider adapter／IaC module建立，核心不引用雲商專屬身分或名稱。
- 每個部署成品記錄已驗證的 Kubernetes 當期與前一 minor及實測 distribution／operator；升級先跑 conformance、render、server-side dry-run、admission、snapshot與故障測試，矩陣外版本不宣稱正式支援。
- Windows／macOS Docker Desktop只支援 Compose dev；Compose pilot使用受支援 Linux 主機、Compose v2、UTC、加密磁碟、獨立備份、私有 TLS及資源限制，雖接受單機停機但不省略治理控制。
- 安裝前分別驗證 PostgreSQL 與 SeaweedFS 所需 StorageClass 的加密、RWO、snapshot／restore、expansion、zone binding、IOPS及故障語意；chart 只接受已核准 class，不因 CSI 名稱推定能力。
- Compose／Kubernetes 共用 typed configuration schema；成品保存非敏感預設，環境 overlay只提供受控差異與 secret references。正式禁止 `.env` secret、未知欄位、fallback及未解析 placeholder，遮罩後 effective config digest進稽核。
- 一般變更只經簽章成品與受保護 pipeline；每小時偵測 IaC drift。緊急變更綁 SEV1／2、雙人核准、完整 audit與期限，事後回寫成品或撤銷。
- 上線前至少指定 platform、data、model、source、security owner並完成部署／回退、DB／object restore、來源、刪除、容量、OIDC／secret、模型回退及 DR runbook；小團隊可兼任，但高風險決策仍維持職責分離。
- 每個 release 在 Compose及至少一個 conformant Kubernetes 跑 provider contracts；每年以第二種 object／secret adapter及另一 Kubernetes distribution完成 restore＋E2E，只有不改 application module便可替換才宣稱可攜。
- 容量報告產生 namespace ResourceQuota、LimitRange、PriorityClass與 autoscaling 上限；缺 request／memory／ephemeral limit即 render失敗，各 namespace使用獨立預算且 application保留日終容量。

### Grilling round 9

使用者以「全部採建議」確認：

- 首次安裝依序驗部署成品／平台能力、隔離／身分／secret／網路、PostgreSQL／object／backup、migration、audit／telemetry、Dagster／relay／API、來源政策／資格，再跑 provider／restore／capacity smoke；來源排程預設停用並逐一核准開啟。
- 七年歷史回填只在 backfill-training pool，以來源／市場／期間 manifest、checkpoint、涵蓋及政策分段；daily-critical容量獨立，回填可在保護視窗暫停及冪等續跑，不略過時間點、更正、公司行動或權利。
- 全量回填驗證後，以正式容量連續完成至少五個合格台美 EOD shadow cycles，每次通過 T+105、API SLO、三期間完整性、通知、audit及無洩漏；另要求近期 restore、SEV2 drill、容量報告、安全 assessment及 platform＋data＋model共同核准。
- Pilot 到正式只提升相同已簽部署成品及政策允許、內容定址且核准的 artifact；不複製 volume、測試身分／secret或未審查資料。正式資料與信任根重建，模型重驗權利、checksum、schema、reproduction及服務資格。
- 切換先開內部唯讀研究，再開來源擷取／projection，再跑 shadow正式批次，最後開正式發布／通知；每階段有 smoke、觀察及回退，使用單一部署世代並禁止兩環境發布同一市場批次。
- 未授權來源保持 disabled／policy-blocked，平台可用合成或已獲准資料啟動；若它是版本化資料選擇條件的必要來源，對應資料集、特徵或預測明確阻斷，不得偷用測試 key、爬蟲或未授權替代。
- 營運交接包含拓撲／資料流、部署成品 inventory、支援矩陣、容量報告、復原集合、backup／DR、來源／secret到期、dashboard／alert／runbook、owner／escalation、限制與風險；接手人演練部署、回退、來源故障及預測證據追查。
- 退役先停排程／發布／匯出、撤銷全部 identity、計算保存／刪除期限、移交允許 archive及金鑰、刪除 online／replica／backup／cache並驗刪除證明，最後才移除 DNS、網路、叢集與 volume。
- 最終部署契約需包含 Compose／Kubernetes拓撲、程序／持久卷／網路／身分矩陣、資源擴縮、備份復原、rolling migration、容量 benchmark、安裝切換退役runbook及 acceptance scenarios，並以 ADR記錄 Compose完整但非 HA、Kubernetes單區 HA＋異地 DR的取捨。

## Answer

共有理解由使用者明確確認。系統採 `compose-dev`、`compose-pilot`、`k8s-production` 三種部署輪廓；Compose 使用相同 application image 與完整治理路徑，但只有 Linux pilot承諾 `small` 容量且接受單機停機，正式 Kubernetes 才承諾 `baseline` 容量、單區三個 failure domains 的 HA 與異地災難復原。Application module 維持程序內深 interface，不按 module 拆微服務；API／BFF、Dagster、三類 Job pool、relay、PostgreSQL、object storage與 observability 各依責任部署。

Kubernetes 以 `edge`、`application`、`orchestration`、`data`、`observability` namespace分離營運責任，採 default-deny network、獨立工作負載身分、三節點 CloudNativePG、跨域 SeaweedFS參考 overlay及可替換受管 adapter。API、relay與關鍵 adapter可水平擴充；日終使用保留 quota、Guaranteed QoS及保護視窗，backfill／training可 checkpoint／重建且不能擠壓正式批次。部署成品、設定、工作 attempt及正式批次均以 digest pin版本，rolling deployment配合 canary、role-specific probes及 expand／contract migration。

區域內已提交資料目標 `RPO≈0`；區域災難時 application PostgreSQL `RPO≤15分鐘`、物件／artifact `RPO≤24小時`、完整研究能力 `RTO≤4小時`。版本化復原集合把database target、object replication watermark、deletion-ledger sequence與deployment digests綁定；restore後先重播政策性刪除、驗reference graph／audit／checksum，再依唯讀研究、擷取、排程及正式發布順序開放。Failover／failback均需SEV1、雙人核准、地域來源資格及單一部署世代，禁止跨區雙主。

容量承諾由不可變容量報告證明：`small`為500掛牌／每日1,250文件／2 RPS，`baseline`為2,000掛牌／每日5,000文件／50使用者／10持續及50突發RPS，`stretch`為四倍負載。Baseline連續三次最差須T+105完成、推論＋歸因不超過10分鐘、REST p95≤500 ms／p99≤1.5秒，且所有manifest、機率、服務指派、政策及audit正確。正式上線另需七年回填驗證、五次台美EOD shadow、近期restore／SEV2演練、安全評估及多人核准。

- Design contract: [`docs/design/deployment-topology-capacity-and-recovery.md`](../../../docs/design/deployment-topology-capacity-and-recovery.md)
- ADR: [`docs/adr/0014-compose-complete-kubernetes-single-region-ha.md`](../../../docs/adr/0014-compose-complete-kubernetes-single-region-ha.md)
