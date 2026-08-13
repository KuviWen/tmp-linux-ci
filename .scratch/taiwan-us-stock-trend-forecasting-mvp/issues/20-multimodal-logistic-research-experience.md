# 20 — Multimodal logistic 到完整研究介面

**What to build:** 將合格價量、FinancialFact、台美與國際總體 vintage，以及可用或明確受阻的文件／新聞模態，形成 100＋100 immutable FeatureSnapshots 與 multimodal logistic 候選；以價量及逐模態消融完成相同治理與 shadow，並在完整繁中研究介面展示預測、支援、影響因素、證據、vintage、回測及譜系。

**Blocked by:** 14 — 台灣總體 vintage 研究路徑, 15 — 美國總體 vintage 研究路徑, 16 — 國際機構預測 vintage 研究路徑, 17 — 台灣授權新聞或明確阻斷路徑, 18 — 美國授權新聞或明確阻斷路徑, 19 — 雙語文件情報、sandbox 與 review 路徑

**Trace IDs:** `P3-TRACE-MODEL-01`, `GATE-MODEL-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] 版本化 100＋100 manifest 依產業、規模、流動性、掛牌年齡、股別／ADR、報告制度、文件密度及 full／degraded／unavailable／policy-blocked 支援分層。
- [ ] FeatureFactory 由固定 DataSelection 產生共同 feature schema，provider-native 欄位不外洩；每個模態保存 dataset／processing／policy version、age、coverage、quality 及 availability。
- [ ] 文件有效空集合只在所有預期來源均檢查且 CoverageReport 完整時成立；uncovered、late、policy blocked 與 processing failure 維持不同語意。
- [ ] ForecastLab 訓練 price-only、各單模態及完整 multimodal logistic，全部使用相同 folds、labels、calibration、three seeds、cost scenarios、artifacts、hard gates 與人工核准。
- [ ] 完整候選只有通過全部 gates 與五次 shadow 才可取代價量 baseline；未改善時保留 baseline assignment 並保存 ablation／GateDecision 供研究。
- [ ] 100＋100 正式／shadow EOD 對每掛牌三期間產生機率或明確不可用原因，optional modality 降級不改寫信心定義，必要價量不足時不產生機率。
- [ ] 繁中比較矩陣支援股票搜尋、市場／支援／主導類別／信心篩選與排序；標的頁顯示三期間結果、主要影響因素、允許文件證據、fundamentals、macro vintages、歷史、backtest 及 lineage。
- [ ] URL、opaque cursor、ETag、projection version、鍵盤操作、文字狀態、窄螢幕與移除色彩後可理解性通過外部驗收。
- [ ] EOD T+120、REST p95／p99、evidence projection、document quality、support、model gate 與 incident 狀態能由 canonical OperationsControl 與 research projection 一致驗證。
