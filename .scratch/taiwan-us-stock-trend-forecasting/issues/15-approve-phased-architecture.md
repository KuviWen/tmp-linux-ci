# 核准分階段架構並交付規格化

Type: grilling
Status: resolved
Blocked by: 07, 10, 11, 12, 13, 14

## Question

所有已解決決策能否形成一致的系統上下文、深模組、資料與模型譜系、部署拓撲及驗收邊界；哪些垂直 tracer bullet 應先證明台股與美股各一條端到端路徑，哪些能力延後但保留明確 seam，並確認地圖已足以交給 `/to-spec`？

## Comments

### Grilling round 1

使用者以「全部採建議」確認：

- 交付一份權威整體架構核准契約，附分期矩陣、垂直 tracer bullets、硬性 gates、延後能力及既有文件索引；後續 `/to-spec` 依可獨立驗收的垂直成果拆規格，不採一次實作的龐大規格。
- 採風險優先垂直切片；每條 tracer bullet至少穿過 source adapter、時間點證據、dataset／feature、baseline prediction、canonical ledger、REST／研究介面、health／audit，只建立該切片需要的 implementation。
- 首期同時包含台股與美股切片，共用發行人／證券／掛牌、交易日曆、時間點、PredictionRecord及 ResearchQuery interface；市場差異只能經 adapter、calendar、normalization或小型 model adapter表達。
- 首期使用 prior／logistic baseline adapter，先產生三分類機率、校準器、信心與資料支援；完整 neural、gated fusion及 SHAP延後，但從第一期固定 `TrendForecaster` interface。
- 每市場至少一個掛牌能在繁中最小比較矩陣／標的頁及 REST看到1／5／20日機率、信心、資料支援、資訊截止點及譜系ID；UI可簡化但不能整體延後。
- 合成／固定fixture只證明CI與module interface；正式來源模式另需有效政策、來源使用資格、首次取得時間、涵蓋報告及可追溯artifact。Fixture不得標為正式預測或解除授權阻斷。

### Grilling round 2

使用者以「全部採建議」確認：

- 台股首條正式路徑由manifest選一個XTAI普通股掛牌，當期行情／公司行動用明確OGDL的TWSE介面，七年歷史、symbol history及完整公司行動用獲准保存／建模的回填來源；回填權未取得前只有fixture路徑。
- 美股選一個主要交易所普通股，SEC／BLS／BEA提供申報及總體證據，價格、symbol history與公司行動必須使用部署者已簽約EOD adapter；未簽約時正式路徑`policy_blocked`，不得暗用免費網站行情。
- 首條正式預測只把時間點價量、交易日曆、公司行動及身分設為必要；基本面、總體、文件先明確`unavailable`，baseline可產生degraded-support，不用零值假裝模態存在。
- Fixture可用決定性stub驗證管線；正式baseline至少需要獲准七年時間點行情、成熟標籤、既定purge／embargo回測、prior／logistic比較、六個校準器及人工核准服務指派。
- 文件情報先接台灣OGDL MOPS重大訊息／月營收摘要，以及美國SEC 8-K／6-K／company facts，驗證文件版本、首次取得、標的連結、保存模式與政策顯示；新聞全文、廣泛IR及法人報告延後。
- 總體vintage先接台灣央行或主計總處的修訂序列，以及美國BLS／BEA核心series；保存每次release／取得版本，以cutoff情境證明後續修訂不改寫舊預測。
- 股票池依1＋1、10＋10、100＋100、台600＋美1,400擴張；每階段增加身分事件、退市／更名、半日市、文件量及缺資料情境，上一階段正確性、政策與容量gate通過才擴大。
- 第一條正式路徑就以未調整行情及版本化公司行動產生內部調整版本，涵蓋股利、分割、減資／反向分割及掛牌生命週期；必要事件不足時阻斷標籤／預測，不使用供應商最新adjusted close掩蓋譜系。

### Grilling round 3

使用者以「全部採建議」確認：

