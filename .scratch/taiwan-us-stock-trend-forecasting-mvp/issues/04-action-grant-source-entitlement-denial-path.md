# 04 — 行動權限與來源使用資格交集拒絕路徑

**What to build:** 使用相同本機 SecurityContext 與行動權限，分別搭配有效及無效來源使用資格，讓 fixture 擷取、日終 workflow、預測發布、研究 projection、REST／UI 與安全稽核都由同一 AuthorizationPolicy 得到一致 allow／deny 結果；任何單一工具或管理角色都不能繞過來源政策。

**Blocked by:** 01 — 台股 fixture 完整日終研究路徑

**Trace IDs:** `P1-TRACE-AUTH-01`

Status: ready-for-agent

- [ ] Loopback local／development API key 只能建立具 owner、expiry、scope 與 environment 的可信 SecurityContext；非 loopback 或正式設定會啟動失敗。
- [ ] 兩個 fixture 來源具版本化來源政策、資料保護類別與來源使用資格，且 allow 決策同時要求有效身分、行動權限、來源使用資格、用途、環境與資源狀態。
- [ ] 相同行動權限配合 active entitlement 時，擷取、FeatureSnapshot、fixture 預測、projection 與授權允許的研究查詢能完整成功。
- [ ] 將 entitlement 改為 suspended／expired／revoked 或移除指定用途後，新的擷取、推論、projection 及展示均在一致位置 fail closed，且不回傳受限 payload。
- [ ] REST 對 caller 只回穩定 problem code 與 correlation ID；受控 audit 保存真實 decision、reason、policy／grant／entitlement version 與 trace。
- [ ] Dagster、CLI、REST handler、資料庫角色或 platform-admin 身分都不能產生與 AuthorizationPolicy 相反的 allow 結果。
- [ ] 端到端 decision-matrix 測試同時涵蓋有權限無資格、有資格無權限、未知政策、到期資格與正常 allow，並驗證 workflow、REST、projection 與 audit 一致。
