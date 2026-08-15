# 零付費認證來源與程式內憑證管理

## Status

Accepted

## Context

ADR 0017 將 P2 之後的資料來源限制為免帳號、免 API key、免申請的官方公開資料。這可消除採購依賴，卻也排除了 Alpaca、Tiingo 等具備免費自助方案、完整技術介面及來源憑證的候選，使「需要 key」本身成為 ticket 交付 blocker。匿名官方來源又只能提供美股契約的局部證據，無法單獨涵蓋 EOD、公司行動、掛牌生命週期與交易日曆。

來源存取、來源使用資格與系統行動權限是三個不同問題。API key、secret 或 token 只能證明資料提供者接受某個呼叫者；它不能證明保存、備份、建模、衍生、內部展示或再散布的權利，也不能授予人員操作系統的權限。若把三者合併，程式不是會錯誤放行資料，就是會因尚未設定一把可自助取得的 key 而無法交付可測的垂直切片。

## Decision

P2 與後續 phases 可以使用官方或非官方提供者的零付費方案。方案必須能由符合其主體分類的部署者自助註冊，不要求付款方式、付費試用、sales 接洽、人工核准、採購流程或協商契約；dataset-specific 條款必須明示允許本專案實際需要的保存、備份、轉換、建模、衍生與內部研究展示。`$0` 或技術上可呼叫不會自動建立來源使用資格。

每個資料集以版本化來源使用依據獨立資格審查，記錄提供者、方案、distribution、條款／license 與雜湊、部署主體分類、允許用途、顯名、保存／刪除限制及有效期間。多個來源可以組成版本化 source bundle，但每個成員保留自己的政策、譜系、涵蓋與缺口；組合不能擴張任何成員的權利，也不能用一個來源的資格替代另一個來源。

來源憑證與 `SourcePolicyVersion`、來源使用資格及 `ActionGrant` 分開管理。缺少、無效、到期或撤銷的憑證形成可查詢的 `credential_required`／`policy_blocked` 就緒結果，阻止該來源的網路呼叫與新的下游使用，但不阻止 adapter、REST、UI、工作流程及 fail-closed acceptance 的工程交付。只有真實符合方案分類的帳號、合格來源使用依據及有效憑證同時存在時，才可把 live source path 宣稱為正式合格。

應用程式提供來源憑證的清單、寫入、輪替、撤銷及驗證操作。secret value 是 write-only，只能交給 `SecretProvider`；資料庫、repository、typed configuration、work command、artifact、REST response、outbox、log 與 trace 只保存不敏感的 reference、狀態、版本、時間與 audit evidence。必要 Compose 輪廓使用本機開源 secret provider，不要求外部 KMS。

「重新申請」由程式顯示版本化、提供者擁有的官方註冊或 key-management URL，並在使用者完成提供者流程後接受新的 write-only credential。程式不冒充提供者、不自動處理 CAPTCHA、email／MFA 或條款接受；只有提供者正式提供帳號或 key 發行 API，且另有合格政策與安全設計時，才可新增自動發行 adapter。

付費資料、付費試用、付款方式、借用／共享／測試憑證、爬蟲、人工下載、sales／人工 approval、採購及協商契約仍不在必要交付邊界。條款撤回、部署主體不符合方案分類或用途證據不足時一律 fail closed；不得以 fixture、mock credential 或技術成功宣稱正式來源資格。

## Consequences

Ticket 07 可以交付完整的認證來源 adapter 與憑證管理垂直切片，即使部署者尚未輸入真實 key；此時外部可觀察結果必須是 `credential_required`，而不是假的 live 成功。提供真實且用途合格的零付費帳號後，同一公共 seam 可執行 opt-in live contract 驗證並建立正式來源證據。

來源資格模型比 ADR 0017 更寬，也增加方案條款、帳號分類、credential rotation 與 provider URL 變更的治理負擔。它換取的是不把可自助解決的 authentication 當成產品 blocker，同時維持零採購、秘密不外洩與來源權利 fail-closed。

此決定部分取代 ADR 0017 的免帳號、免 API key、免個別申請限制；ADR 0017 的零付費、本機／開源必要輪廓、實得歷史、不可偽造證據與缺口明示原則仍有效。ADR 0012 的 `ActionGrant` 與來源使用資格交集、ADR 0013 的內容／治理證據分離及 ADR 0016 的時間點歷史證據仍有效。