- 分五個實作階段：雙市場工程脊柱、正式價量baseline、多模態研究pilot、受治理神經模型、正式基準部署；來源契約／entitlement／採購是對應entry gate，不冒充軟體成果。
- 外部授權延遲時，fixture、公開來源adapter、module interface、UI、回測框架及部署工具可繼續；依賴來源的正式dataset、artifact、PredictionRecord及退出gate維持blocked，不以爬蟲藏風險。
- 第1階段即有可信SecurityContext、AuthorizationPolicy、SourceEntitlement、SecretProvider reference、append-only audit及資料保護類別；後期只增加正式adapter、HA、簽章、滲透與刪除／DR演練。
- 第1至3階段保持Compose完整運行；第2階段起持續做Helm／provider／Kubernetes smoke，第4階段建立完整production staging，第5階段通過baseline容量、HA及DR。
- Prior／logistic baseline永久保留為`TrendForecaster` adapter、對照及安全證據；neural只在八季回測、hard gates、人工核准與五次shadow後取得服務指派。
- UI由第1階段1＋1最小矩陣／標的頁，第2階段加入10＋10搜尋、歷史／譜系，第3階段完成資料支援、影響因素、證據、回測及無障礙MVP；卷宗、觀察清單、協作、列印／分享延後。
- 監控由第1階段canonical work／health／incident／audit、telemetry及最小dashboard，第2階段加入EOD SLO／retry／circuit／通知／品質，第3至4階段加入文件／漂移／模型／升版，第5階段完成HA／meta／容量／DR。
- 每階段以不可變acceptance bundle綁定程式／部署、來源政策、資料manifest、contracts、E2E、failure、容量及核准；下一階段只引用已通過bundle，不以簡報或截圖代替。
- 預測執行用途固定為`fixture`、`shadow`、`production`、`retrospective_replay`；fixture／shadow隔離且不進正式研究歷史，只有有效production指派與資料資格可建立正式PredictionRecord。
- 階段失敗不回寫舊bundle、dataset、model或decision；形成新blocked／failed證據並維持上一核准階段，修復以新版本驗收。只有安全／政策／完整性事件沿譜系撤銷既有資格。

### Grilling round 4

使用者以「全部採建議」確認：

- 第1階段只實作切片需要的`DataSupply`、`FeatureFactory`、`ForecastExecution`、`ModelGovernance`、`ResearchQuery`、`OperationsControl`深行為；`DocumentIntelligence`／`ForecastLab`延後，不建空殼、pass-through或永久not-implemented module。
- 每市場fixture含穩定發行人／證券／掛牌、ticker有效期、交易日曆、至少253個未調整交易日、公司行動、來源政策、收據、首次取得、正常／遲到版本，以及可選缺失、重複、修訂與cutoff情境。
- Unit tests可用in-memory adapter；Compose E2E必須用真實PostgreSQL、Dagster、transactional outbox及本機filesystem `ObjectRepository`。SeaweedFS、MLflow與完整telemetry延至第2階段。
- `FixtureTrendForecaster` test adapter只從固定FeatureBatch產生決定性機率／信心／支援；其ModelArtifact不可升版且只能由fixture服務指派引用，第2階段才加入可訓練prior／logistic。
- Local API key形成SecurityContext，兩fixture來源有明確policy／entitlement；REST、工作及projection都經相同AuthorizationPolicy，並測行動權限與來源資格兩軸拒絕。正式OIDC延至第2階段。
- 最小UI有兩列掛牌、三期間機率／信心／支援、cutoff及fixture標章；標的頁列FeatureSnapshot、ModelArtifact、服務指派、dataset及raw evidence ID，URL可重載且fixture不進production路由／匯出。
- Dagster只呼叫普通workflow；canonical state、outbox與audit同PostgreSQL交易提交，relay冪等建projection。測dataset commit後、outbox delivery前crash並驗恢復不重複／不遺失。
- 失敗矩陣至少涵蓋重複擷取、late data、公司行動／日曆缺失、entitlement拒絕、必要／可選模態缺失、checksum、stale fencing、outbox重送、fixture升版企圖及單市場失敗，並產生穩定Outcome／health／incident／audit。
- 第1階段bundle保存deployment／fixture／migration digests、provider／module／REST contracts、雙市場E2E IDs、failure、UI／REST golden、restart、resource smoke及核准；只證明工程脊柱，不授予正式資格。
- 乾淨環境以單一Compose命令及單一acceptance runner執行雙市場EOD、REST／UI、故障、重啟與bundle；無module間HTTP、未追蹤`latest`或手改DB，Windows Docker Desktop與Linux CI都通過。

### Grilling round 5

使用者以「全部採建議」確認：

