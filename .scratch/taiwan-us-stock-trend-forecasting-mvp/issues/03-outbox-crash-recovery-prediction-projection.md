# 03 — Outbox crash 後完整恢復預測投影

**What to build:** 在台股 fixture 預測的權威 PostgreSQL 交易已提交、outbox 尚未投遞時注入故障；系統重啟後必須從相同事件身分恢復研究與營運投影，使 REST／UI 最終呈現一次且僅一次的結果，並保留完整 crash、retry 與 audit 證據。

**Blocked by:** 01 — 台股 fixture 完整日終研究路徑

**Trace IDs:** `P1-TRACE-OUTBOX-01`

Status: ready-for-agent

- [ ] 測試可在 PredictionRecord、核心權威狀態與 outbox 同交易提交後、任何 consumer effect 發生前確定性終止 relay／process。
- [ ] 重啟不需手動修改資料庫或重跑整個 EOD，relay 會以原 event ID、aggregate version 與 trace ID 重送事件。
- [ ] Consumer 在自己的交易中完成去重；重複投遞、consumer transaction crash 與 relay 再次重啟都不產生重複研究或營運投影。
- [ ] REST／UI 在投影未追上時明示 projection version／stale 狀態，恢復後顯示與權威 PredictionRecord 完全一致的結果。
- [ ] 權威 PredictionRecord、服務指派、FeatureSnapshot 及 audit 在 crash 前後保持不可變，不以刪除或覆寫補償。
- [ ] OperationsControl 留下 work attempt、outbox delivery、恢復與任何投影延遲證據，且相同根因不為每次 retry 建立互相競爭的事故。
- [ ] 端到端事故測試同時涵蓋 relay crash、consumer crash、重複事件與亂序 aggregate version，並驗證零遺失、零重複外部效果。
