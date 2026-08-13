# 雲端中立的資料與 MLOps 元件比較

查證日期：2026-08-13

## 問題與比較方法

本文件比較能支援「生產導向 MVP」的自託管元件，但不替後續架構票券做最終選型。比較基準是：

- 本機能否以 Docker Compose 形成可重現的完整開發環境；
- 正式環境能否部署於不綁定特定雲商的 Kubernetes；
- Windows 開發者是否有受支援的原生路徑，或至少有清楚的 Docker Desktop／WSL2 路徑；
- 授權、外部狀態服務、升級及備份責任是否適合小型團隊；
- 能否用穩定協定或應用層 port 隔離供應商，避免資料、模型及監控後端滲入領域邏輯。

「營運負擔」是本研究的相對評級，不是效能測試：低表示單一或無常駐服務；中表示需要資料庫、worker 或數個具狀態服務；高表示要營運分散式狀態、operator、額外訊息系統或複雜升級程序。所有正式部署仍需要備份、還原演練、TLS、密鑰、容量及升級 runbook。

## 摘要

| 類別 | 適合帶入 MVP 決策的候選 | 需要先解決的門檻 |
| --- | --- | --- |
| 資產／排程編排 | Dagster、Prefect；Airflow 作成熟但較重的對照 | 資產模型與一般 flow/DAG 模型的取捨；Windows 是否必須原生執行 |
| 物件儲存 | SeaweedFS；外部 S3 相容服務 adapter | S3 行為契約測試、備份與七年保存容量；MinIO 目前的封存／授權風險 |
| 關聯式 serving store | PostgreSQL；CockroachDB 作分散式 SQL 對照 | 單區 HA 是否足夠；是否接受 CockroachDB 授權、license key 與 telemetry 條件 |
| 模型登錄／實驗追蹤 | MLflow；Kubeflow Hub 作 registry-only 對照 | 是否需要同一產品同時負責 tracking 與 registry；人工升版核准紀錄放在哪個權威來源 |
| 資料品質／漂移 | GX Core、Evidently | 規則式資料契約與統計漂移應分開；兩者輸出需正規化成系統事件 |
| metrics／logs／traces | OpenTelemetry Collector + Prometheus；logs 比較 Loki/OpenSearch；traces 比較 Tempo/Jaeger | AGPL 接受度、物件儲存／搜尋叢集成本、Windows log agent 路徑及保存期 |