- `first_observed_at`保留真實平台取得時間；另建綁定來源archive、版本／修訂、發布證據、契約及可信度的歷史可得性主張。只有可證明as-of／完整修訂語意者可供正式歷史重建；current-final extract只作隔離研究，正式每日預測仍只用first-observed。
- 台股驗TWSE OGDL當期adapter及契約歷史adapter；美股驗一個含EOD、公司行動、symbol history及七年權利的商業adapter。共同通過Collector／Decoder、checkpoint、rate／policy及reference graph contracts，供應商SDK型別不進`DataSupply` interface。
- 10＋10代表性manifest涵蓋普通股、股別／ADR、ticker變更、公司行動、暫停／半日市及訓練史中的下市標的；正式預測只含當期合格掛牌，不以存活大型股挑樣本。
- 正式workflow依市場收盤啟動，T+90解析一次DataSelection，再建FeatureSnapshot、pin assignment、推論三期間並交易發布PredictionRecord／projection／outbox，T+120前完成；late內容只進後續或replay。
- `ForecastLab`首版實作class-prior與regularized multinomial logistic兩個`TrendForecaster` adapter，共用FeatureBatch、七年窗、六calibrator、八季walk-forward、三seeds、成本情境及自包含artifact，不含neural／文字／gate／HPO。
- 首個logistic使用版本化BootstrapGatePolicy，至少勝過class-prior 1 percentage point並通過絕對校準、穩定、涵蓋、重現、安全、營運gate；免除最佳logistic／incumbent比較。首個指派後恢復完整GatePolicy，若無法勝prior則正式服務blocked。
- 七年匯入先產生backfill qualification report，驗session、listing生命週期、未調整價、公司行動、adjustment、端點、修訂、政策及歷史可得性；不合格樣本明列排除，不補值或使用目前adjusted close。
- 第2階段加入SeaweedFS、正式OIDC、正式SecretProvider測試adapter、OTel／Prometheus／Alertmanager、SMTP／webhook sandbox、MLflow projection及持續Helm／Kubernetes smoke，Compose仍為主要交付輪廓。
- 10＋10正式路徑同時驗來源六維健康、coverage、schema／integrity、retry／circuit、quarantine／recovery、deadline事故、通知dead-letter、clock及REST SLO；每掛牌有三期間結果或機器原因。
- 退出需兩市場各五次合格EOD shadow，10＋10通過historical qualification、BootstrapGatePolicy、人工核准、重現、回退、資格、政策顯示及故障恢復，Compose small-smoke與Kubernetes provider smoke通過後才可建立首個production指派／PredictionRecord。

### Grilling round 6

使用者以「全部採建議」確認：

- 文件主路徑為台灣OGDL MOPS重大訊息／月營收／財報摘要及美國SEC 8-K／6-K／10-Q／10-K／company facts；歷史文件需歷史可得性主張，無版本證據的MOPS快照只從實際觀測日起正式合格。
- 官方公告不等同一般新聞。第3階段建contract-required新聞adapter與四種內容政策；完整產品切換前台美各至少一個具保存、建模、embedding／情緒、展示、刪除權的新聞來源。未取得時只可標`official-documents-only`，不可宣稱新聞整合完成。
- 台美來源欄位先形成共同FinancialFact及版本化feature schema，保存concept／period／unit／currency／dimensions／filing／availability／revision；原生欄只在Decoder，FeatureFactory推導共同基本面。
- 總體硬需求為台灣央行＋主計總處、美國BLS＋BEA及一個權利核准的OECD Economic Outlook dataflow；FRED只allowlist，IMF usage terms未確認前維持legal hold。
- `DocumentPipeline.process`隱藏文件版本／呈現／片段、標準文本、安全解析、去重、confirmed標的連結、taxonomy、事件、多語embedding及三期間市場影響；parser／linker／classifier／embedder／impact皆為內部adapter。
- 首次真實文件即使用無secret／無網路／有限資源sandbox；拒絕巨集、外部資源、archive bomb及不安全反序列化，單件parser crash只隔離該物件並產生營運／audit證據。
- 最小review queue只建立版本化confirm／reject、merge／split、事件裁決及抽取失敗決定，帶操作者、guideline、理由與證據並產生新衍生dataset；不得改原文、annotation或預測。
- 新模態先形成multimodal logistic候選，對價量only、單模態與完整組合做ablation，仍走完整gate／重現／核准／五次shadow；未改善則不取代價量baseline，但證據仍可研究展示。
- 100＋100manifest依產業、規模、流動性、掛牌年齡、ADR／股別、財報制度、文件密度及缺失分層，兩市場均含full／degraded／unavailable／policy-blocked，橫斷面rank只用當時股票池。
- 中英文golden corpus要求結構化fact exact match≥99.5%、confirmed link precision≥99%、taxonomy macro-F1≥0.80且ECE≤0.10、事件macro-F1≥0.75；可abstain且另報coverage，錯公司／證據／政策為hard failure。
- 研究介面完成100＋100搜尋／篩選／排序、三期間預測／信心／支援、影響因素、允許文件片段、基本面／總體vintage、歷史／回測／譜系及繁中無障礙。
- 退出需台美文件／基本面／總體正式dataset及coverage、100＋100五次EOD shadow、T+120／REST SLO、文件品質／sandbox／刪除／修訂／裁決／ablation全通過；新聞未授權則bundle保留正式產品blocker。

