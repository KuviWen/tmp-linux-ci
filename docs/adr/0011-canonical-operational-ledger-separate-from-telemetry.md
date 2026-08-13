# 權威營運帳本與 telemetry 分離

來源健康評估、SLO 結果、品質／漂移檢查、營運事故及通知投遞以應用 PostgreSQL 的版本化營運帳本作權威真相，Prometheus、Alertmanager、Grafana、logs、traces 及品質工具 UI 只作短期診斷、視覺化與可重建 projection；應用只經 OTLP、OpenMetrics、結構化 logs 及 W3C Trace Context 等標準 seam 輸出。這增加健康／事故狀態機、七年精簡結果保存及 projection reconciliation 的實作成本，但避免 telemetry retention、工具替換、告警重複或監控後端故障破壞預測追溯與處置證據，也讓高容量 metrics／logs／traces 採較短保存期而不犧牲治理紀錄。
