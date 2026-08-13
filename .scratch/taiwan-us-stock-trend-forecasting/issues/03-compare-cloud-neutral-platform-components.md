# 比較雲端中立的資料與 MLOps 元件

Type: research
Status: resolved

## Question

依官方文件比較可在單機 Docker Compose 與 Kubernetes 運行的排程／資產編排、物件儲存、關聯式 serving store、模型登錄、實驗追蹤、資料品質、漂移監控、指標／日誌／追蹤元件。輸出各候選方案的授權、Windows 開發相容性、部署依賴、可替換介面與首版營運負擔，不直接替架構票券做最終選擇。

## Comments

- 已於繪圖階段指派研究代理；研究只能引用官方文件、專案原始碼與正式規格。

## Answer

已依官方文件、正式授權頁、官方 repo 與開放規格完成六類雲端中立元件比較，涵蓋授權、Compose／Kubernetes、Windows 開發、外部依賴、營運負擔、替換 seam、MVP 候選集合與決選前驗證項目；結論刻意保留為候選及可驗證門檻，不代替後續 ADR 做最終選型。研究也標示 MinIO 社群版封存／source-only 與 CockroachDB 現行授權等時效性風險。

詳見 [雲端中立資料與 MLOps 元件比較](../../../docs/research/cloud-neutral-data-mlops-components.md)。