### Grilling round 7

使用者以「全部採建議」確認：

- `NeuralTrendForecaster`先以price-only通過與logistic相同contracts，再依基本面、總體、文件加入encoder，最後加入quality-aware gate與完整組合；每步有ablation／golden parity，不一次除錯全網路。
- DocumentIntelligence以授權、凍結、版本化多語encoder產生最多64個segment embeddings；TrendForecaster不讀原文或微調encoder。Encoder／tokenizer／cutoff／license／bundle進譜系但不計15M參數。
- 正式TrainingIntent在隔離Job固定image、lock、hardware／precision、seeds與manifest；Notebook只提研究設定。Gate前在無網路核准runtime重建primary seed、樣本、標籤、normalizer及評估，超容差即否決。
- ModelArtifact／服務指派明列核准股票池及support profile；第4階段只核准100＋100，額外掛牌僅隔離OOD／shadow，第5階段須對台600／美1,400重新訓練、校準、回測與核准。
- HPO只用前9月validation、最多30 trials，測試季度不可見；選定config後另建intent，以三seeds從頭訓練，trial checkpoint永不可核准／指派。
- Integrated Gradients重跑須在容差內、文件貢獻100%回到DocumentVersion／Segment、遮罩模態貢獻為零、completeness相對誤差median≤5%／p95≤10%，推論＋歸因仍在CPU 10分鐘內。
- Gate collapse不強迫均勻；依市場×期間×支援保存依賴分布，連續collapse觸發營運調查／消融，修復只形成新候選，不能在線改gate。
- 排程先manual rebuild，再啟用月full；連續兩次成功後啟用週增量，下一季度才啟用HPO，仍受readiness、資源、完整gate及人工核准。
- 連續兩週漂移才建`drift_early` intent；來源問題先修資料，漂移不直接promotion／rollback，只有可立即驗證serving失敗可依規則自動回退。
- 正式ModelArtifact只用safetensors／ONNX、JSON、Parquet等資料格式，禁止pickle／joblib／remote code／load hook；digest、SBOM、provenance、signature、cold-load、惡意artifact及政策影響皆為hard gate。
- 新聞授權後到會形成新policy、processing bundle、feature schema、qualification及intent，重跑文件golden、七年回測、ablation、gate與shadow，不把embedding插入現行模型或沿用舊calibrator。
- 退出需100＋100 shared候選完成三seeds、八季回測、baselines／ablations、HPO、六calibrator、IG、無網路重現、hard gates、人工核准、五次shadow及promotion／rollback／stale／drift／policy情境；候選未勝出時治理仍成立且logistic留任。

### Grilling round 8

使用者以「全部採建議」確認：

