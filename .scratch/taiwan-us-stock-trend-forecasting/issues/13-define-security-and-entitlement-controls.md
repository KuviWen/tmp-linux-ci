# 定義安全、憑證、授權與保存控制

Type: grilling
Status: resolved
Blocked by: 01, 02, 05, 11

## Question

本機 API key、正式 OIDC、角色、secret provider、來源 entitlement、原文保存限制、七年保留、刪除例外、稽核紀錄與供應鏈安全如何落到核心介面，使部署者能替換身分與 secret 後端而不繞過來源授權政策？

## Answer

共有理解由使用者明確確認。每個部署為單一 tenant，開發、測試與正式環境各自形成信任邊界；互動式使用者採 OIDC Authorization Code＋PKCE、AAL2 與高風險操作 WebAuthn step-up，正式工作負載使用短效 workload identity，本機 API key 只准 loopback 開發。Break-glass 必須有期限、綁定事故、雙人覆核且不得繞過來源政策。

所有入口先建立可信 `SecurityContext`，再由單一 `AuthorizationPolicy` module 將行動權限、來源使用資格、來源政策、用途、環境與資料保護類別取交集並 fail closed。IdP、reverse proxy、Dagster、資料庫角色與外部 policy engine 都只能是 adapter 或防禦縱深。角色區分讀者、研究、資料／模型操作、模型核准、來源 steward、安全／平台管理與稽核；模型升版、來源資格變更、保存例外、break-glass 與高風險匯出均實施職責分離及雙人控制。

`IdentityVerifier`、`AuthorizationPolicy`、`SecretProvider`、`RetentionControl` 與 `SecurityAudit` 是可替換但不可繞過的深介面。Secret 不進 repo、映像、資料庫或 telemetry；正式環境使用外部 secret／KMS adapter、短效取得、版本化輪替及撤銷。來源資格以狀態機、用途、環境、允許資料類別、有效期及契約證據管理，過期或不明即停止新擷取、新訓練、新推論與匯出。

只有獲准保存及模型使用的正式輸入才能進七年時間點證據鏈。可刪的來源原文、衍生物與受影響模型置於可精確刪除的加密儲存；政策性刪除優先於一般保存期限，沿 reference graph 停止使用、刪除並在備份還原時重播。Tombstone、影響評估、核准、刪除證明與不含被禁止內容的 append-only 安全稽核繼續保存，避免 WORM 把來源內容鎖過最晚刪除期限。

安全稽核記錄身份、政策版本、來源資格、資源、決策、理由、關聯 ID 及完整性鏈，不記 secret 或受限 payload；所有拒絕、管理操作、核准、匯出、刪除與 break-glass 都可追溯。供應鏈採依 digest 鎖定、SBOM、簽章與 provenance、CVE／secret／IaC 掃描、隔離建置及部署前驗證；同時納入 least privilege、網路分區、SSRF／webhook egress 控制、訓練資料污染與模型成品驗證、事故回應、例外到期及退役銷毀。

- Design contract: [`docs/design/security-identity-entitlement-and-retention.md`](../../../docs/design/security-identity-entitlement-and-retention.md)
- ADR: [`docs/adr/0002-licensed-point-in-time-data-retention.md`](../../../docs/adr/0002-licensed-point-in-time-data-retention.md)
- ADR: [`docs/adr/0012-action-grants-and-source-entitlements-intersect.md`](../../../docs/adr/0012-action-grants-and-source-entitlements-intersect.md)
- ADR: [`docs/adr/0013-separate-deletable-source-content-from-immutable-governance-evidence.md`](../../../docs/adr/0013-separate-deletable-source-content-from-immutable-governance-evidence.md)
