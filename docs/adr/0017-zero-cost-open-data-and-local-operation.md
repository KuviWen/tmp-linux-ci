# 零成本公開資料與本機運行是產品邊界

本專案只供個人與內部團體研究使用。自 P2 起，必要資料來源必須是官方機關或交易場所逐一明示的公開資料集／官方文件，且正式路徑不得要求付費、帳號、API key、申請文件、另行書面契約或部署者取得外部 entitlement。公開資料使用依據以版本化 `SourcePolicyVersion` 保存資料集 ID、distribution、公開條款／license、條款內容雜湊、顯名文字、允許用途與有效期間；使用公開條款本身不被描述成無條件，但它不形成個別申請或採購依賴。`ActionGrant` 仍控制誰可執行系統操作，公開資料政策則控制該資料可否被保存、轉換、建模、備份與內部展示。P2 行情路徑以 `retain_observed_history` 表示保存實際取得歷史的用途，不得以 `retain_7_years` 作為接觸來源、治理或查詢的硬閘門；可用深度由不可變歷史證據另行判定。

當官方零成本資料沒有足夠歷史、公司行動、身分生命週期、revision 或涵蓋時，系統只保存從首次取得日起的不可變 platform-observed history，並將缺口呈現為 `unavailable`、`degraded` 或明確的 qualification failure；不得改用爬蟲、互動頁、人工下載、測試帳號、商業資料或虛構證據。回測仍固定標籤、交易日端點、purge、embargo 與 once-only tests，但訓練期間及可形成的 folds 由實際已驗證歷史決定並綁入 `TrainingIntent`；統計、類別或校準支援不足時不建立正式模型，不能用固定七年承諾掩蓋不存在的資料。

官方文件是文字模態的完整產品範圍；付費新聞、公司級 consensus 與其他商業內容不構成缺漏或未解除 gate。模型與部署必須可用本機／自管的開源元件完成，不要求付費 API、雲端服務、外部 OIDC／KMS、商業簽章、獨立滲透測試或跨區基礎設施。容量、股票池、可用性與復原承諾以實際零成本硬體及合格來源可重現的量測為準，不再把 600＋1,400、2,000 listings、三 failure domains 或 regional failover 當固定產品門檻。

這個決定讓產品範圍受官方免費來源與本機資源約束，也可能需要長期累積歷史後才能產生正式模型；換取的是所有後續 ticket 都能在零採購、零外部 entitlement 的邊界內完成，且不會以假的授權、歷史或營運證據換取綠燈。它取代 ADR 0002 的付費／契約來源與固定七年必要保存假設、ADR 0007 的固定七年訓練長度、ADR 0012 對每個來源皆需 principal entitlement 的假設、ADR 0014 的必備 Kubernetes／跨區運行輪廓，以及 ADR 0015 的固定 600＋1,400 擴張門檻；其餘 point-in-time、行動權限交集、purge／embargo、Compose 完整語意與 fail-closed 約束仍有效。
