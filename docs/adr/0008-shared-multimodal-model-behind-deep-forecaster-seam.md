# 共享多模態模型置於深 TrendForecaster seam 後

首版以一個共享台美多模態模型結合市場別正規化、小型 adapter、預測期間別 gated fusion、分類 head 與市場別校準器，並只透過深 `TrendForecaster` interface 暴露訓練與推論；大型文字 encoder 預先計算且凍結，內部 TCN／MLP／gate 不成為呼叫端 seam。這放棄台美各自最大化容量與端到端文字微調，以換取樣本效率、授權隔離、15M／CPU SLA 算力護欄、可替換 implementation 及離線 ModelArtifact 復現；只有跨八季、三 seeds 的預先登錄證據與人工核准才能把分市場模型提升為新候選架構。