兩項時效性風險不能沿用舊印象：MinIO 社群 server repo 已於 2026-04-25 封存、改成只散布原始碼，Operator 亦已封存；CockroachDB 自 24.3.0 起改受 CockroachDB Software License，而非一般寬鬆開源授權。[MinIO server repo](https://github.com/minio/minio)、[MinIO Operator repo](https://github.com/minio/operator)、[CockroachDB licensing FAQ](https://www.cockroachlabs.com/docs/stable/licensing-faqs)

## 資產與排程編排

| 候選 | 授權與部署 | Windows 開發 | 外部依賴與營運負擔 | MVP 評估位置 |
| --- | --- | --- | --- | --- |
| Dagster OSS | Apache-2.0。官方文件同時提供 Docker Compose 與 Kubernetes Helm；Compose 範例包含 webserver、daemon、code location、PostgreSQL，並以 Docker socket 啟動個別 run。[repo／授權](https://github.com/dagster-io/dagster)、[Compose](https://docs.dagster.io/deployment/oss/deployment-options/docker)、[Helm](https://github.com/dagster-io/dagster/tree/master/helm/dagster) | Python 套件可在本機開發，但本輪官方部署文件沒有像 Prefect 一樣宣告完整原生 Windows 支援。以 Docker Desktop Linux containers 作為共同驗收路徑較穩妥；Docker socket 與 bind mount 必須在 Windows CI 實測。 | PostgreSQL、webserver、daemon、每個 code location 的 image；Compose 透過 Docker socket 執行工作，正式環境則多一套 Helm／run launcher 設定。負擔：中。 | 資產、partition、materialization 與 lineage 是一級概念，和「來源 → 原始資料 → 標準化資料 → 特徵 → 模型 → 預測紀錄」相符；應驗證跨台美市場的 partition/backfill 與資訊截止點表達能力。 |
| Prefect OSS | Apache-2.0。官方 Compose 包含 PostgreSQL、Redis、server、background services 與 worker；官方 Helm 可安裝 self-hosted server 及 worker。[repo／授權](https://github.com/PrefectHQ/prefect)、[Compose](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose)、[Helm](https://docs.prefect.io/v3/advanced/server-helm) | 官方文件宣告 first-class Windows／PowerShell 支援，process 與 Docker workers 都有 Windows 路徑。[Windows 指南](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-windows) | 正式資料庫為 PostgreSQL；Redis 在擴展 messaging 時建議使用；server/background services/worker 分開。負擔：低至中。 | Python flow/task 對既有模型程式侵入較小，Windows 友善；應驗證資產譜系、分區資料 freshness 與 backfill 的可觀測性是否足夠。 |
| Apache Airflow | Apache-2.0。官方有 Docker image、Compose quick-start 與由 Airflow 社群維護的 Helm chart；正式部署需自行管理 metadata DB、客製 image、監控及資源調校。[repo／授權](https://github.com/apache/airflow)、[安裝責任](https://airflow.apache.org/docs/apache-airflow/stable/installation/index.html)、[Helm](https://airflow.apache.org/docs/helm-chart/stable/index.html) | 官方明確指出只支援 POSIX 正式環境；Windows 要用 WSL2 或 Linux containers。[prerequisites](https://airflow.apache.org/docs/apache-airflow/stable/installation/prerequisites.html) | 依 executor 需要 scheduler、API/web、dag processor、triggerer、workers、PostgreSQL/MySQL，Celery 路徑再加 Redis。負擔：高。 | provider 與 DAG 生態成熟，但對 MVP 的服務數量、image 客製及 Windows 摩擦最大；適合在已有 Airflow 營運能力時進入決選。 |

### 編排替換 seam

應用程式不直接匯入 orchestrator context。每個工作只呼叫可在 CLI／測試獨立執行的 use case，例如 `ingest_source(cutoff)`、`build_feature_snapshot(snapshot_id)`、`train_candidate(manifest_id)`、`evaluate_candidate(model_version)`。編排 adapter 只負責：schedule、retry、concurrency、partition、run metadata 與 event emission。跨產品只交換明確的 ID／URI，不交換 Dagster asset object、Prefect future 或 Airflow XCom payload。

可用同一組驗收情境比較三者：收盤後排程、來源連續失敗兩次告警、20 交易日隔離期回測、指定股票池 backfill、單一 partition 重跑不覆寫既有版本，以及 worker 被終止後的 idempotent retry。

## 物件儲存

| 候選 | 授權與部署 | Windows 開發 | 外部依賴與營運負擔 | MVP 評估位置 |
| --- | --- | --- | --- | --- |
| SeaweedFS | Apache-2.0；提供 S3 gateway。官方 repo 內有 Compose 範例與 Kubernetes Helm chart／operator。[repo／授權](https://github.com/seaweedfs/seaweedfs)、[Compose 範例](https://github.com/seaweedfs/seaweedfs/blob/master/docker/compose/local-dev-compose.yml)、[Helm values](https://github.com/seaweedfs/seaweedfs/blob/master/k8s/charts/seaweedfs/values.yaml) | 官方 release notes 持續記錄 Windows-specific 修正，表示原生 code path 仍在維護；團隊仍宜用與正式環境相同的 Linux container 做整合測試，以免 FUSE／路徑差異滲入結果。[releases](https://github.com/seaweedfs/seaweedfs/releases) | 完整拓撲包含 master、volume、filer、S3/IAM 等元件；單機可簡化，HA 與修復仍需理解多種角色。負擔：中至高。 | 授權寬鬆且專案仍有活躍 release，可作自託管 S3 候選；必須用實際 SDK 對 multipart、range read、conditional write、object versioning／immutability、checksum 與 presigned URL 做契約測試，不能只憑「S3-compatible」字樣。 |
| MinIO Community | Server 與 Operator 是 AGPL-3.0。server repo 已封存並改為 source-only distribution；歷史 binaries 不再維護，Operator repo 也已封存。[server 狀態與授權](https://github.com/minio/minio)、[Operator 狀態與授權](https://github.com/minio/operator) | 舊版官方文件有 Windows container／client 路徑，但現況的社群 server 要自行從 source 建置 image。[Windows/container 文件](https://min.io/docs/minio/container/index.html) | 單節點操作曾很簡單，但現在要承擔 source build、安全修補、封存 operator、AGPL 合規或商業 AIStor 採購。負擔：MVP 中、長期高。 | 只能作「接受 AGPL 且願意自維」或「採購商業版」候選；不應再把舊版預編譯 image／operator 當成無條件的社群基線。 |

### 物件替換 seam

建立 `ObjectRepository` port，只暴露 `put/get/head/list/delete`、checksum、content type、版本／保留 metadata 與 presigned URL；領域資料以不可變 key（例如內容雜湊或版本 ID）定位。adapter 可以是本機檔案系統、S3 API 或未來雲商物件儲存。由契約測試定義本系統實際依賴的 S3 子集，而不是假設不同 S3-compatible 實作完全等價。

七年證據鏈的 authoritative manifest 應獨立於 storage console：每個物件記錄來源、事件時間、首次取得時間、內容雜湊、大小、媒體型別、加密 key ID 與 retention class。這是架構需要保留的 seam，不代表本研究已選定物件儲存產品。

## 關聯式 serving store

| 候選 | 授權與部署 | Windows 開發 | 外部依賴與營運負擔 | MVP 評估位置 |
| --- | --- | --- | --- | --- |
| PostgreSQL | PostgreSQL License（類 BSD/MIT 的寬鬆授權）。Docker Official Image 有 Compose 範例；Kubernetes 可加 Apache-2.0 的 CloudNativePG operator。[授權](https://www.postgresql.org/about/licence/)、[Docker image](https://github.com/docker-library/docs/blob/master/postgres/README.md)、[CloudNativePG](https://github.com/cloudnative-pg/cloudnative-pg) | PostgreSQL 官方下載頁列出 Windows installer；Docker Desktop 也可與正式 schema 保持一致。[Windows](https://www.postgresql.org/download/windows/) | Compose 可單節點；Kubernetes HA 需 operator、PVC、備份儲存、連線池及故障切換演練。負擔：低至中。 | SQL、JSONB、交易與廣泛 driver 足以承載預測紀錄、版本 metadata、事件及 dashboard read model；應以實際查詢和保留量測試分區、索引與備援，不預先假設需要分散式 SQL。 |
| CockroachDB | 新版受 CockroachDB Software License；免費資格、license key、年度續期與 telemetry／throttling 條件需逐案審核。官方有 container 與 Kubernetes operator/Helm；官方 releases archive 亦列出 Windows binaries。[授權 FAQ](https://www.cockroachlabs.com/docs/stable/licensing-faqs)、[Kubernetes](https://www.cockroachlabs.com/docs/stable/deploy-cockroachdb-with-kubernetes)、[downloads archive](https://www.cockroachlabs.com/docs/releases/downloads-archive) | releases archive 可取得 Windows binary，也可用 Docker Desktop；採用前應再確認擬鎖定版本仍提供 Windows artifact。正式分散式行為仍應在 Linux/Kubernetes 多節點測試。 | 正式叢集、憑證、節點／區域放置、升級與 license 管理均比單節點 PostgreSQL 多；官方 K8s 指南以多節點為主。負擔：高。 | 只有當跨區存活、水平 SQL 或 PostgreSQL 單主瓶頸有證據時才值得把複雜度帶入決選；現行授權條件本身是架構門檻。 |

### serving store 替換 seam

API／dashboard 只透過 repository/query service 讀寫；schema migration 使用標準 SQL 優先，將資料庫特有功能限制在 adapter。`PredictionRecord`、模型核准、來源健康與事件表使用應用產生的 UUID／內容雜湊，不依賴資料庫自增 ID。對 PostgreSQL wire compatibility 不等於 SQL 行為完全相同，兩個 adapter 必須跑同一套 migration、transaction isolation 與查詢效能測試。

## 模型登錄與實驗追蹤

| 候選 | 授權與部署 | Windows 開發 | 外部依賴與營運負擔 | MVP 評估位置 |
| --- | --- | --- | --- | --- |
| MLflow OSS | Apache-2.0。官方 self-hosting repo 提供 MLflow + PostgreSQL + MinIO 的 Compose；Model Registry 要求 database-backed store，artifact 另放檔案或 S3-compatible store。[repo／授權](https://github.com/mlflow/mlflow)、[self-hosting](https://mlflow.org/docs/latest/self-hosting/index.html)、[backend store](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/)、[artifact store](https://mlflow.org/docs/latest/self-hosting/architecture/artifact-store/) | Python client/server 可本機使用；特定 `--dev` CLI 模式明確不支援 Windows，因此共同路徑仍宜是 container。[CLI](https://www.mlflow.org/docs/latest/api_reference/cli.html) | MVP 至少一個 tracking server；團隊／registry 路徑再加 PostgreSQL 與 object store。DB schema upgrade 要在 server 啟動前處理。負擔：中。 | 同時覆蓋 run、metric、artifact、model version，最接近完整需求；但人工核准、升版閘門與資料 manifest 仍要由本系統保存，不能把任一可變 stage/alias 當唯一稽核來源。 |
| Kubeflow Hub（原 Model Registry） | Apache-2.0；目前仍標示 alpha。repo 同時提供 pre-built/local Compose（MySQL 或 PostgreSQL）與 Kustomize/Kubernetes 安裝。[repo／Compose／授權／alpha](https://github.com/kubeflow/hub)、[Kubernetes 安裝](https://www.kubeflow.org/docs/components/hub/installation/) | Compose 可在 Docker Desktop/WSL2 評估；完整 Kubernetes 整合需要 Kustomize，選配 Istio、CSI/KServe 會擴大依賴。 | registry server、UI、MySQL/PostgreSQL；若加入 Kubeflow dashboard、Istio、CSI、KServe，服務面顯著擴張。負擔：中至高。 | 它是模型 metadata registry，不是等價的完整 experiment tracker；可作「registry 與 tracking 分拆」候選，但 alpha 狀態及額外 tracker 是明確成本。 |

### MLOps 替換 seam

定義 `ExperimentTracker`（start/end run、param、metric、artifact link）與 `ModelRegistry`（register candidate、attach evaluation、request approval、promote、resolve current）兩個 port，不把兩者綁成單一供應商 API。每個候選模型同時產生不可變的 canonical manifest，至少含資料清單雜湊、特徵綱要版本、模型 artifact URI/checksum、設定雜湊、Git SHA、套件鎖定檔、資訊截止點與回測結果 URI。

依既有 ADR，`promote` 只能在本系統已記錄升版閘門結果與人工核准後執行；registry 的 alias/stage 是 projection，不是權威核准紀錄。這個邊界也讓 MLflow、Kubeflow Hub 或未來產品可以被替換，而不改變「候選模型／現行模型」語意。

## 資料品質與漂移

| 候選 | 授權與部署 | Windows 開發 | 外部依賴與營運負擔 | MVP 評估位置 |
| --- | --- | --- | --- | --- |
| GX Core | Apache-2.0 的 Python library；支援 Python 3.10–3.13，可驗證 dataframe、filesystem 與 SQL data source。[repo／授權](https://github.com/great-expectations/great_expectations)、[安裝](https://docs.greatexpectations.io/docs/core/set_up_a_gx_environment/install_gx/)、[SQL sources](https://docs.greatexpectations.io/docs/core/connect_to_data/sql_data/) | 無常駐平台需求；可在 Windows Python 或統一的 worker container 執行。官方未以作業系統矩陣承諾 Windows，應由本 repo 的 Windows CI 驗證。 | 核心是 library；validation result／Data Docs 的持久化與排程由系統和 orchestrator 負責。負擔：低。 | 適合 schema、null、範圍、唯一性、row count、時間完整性及來源-specific contract；不是本需求 PSI/ECE/模型績效漂移的完整替代品。 |
| Evidently OSS | Apache-2.0 的 Python library，涵蓋資料品質、data/prediction drift 與模型評估；可選 self-host UI。workspace 可放 filesystem、SQL 或 S3-compatible storage。[repo／授權](https://github.com/evidentlyai/evidently)、[drift](https://docs.evidentlyai.com/metrics/preset_data_drift)、[self-hosting](https://docs.evidentlyai.com/docs/setup/self-hosting) | `pip`/Conda library 可本機跑；UI 可 containerize。官方沒有在本輪來源提供 production Kubernetes operator，因此 K8s 應視為一般 Deployment/Job，由本系統負責 manifests。 | 純批次 report 幾乎無常駐依賴；使用 UI 時再加 workspace storage。負擔：低至中。 | 適合 PSI／distribution drift、預測分布、成熟標籤績效及 pass/fail report；預設 drift 方法會依樣本及型別變動，需求中的 PSI 0.20 等門檻必須顯式設定，不可直接採自動預設。[方法說明](https://docs.evidentlyai.com/metrics/explainer_drift) |

### 品質與漂移替換 seam

GX 與 Evidently 可互補，但核心系統只接收正規化 `QualityCheckResult`／`DriftCheckResult`：`check_id`、scope、window、observed、threshold、status、evidence_uri、tool/version、created_at。告警判斷（例如「連續兩次」）放在本系統規則層，不放在工具 UI。如此可逐步用 SQL、Python 或其他 library 取代個別 check，歷史事件仍可比較。

## Metrics、logs 與 traces

### 共同收集層

OpenTelemetry Collector 是 Apache-2.0、vendor-neutral 的 receiver/processor/exporter；官方提供 Docker、Compose、Kubernetes 與 Windows binary 安裝路徑。[repo／授權](https://github.com/open-telemetry/opentelemetry-collector)、[Docker/Compose](https://opentelemetry.io/docs/collector/install/docker/)、[安裝矩陣](https://opentelemetry.io/docs/collector/install/)

應用只輸出 OTLP metrics/logs/traces、Prometheus/OpenMetrics endpoint 及結構化 stdout，不直接呼叫 Loki、Tempo、Jaeger 或 OpenSearch SDK。跨服務 trace propagation 使用 W3C Trace Context。這些是正式規格 seam：[OTLP specification](https://opentelemetry.io/docs/specs/otlp/)、[OpenMetrics 1.0](https://prometheus.io/docs/specs/om/open_metrics_spec/)、[W3C Trace Context](https://www.w3.org/TR/trace-context/)。

### Metrics

| 候選 | 授權與部署 | Windows／依賴 | 營運評估 |
| --- | --- | --- | --- |
| Prometheus + Alertmanager | Prometheus 為 Apache-2.0，官方提供 binaries 與 container；Kubernetes 可用 Apache-2.0 Prometheus Operator/kube-prometheus 管理。[repo／授權](https://github.com/prometheus/prometheus)、[Docker](https://prometheus.io/docs/prometheus/latest/installation/)、[Operator](https://github.com/prometheus-operator/prometheus-operator) | Windows 開發可用 Docker Desktop；Windows host 指標另有 windows_exporter。單機要保存 volume，K8s 要設定 PV、retention、rules 與 Alertmanager routes。[windows_exporter](https://github.com/prometheus-community/windows_exporter) | 對 MVP 可先單節點、短 retention，成本低至中；長期 HA／長 retention 通常要額外 remote-write backend，但它不應與七年研究證據鏈共用保存政策。 |

### Logs

| 候選 | 授權與部署 | Windows／依賴 | 營運評估 |
| --- | --- | --- | --- |
| Grafana Loki + Grafana/Alloy | Loki 與 Grafana 預設為 AGPL-3.0-only。官方有 Compose quickstart 與 Kubernetes 路徑；quickstart 會帶 Grafana、Alloy 與 object store 等服務。[Loki 授權](https://github.com/grafana/loki)、[Grafana 授權](https://github.com/grafana/grafana)、[Compose](https://grafana.com/docs/loki/latest/get-started/quick-start/quick-start/) | Windows 官方教程建議 WSL2；Loki Docker logging plugin 不支援 Windows，因此應以 OTLP/Alloy 或 stdout collector 為共同路徑。[Windows 教程](https://grafana.com/docs/loki/latest/get-started/quick-start/tutorial/)、[Docker driver 限制](https://grafana.com/docs/loki/latest/send-data/docker-driver/) | 單 binary 可低至中；分散式 mode、object store、retention/compactor 與 AGPL 審核提高負擔。標籤 cardinality 與敏感新聞／憑證遮罩需另定規則。 |
| OpenSearch + OpenSearch Dashboards | Apache-2.0。官方支援 Docker Compose、Helm/operator 與 Windows zip 安裝。[repo／授權](https://github.com/opensearch-project/OpenSearch)、[安裝方式](https://docs.opensearch.org/latest/install-and-configure/install-opensearch/index/)、[Windows](https://docs.opensearch.org/latest/install-and-configure/install-opensearch/windows/) | Docker/Windows 均可，但 JVM heap、`vm.max_map_count`、磁碟與索引生命週期是額外負擔；官方完整 Observability Stack 本機需求至少 8 GB RAM。[system settings](https://docs.opensearch.org/latest/install-and-configure/install-opensearch/index/)、[Observability Stack](https://observability.opensearch.org/docs/get-started/installation/) | 搜尋、logs 與 Jaeger trace storage 可整合，但 stateful search cluster 對 MVP 最重。若選它，需用查詢/保存需求證明成本，而非只因 dashboard 功能多。 |

### Traces

| 候選 | 授權與部署 | Windows／依賴 | 營運評估 |
| --- | --- | --- | --- |
| Jaeger v2 | Apache-2.0；all-in-one container 內含 collector、query 與 UI，接受 OTLP。Kubernetes 可用 OpenTelemetry Operator 或 Helm。[repo／授權與 quickstart](https://github.com/jaegertracing/jaeger)、[Kubernetes](https://www.jaegertracing.io/docs/2.20/deployment/kubernetes/) | Docker Desktop 可執行；本機 in-memory storage 重啟即遺失。正式持久化要 OpenSearch、Elasticsearch、Cassandra 或 remote storage API。[storage backends](https://www.jaegertracing.io/docs/2.20/storage/) | 開發環境低；正式加 search/storage 後中至高。自帶 UI，且 stable gRPC remote storage API 是清楚替換 seam。[remote storage API](https://www.jaegertracing.io/docs/2.20/architecture/apis/) |
| Grafana Tempo + Grafana | Tempo 為 AGPL-3.0-only。官方有 Docker Compose 與 Kubernetes Helm/operator；Tempo 以 Grafana 作查詢 UI。[repo／授權](https://github.com/grafana/tempo)、[Compose](https://grafana.com/docs/tempo/latest/docker-example/)、[Linux monolithic 部署](https://grafana.com/docs/tempo/latest/set-up-for-tracing/setup-tempo/deploy/locally/linux/)、[Kubernetes operator](https://grafana.com/docs/tempo/latest/set-up-for-tracing/setup-tempo/deploy/kubernetes/operator/) | Docker Desktop/WSL2 可開發；正式通常再需要 object store，operator 另依賴 cert-manager。當前不同部署範例對 Kafka/Redpanda 的需求不同，必須在選定版本與 mode 後以該版文件鎖定依賴。 | 單機中；分散式 object store／queue／Grafana 後中至高。若 logs 已採 Grafana stack，介面一致是優點；AGPL 與共用物件儲存容量是門檻。 |

### 監控替換 seam 與最低訊號

所有服務至少輸出：

- `run_id`、`source_id`、`market`、`stock_pool_id`、`information_cutoff`、`data_manifest_hash`、`model_version`；
- schedule delay、source freshness、retry count、rows/objects processed、quality failures；
- inference latency、prediction status、class probability distribution、low-confidence/data-unavailable count；
- training/backtest duration、mature-label macro-F1/ECE/Brier/rank IC、drift check status、promotion decision；
- trace/span ID 同時寫入 structured log，讓 metrics → trace → log 可關聯。

告警事件的權威紀錄仍是系統的結構化事件表；Prometheus Alertmanager、Grafana、OpenSearch 或 Evidently UI 只是傳送與視覺化 projection。這符合通用 webhook／SMTP 的需求，也避免替換監控後端時遺失「連續兩次」等決策狀態。

## 不做最終選擇的 MVP 決選集合

後續架構票券可從下列集合做實測，這不是選型結論：

1. **編排**：Dagster 與 Prefect 各做相同的收盤後 ingest/backfill/retry spike；Airflow 只在團隊已有其營運能力或 provider 需求時進入實作 spike。
2. **物件儲存**：以 `ObjectRepository` 契約同跑本機檔案與 SeaweedFS S3；若仍要 MinIO，先取得授權／維護策略書面決策。也可把部署者提供的外部 S3-compatible endpoint 當第三 adapter。
3. **serving store**：以 PostgreSQL 跑 schema、查詢與備援驗證；只有量測顯示需要分散式 SQL且接受授權條件時，再把 CockroachDB 納入 production 決選。
4. **MLOps**：MLflow 跑完整 tracking + registry spike；Kubeflow Hub 只用來驗證 registry/tracker 分拆是否值得 alpha／Kubernetes 成本。
5. **品質／漂移**：GX 與 Evidently 不必二選一；用共通 result schema 比較重疊規則，避免同一 check 重複執行。
6. **可觀測性**：先固定 OTel/OTLP、OpenMetrics、structured logs 與 W3C Trace Context。後端分別比較 Prometheus、Loki/OpenSearch、Jaeger/Tempo；是否同時部署 logs 與 traces 後端由 MVP 的除錯 SLO 決定。

## 架構票券應要求的實證

- 所有 image 以版本與 digest pin 住，不用 `latest`；產出 SBOM、授權清單與 CVE 掃描結果。
- Windows 11 + Docker Desktop 與 Linux CI 都能一個命令啟動、跑 smoke test、停止後再啟動且資料仍在。
- Kubernetes manifests 使用相同 application images/config contract；演練 worker、DB pod、object store node 與 telemetry backend 故障。
- 對 object store、SQL repository、registry/tracker、quality/drift result、OTLP exporter 跑 provider contract tests。
- 做一次完整備份／還原：從 prediction ID 找到 DB row、canonical manifest、feature/data/model artifacts、Git SHA 與核准紀錄。
- 量測 idle RAM/CPU、一次台美日終批次尖峰、保留容量及升級停機；以結果決定「低／中／高」營運負擔是否可接受。
- AGPL、CockroachDB Software License、任何商業 edition 及對外網路服務方式交由法務／採購確認；本研究只記錄官方授權狀態，不構成法律意見。
