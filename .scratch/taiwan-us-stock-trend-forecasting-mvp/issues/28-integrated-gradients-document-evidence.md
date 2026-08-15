# 28 — Integrated Gradients 到文件證據研究路徑

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 為正式共享候選產生每個期間前五項正向與負向 Integrated Gradients 主要影響因素，依模態、具名特徵及時間桶聚合並把文字貢獻映射回授權文件片段；結果隨 shadow 預測進入 REST／繁中研究頁、權威譜系與營運 SLA，而不宣稱因果。

**Blocked by:** 26 — 受限 HPO 到正式三-seed 候選

**Trace IDs:** `P4-TRACE-ATTR-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] TrendForecaster predict 對每個可用 listing × horizon 回傳前五正向與前五負向 grouped attributions，並以 feature／modality／time bucket／evidence pointer 表示。
- [ ] Text attributions 只能映射到實際進入 FeatureSnapshot 的 Segment、DocumentVersion、ProcessingBundleVersion 與允許展示的 SourcePolicyVersion。
- [ ] 被 mask、unavailable 或 policy-blocked 的模態 attribution 為零；有效空集合、缺失與受阻不被混為同一說明。
- [ ] 相同 ModelArtifact／FeatureBatch 的 Integrated Gradients 重跑在核准容差內一致，completeness relative error median `<=5%`、p95 `<=10%`。
- [ ] Gate reliance 另列為模態依賴 metadata，不被當成主要影響因素、因果、準確率或交易理由；研究介面明示 attribution 的非因果限制。
- [ ] PredictionRecord 與 research projection 保存 attribution schema／method／baseline／artifact／feature／evidence versions，既有預測不因方法升級被重算。
- [ ] 完整 measured support pool 的 CPU EOD inference 加 attribution 最差執行不超過 10 分鐘；若本機容量無法滿足，縮小並重版 support profile，timeout／partial attribution 產生穩定結果狀態及營運證據，不發布無法驗證的混合結果。
- [ ] REST／繁中標的頁可從每項文字／結構特徵影響定位到允許證據或具名 feature group，並以鍵盤與文字狀態完整理解。
