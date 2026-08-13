# 24 — 凍結多語文件表示 neural 路徑

**What to build:** 將 DocumentIntelligence 產生的授權、凍結、版本化繁中／英文 segment embeddings 與事件／市場影響特徵，經決定性挑選後加入 neural candidate；TrendForecaster 不讀原文、不微調大型 encoder，shadow 預測及研究頁能回到實際文件片段與政策證據。

**Blocked by:** 22 — Neural price-only TrendForecaster 路徑

**Trace IDs:** `P4-TRACE-TEXT-01`, `P4-TRACE-NEURAL-01`

Status: ready-for-agent

- [ ] DocumentIntelligence 使用核准、凍結、版本化多語 encoder／tokenizer／pooling 產生 segment embeddings，保存 license、training cutoff、bundle、checksum 及 source-policy lineage。
- [ ] 每掛牌只選 cutoff 前 20 sessions 內、confirmed subject 或明確 market／sector scope、權利合格且處理完成的最多 64 個 segments。
- [ ] Segment selection 先按 DocumentCluster 去重，再依版本化 source authority、processing quality、role 與 recency 決定性排序，不使用未來 labels 或測試 relevance。
- [ ] TrendForecaster request 只收到 embeddings、event／impact features、source、role、age、quality、availability 與 evidence pointers，不接收原文、URL fetch 或 encoder update 能力。
- [ ] 沒有新聞只有在所有預期來源均完整檢查時是有效空集合；partial、late、policy blocked、processing failure 分別產生不同 support masks。
- [ ] Text increment 使用與 neural price-only 相同 folds、seeds、calibrators、cost、artifact、ablation、hard gates 及 shadow workflow，並與 price-only／非文字增量比較。
- [ ] 研究頁能從文件相關預測支援與模型影響回到允許展示的 Segment、DocumentVersion、ProcessingBundleVersion、SourcePolicyVersion，不把 gate reliance 當成因果歸因。
- [ ] Encoder／tokenizer 不計入 15M forecast trainable-parameter 上限，但其版本、授權與 artifact 完整進入 ModelArtifact／PredictionRecord lineage。
