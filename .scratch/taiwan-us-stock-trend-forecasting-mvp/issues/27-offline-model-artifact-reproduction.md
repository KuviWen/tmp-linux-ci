# 27 — 無網路 ModelArtifact 重現路徑

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 在隔離、無網路、核准的 reproduction runtime 中，從正式候選 manifests 重建資料選擇、樣本 membership、標籤、normalizer、primary-seed ModelArtifact 與 evaluation，將差異報告與通過／否決結果寫入 canonical lifecycle 並可由治理介面查驗。

**Blocked by:** 26 — 受限 HPO 到正式三-seed 候選

**Trace IDs:** `P4-TRACE-REPRO-01`, `GATE-MODEL-01`, `GATE-SEC-01`

Status: ready-for-agent

- [ ] ReproductionRun 固定 signed image digest、dependency lock、hardware／precision profile、code SHA、source／dataset／feature／label／fold／processing／policy manifests 及 primary seed。
- [ ] Runtime 無網路、無 source／user secrets，不下載 code／model，不解析 latest 或 registry alias，只讀內容定址且 checksum 合格的明確 inputs。
- [ ] 重建的 sample membership、labels、fold exclusions、normalizer、feature schema、calibrators、artifact checksum 與 evaluation metrics 逐項產生差異報告。
- [ ] 相同 CPU environment 的逐筆機率最大差 `<=1e-6`；核准 mixed-precision GPU 路徑容差 `<=1e-4`，其他差異或缺件直接否決。
- [ ] Artifact 只使用 safetensors／ONNX、JSON、Parquet 等安全資料格式；pickle、joblib、remote code、callable checkpoint、load hook、unsigned／corrupt artifact 在載入前拒絕。
- [ ] Reproduction success／failure、runtime、hardware、checksums、diffs、trace、incident 及 GateDecision 進 canonical ledger，MLflow／registry 只作可補建 projection。
- [ ] 治理 REST／UI 能從候選查看 reproduction status 與安全的差異摘要，不暴露原始受限資料、object URI 或 runtime secrets。
- [ ] 任何 source-policy invalidation、retrospective contamination、current-only history、schema mismatch 或 artifact corruption 都使候選無法核准／shadow／promotion。