- 2,000掛牌逐一通過身分、日曆、symbol history、公司行動、session、來源資格及coverage；60–239 sessions可degraded，低於60／anchor缺價則unavailable並保留機器原因，不偷移除。
- 完整產品資料gate需台美EOD／公司行動／symbol history、台美合格新聞、台美申報／基本面、台美總體vintage、至少一個國際機構forecast及一個具歷史vintage／建模權的公司級法人consensus來源；缺商業類別只可受限pilot，不宣稱完整整合。
- 可選模態不要求每日每股都有內容，但每個預期partition有CoverageReport並區分有效空集合、不涵蓋、late、policy-blocked及processing failure；只有必要價量／身分／日曆／公司行動／現行模型／譜系不足阻止機率。
- Neural必須完成、評估及治理，但正式現行模型是通過全部gate的最佳合格成品；neural未證明改善時logistic繼續服務。
- Baseline gate使用台600／美1,400、每日5,000文件、七年資料、50人、REST 10持續／50突發RPS，三次最差T+105、推論歸因≤10分鐘、REST p95≤500 ms／p99≤1.5秒、關鍵資源p95≤70%，錯誤發布／繞權／遺失／OOM皆否決。
- HA gate在三failure-domain staging實際注入API／relay／Dagster／worker、pod／node／zone、PostgreSQL、PgBouncer及object故障並驗PDB、拓撲、lease／fencing、同步副本、repair及保留容量；replica數本身不是證據。
- Security gate要求OIDC AAL2／WebAuthn、workload identity、授權矩陣、secret輪替、default-deny、audit chain、policy deletion、signed artifacts、SBOM／provenance／CVE及獨立滲透測試，Critical／High未修即否決。
- DR gate完成月restore、季度PredictionRecord全鏈及region failover／failback，實測DB RPO≤15分鐘、object RPO≤24小時、RTO≤4小時；restore後重播刪除ledger並維持單一部署世代。
- 營運須演練來源rate／schema／coverage、object、outbox、invalid probability、model rollback、notification、clock、audit及policy deletion；SEV1／2 runbook、owner、escalation與meta synthetic由接手人操作。
- 切換依正式資料／信任根、唯讀研究、擷取／projection、五次台美EOD shadow、原子production指派／發布／通知前進，每步有觀察及回退。
- Go／No-Go由platform、data、model、source、security owner共同簽bundle；model approver、source steward及安全雙人控制各自不被多數票取代，任何hard-gate owner可veto。
- 最終bundle綁來源／契約、2,000 qualification、全譜系、現行／回退模型、shadow、容量、安全、restore／DR、SLO／事故、UI／REST無障礙及共同核准；交易、盤中、卷宗、協作、個人化仍不在MVP。

### Grilling round 9

使用者以「全部採建議」確認：

- MVP延後message broker、distributed SQL／query、lakehouse format、online feature store、vector DB、GPU inference、跨區active-active及module微服務；現有outbox、Parquet、ObjectRepository、batch snapshot及程序內module先由量測證明不足。
- 產品延後證據卷宗／PDF／郵件、觀察清單、協作、自訂版面、複雜情境、改模型輸入及公開匿名網站；MVP只有比較矩陣、標的頁、REST及內部營運／治理介面。
- 社群、論壇、任意IR及未授權報告不建通用爬蟲；registry可保存disabled／legal-hold與契約需求，沒有完整權利不實作正式Collector，社群情緒屬新治理專案。
- 模型延後分市場完整模型、Transformer取代TCN、端到端文字微調、seed ensemble、listing embedding、在線學習、LLM投資理由及自動架構搜尋；只能隔離研究，不擴`TrendForecaster` interface或繞gate。
- 只有實際兩個adapter才建立seam；保留已有真變體的storage／forecaster／identity／secret／notification seams，未有第二implementation的SemanticSearch／broker／public export不先建空interface、proxy或pass-through。
- 重開延後項目需量測／使用者研究證明現interface不足、至少兩個實際adapter、failure／policy、migration／rollback及架構評審；涉及資料所有權、遠端module、database或deployment另建ADR。
- 核心規格只固定portable Helm、Compose、typed config及provider contracts；供應商選定後另建IaC spec映射managed DB／object／KMS／OIDC／network，不讓雲商語意進application module。
- 自動下單、券商、個人投資組合／建議、盤中低延遲／高頻、多租戶SaaS、公開匿名及未授權資料是新產品／威脅模型或永久out-of-scope，不保證現seam直接承接。
- 新增ADR記錄雙市場垂直tracer bullets取代水平平台先行，以及歷史可得性主張支援具證據的建置前歷史重建但不改首次取得；BootstrapGatePolicy只更新模型生命週期契約。
- 未選來源供應商、絕對成本及雲商屬部署者輸入／採購blocker，已有allowlist、adapter contract、entry gate及fail-closed，不阻止規格化；實際配額、費用、region與IaC映射留待選定後決策。
- Deferred-capability register記理由、目前seam、重開證據及非目標；`/to-tickets`只為五階段建票券。外部授權／採購另列dependency owner及驗證證據，不用假程式票券代替。

