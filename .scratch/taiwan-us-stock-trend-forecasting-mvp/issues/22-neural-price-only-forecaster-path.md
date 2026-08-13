# 22 — Neural price-only TrendForecaster 路徑

**What to build:** 在既有 TrendForecaster 深 seam 後建立第一個 neural price-only implementation，使用與 logistic 完全相同的 immutable FeatureBatch、回測、校準、artifact、shadow 與研究／營運契約，證明神經模型可以在不改 caller 或正式資料語意的情況下端到端運行。

**Blocked by:** 21 — 發布 P3 多模態研究 pilot acceptance bundle

**Trace IDs:** `P4-ENTRY-01`, `P4-TRACE-NEURAL-01`

Status: ready-for-agent

- [ ] Neural price-only adapter 只透過 TrendForecaster train／predict 行為被 ForecastLab 與 ForecastExecution 使用，TCN、optimizer、checkpoint 及 framework 型別不外洩。
- [ ] 價量輸入使用核准 253-session causal receptive field、market-specific training-fold normalization、mask／age／quality 與 point-in-time lineage，不含 ticker／名稱或 nominal raw price。
- [ ] 240 以上有效 sessions 為 full、60–239 為 degraded、少於 60 或 anchor 缺價為 unavailable；不可用結果不產生機率。
- [ ] TrainingIntent 固定 feature／label／fold／stock-pool／calendar／adjustment／policy／cost／seed／code／runtime manifests，使用三個預先登錄 seeds 且不挑 seed。
- [ ] ModelArtifact 使用安全資料格式，內容定址且離線可載入，綁定 architecture、weights、feature schema、normalizer、heads、calibrators、manifests、runtime、seed 與 provenance。
- [ ] Neural price-only 與 logistic 在相同八季 folds、六 cells、calibration、cost 及 support slices 上比較，結果進 immutable EvaluationReport 與 research governance view。
- [ ] Shadow EOD 對 100＋100 產生與 logistic 相同 shape 的 result-or-reason、REST／UI、health、latency 與 audit evidence，請求順序及批次組成不改變結果。
- [ ] 此票不引入 fundamentals、macro、documents 或 fusion；失敗／未改善只保存候選證據，不變更 production assignment。
