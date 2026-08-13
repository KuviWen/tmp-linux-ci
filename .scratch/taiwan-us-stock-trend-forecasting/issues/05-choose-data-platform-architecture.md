# 選擇資料擷取、編排與儲存架構

Type: grilling
Status: resolved
Blocked by: 01, 02, 03, 04

## Question

在單機可運行、雲端中立、七年證據鏈、來源排程與錯誤重試的限制下，哪些元件分別擁有不可變原始資料、標準化資料、特徵、metadata、serving query、任務狀態與事件告警；它們之間透過哪些小型介面交換資料？

## Answer

完整決策見 [資料擷取、編排與儲存平台架構](../../../docs/design/data-platform-architecture.md)。

平台採模組化單體：Dagster OSS 只擁有排程與執行投影，PostgreSQL 保存身分、來源政策、checkpoint、資料集版本、manifest、品質、事件、模型核准及 serving read model 的權威狀態；原始內容、正規化大量資料、特徵、回測與模型 artifact 以不可變物件及 Parquet 保存。Docker Compose 預設 SeaweedFS S3 gateway，並透過 ObjectRepository provider contract 支援本機檔案與外部 S3-compatible endpoint。

DatasetCatalog 以 staging／verify／publish 狀態機協調 PostgreSQL 與物件儲存；DatasetReader 只能讀指定 manifest，FeatureBuilder 產生不可變特徵快照。PostgreSQL outbox 以 at-least-once 與冪等消費投遞事件；日常關鍵、維護、回補／訓練及 API 使用隔離資源。首版不引入訊息代理、lakehouse、分散式 SQL／查詢叢集或線上 feature store。