### Grilling round 10

使用者以「全部採建議」確認：

- 新增一份精簡、權威的整體分期架構與規格交接文件；它整合並索引既有契約、ADR及研究，不複製其完整內容，也不建立第二套相互競爭的規則。
- `/to-spec`依五個實作階段各產生一份可獨立驗收的垂直規格；不另拆資料平台、模型平台、UI或監控等水平規格，以免再次形成大爆炸依賴。
- 每個entry、tracer bullet、exit及hard gate使用穩定trace ID，例如`P2-ENTRY-01`、`P3-TRACE-DOC-01`；trace matrix逐項連回權威設計契約、ADR、研究結論及後續規格驗收條件。
- 建立外部dependency register，明列台美市場資料、台美新聞及法人consensus契約的owner、所需權利、驗證證據、到期與blocked階段；技術文件只保留fail-closed disabled state及provider contract，不把採購寫成程式成果。
- 歷史證據分三級：`platform_observed`可用於正式每日預測及歷史訓練／回測；`archive_attested`只可用於正式歷史訓練／回測；`published_current_only`只能隔離研究。未知或自行宣稱一律blocked，且任何級別都不改寫`first_observed_at`，production cutoff只接受`platform_observed`。
- 一般`GatePolicy`與既定hard gates維持不變；`BootstrapGatePolicy`只處理首個正式logistic相對class-prior的啟動資格，首個production指派建立後永久停用，不成為日後候選繞過incumbent比較的捷徑。
- 五份規格直接繼承已核准的安全、時間點、容量、SLO、回測、譜系、無障礙及失敗語意，不在規格化階段重新開放數值或語意選擇；變更須走明確change control。
- 決策地圖Fog移除「研究UI原型仍待決」等已解決敘述；保留真正未決的來源契約限制、量測後硬體／成本、雲商選定後IaC映射。商業資料授權列為產品階段blocker，而非架構不確定性。
- 後續票券先建立每階段tracer bullet票券，再建立對應exit-bundle票券；獨立工作可平行，但正式釋出必須受前一bundle與外部dependency gate阻斷，不用水平票券暗示可繞過順序。
- Change control區分adapter-only變更與核心語意變更：前者在既有interface內新增資格與contract測試即可；後者若影響時間點、身分、PredictionRecord、服務指派、gate、資料所有權或部署所有權，必須更新權威契約、ADR及trace matrix。
- 本票券只有在整體交接文件、兩份新ADR、時間點與模型生命週期修訂、主要文件交叉索引、Fog清理、trace／dependency／deferred matrices及一致性驗證全數完成後才可結案；不以功能程式或尚未取得的外部授權作完成宣稱。

## Answer

共有理解已成立，所有既有決策已形成一致且可交給`/to-spec`的五階段垂直架構：P1雙市場工程脊柱、P2正式價量baseline、P3多模態研究pilot、P4受治理神經模型、P5正式基準部署。權威交接文件是[分階段架構核准與規格化交接契約](../../../docs/design/phased-architecture-and-spec-handoff.md)；它包含穩定entry／tracer／exit／hard-gate trace IDs、外部dependency register、acceptance bundle、deferred register、change control及五份垂直規格的交接順序。

時間點契約新增`platform_observed`、`archive_attested`、`published_current_only`三級正式語意與`historical_reconstruction`模式，不改寫`first_observed_at`；模型生命週期新增首個正式logistic專用且首個production指派後永久停用的`BootstrapGatePolicyVersion`。上述難以逆轉的取捨分別記錄於[ADR-0015](../../../docs/adr/0015-dual-market-vertical-tracer-bullets.md)及[ADR-0016](../../../docs/adr/0016-attested-history-without-rewriting-observation.md)。

所有主要設計文件已建立反向索引，決策地圖已移除研究介面範圍的陳舊Fog。尚未選定的商業來源、實際費用／配額、量測後硬體與雲商IaC映射是有owner及fail-closed行為的產品／部署輸入，不是架構不確定性，也不宣稱外部授權已取得。文件連結、Markdown表格、ADR編號、五階段trace覆蓋及兩項跨契約修訂已通過靜態一致性驗證。
