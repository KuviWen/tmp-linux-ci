# 選擇多模態模型與訓練設計

Type: grilling
Status: resolved
Blocked by: 03, 06, 07

## Question

價量 TCN、基本面／總體 MLP、多語金融文本表示、gated fusion 與三個預測期間的多任務頭應採哪些輸入契約、缺失模態行為、校準方法、共享／市場別參數與可替換 seam，才能在首版算力限制下通過既定升版閘門？

## Comments

### Round 1 frontier

1. 首版是否採單一跨市場模型，或台美完全分離？建議共用模態編碼器與 fusion，以 market embedding、market-specific normalization／小型 adapter 表達差異；三個 horizon 使用獨立 head，校準器再按市場與 horizon 分開。
2. 各模態輸入視窗與共同封套如何固定？建議價量 252 sessions、基本面 8 季 vintage、月頻總體 24 個 vintage／季頻 8 個 vintage、文件事件 20 sessions 且最多 64 個已授權 segment 表示；全部攜帶觀測 mask、age、coverage、quality、point-in-time 與版本譜系。
3. 文字表示要端到端微調還是預先計算？建議首版只讀文件管線產生的版本化多語 segment embedding／市場影響特徵，凍結大型文字 encoder，只訓練小型 attention pooling 與 projection。
4. 缺失模態如何處理？建議只有合格價量歷史是預測必需；其他模態用明確 availability／staleness／policy mask、gated fusion 與訓練期 modality dropout，缺失不冒充中性或零值，並反映到可用狀態與不確定性。
5. 首版算力護欄如何定？建議 forecast module 不超過 15M trainable parameters、預先計算大型 embedding、單張 16 GB commodity GPU 可完成單一回測摺訓練，正式 EOD 推論在 CPU 也能於 10 分鐘內完成可設定股票池；完整管線仍受收盤後 2 小時 SLA 約束。

### Round 1 decision

使用者確認五項全部採建議。首版採共享編碼器／fusion 加市場別小型 adapter 與校準器；固定四類模態視窗及共同 point-in-time 封套；凍結大型多語文字 encoder；只有價量是硬性必要模態；forecast module 受 15M trainable parameters、16 GB 單 GPU 訓練及 CPU 十分鐘推論護欄約束。

### Round 2 frontier

6. Fusion 如何保留價量基準並防止 gate collapse？建議每個 horizon 使用獨立 quality-aware gate；以價量表示作 residual anchor，其他可用模態作受 mask 的加權增量，並用 modality dropout、輔助單模態 head 與 gate 使用率監控防止長期只吃單一模態。Gate weight 只表示模型資料依賴，不當作因果解釋。
7. 多任務 loss 與類別不平衡如何處理？建議三個 horizon 等權的 weighted cross-entropy；類別權重只由每個訓練摺計算並限制在 0.5–2.0，禁用跨時間 oversampling／SMOTE，focal loss 不作預設，以免犧牲機率校準；輔助 head loss 只占小比例。
8. 一年校準／驗證資料如何再隔離？建議依時間切成前 9 個月模型選擇／early stopping、後 3 個月機率校準；模型與所有前處理先凍結，再按市場 × horizon 擬合 multiclass temperature scaling。樣本不足時回退 identity calibrator 並阻止宣稱已校準，不使用測試季補樣本。
9. 可替換 seam 放在哪裡？建議外部只看一個深 `TrendForecaster` module interface：`train(TrainingRequest) -> ModelArtifact`、`predict(PredictionRequest) -> ForecastBatch`；TCN、MLP、文字 pooling、gate、head 與 framework checkpoint 都留在 implementation 內。以 neural adapter 與 deterministic baseline adapter 證明 seam 真實存在，測試也只穿過同一 interface。
10. 缺失模態與信心分數如何同時呈現？建議信心分數維持既有定義，只取校準後機率熵，不乘上人工 coverage 權重；另輸出 `prediction_status`、模態 availability／age／quality 與 `data_support`。價量不足時 `unavailable` 且不產生機率；其他模態缺失時可產生 `degraded` 預測並按支援狀態分層驗證。

### Round 2 decision

使用者確認五項全部採建議。每個 horizon 使用 quality-aware gate 與價量 residual anchor；訓練採受限類別權重的等權多任務 cross-entropy；一年校準／驗證依時間拆成 9 個月選模及 3 個月 temperature scaling；外部 seam 固定為深 `TrendForecaster` module；信心熵與資料支援狀態分離呈現。

### Round 3 frontier

11. Optimizer、early stopping 與隨機種子如何固定？建議 AdamW、warmup 後 cosine decay、gradient clipping 1.0、最多 50 epochs；以市場 × horizon 等權 validation NLL early stop，patience 7。每個候選使用三個預先登錄 seed 報告平均與最差表現，禁止挑 seed；現行 artifact 使用預先指定 primary seed，不以 ensemble 隱藏不穩定。
12. 價量與 optional modality 的可用資格如何定？建議 anchor session 必須有有效調整收盤；252-session 視窗有效日不少於 240 為 `full`，60–239 為 mask／left-pad 的 `degraded`，少於 60 為 `unavailable`。基本面／總體 vintage 可依業務有效期 carry forward 但必帶 age；「沒有新聞」只有在來源涵蓋完整時才是有效空集合，partial／policy-blocked 必須是 missing。
13. 正規化如何避免跨時間與股票池洩漏？建議所有 median／IQR、winsorization quantile、缺值統計都只由訓練摺擬合並包進 artifact；市場別 robust scaling，point-in-time cross-sectional rank 只使用當次股票池及合格 session。首版不使用 listing ID embedding，避免記憶個股與新掛牌 cold-start。
14. 20-session、64-segment 文字輸入如何挑選？建議只取授權合格且具 confirmed 標的連結，或具明確市場／產業 scope 的 segment；subject 優先，counterparty／peer 等角色必須明示。先按文件群組去重，再以來源權威、處理品質、角色與 recency 的版本化規則決定性排序；不得依未來標籤或測試期學到的 relevance 挑選。Aggregator 同時接收 embedding、事件／市場影響、來源、age、quality 與 evidence pointer。
15. 主要影響因素如何產生？建議 `predict` 直接回傳每個 horizon 前五項正／負分組 attribution；neural adapter 使用 Integrated Gradients，依模態、具名特徵與時間桶聚合，文字 attribution 映射回文件片段。Gate weight 另列為 reliance metadata，禁止冒充 attribution、因果或買賣理由。
16. `ModelArtifact` 如何自包含與復現？建議不可變 bundle 同時保存權重、architecture config、feature schema、normalizer、市場 adapter、三個 head、六個 calibrator、label／dataset／processing manifests、套件鎖定、seed、程式 SHA 與內容雜湊。推論 implementation 不連網、不查 `latest`、不自行抓資料，只讀 `PredictionRequest` 的不可變 FeatureBatch。
17. 哪些 baseline 與 ablation 必須存在？建議同一 interface 至少有 market × horizon prior、正規化 multinomial logistic 價量基準，以及 neural price-only ablation；完整模型另逐一移除基本面、總體、文字並關閉市場 adapter。所有結果使用相同回測摺、校準與成本情境，完整模型不因參數較多便預設獲勝。

### Round 3 decision

使用者確認七項全部採建議。固定 AdamW 與三個預先登錄 seed；價量依 240／60 有效 session 分為 full／degraded／unavailable；所有正規化只由訓練摺擬合；文件 segment 以 point-in-time 決定性規則挑選；解釋使用可回到文件片段的分組 Integrated Gradients；ModelArtifact 必須離線自包含；完整模型須接受 prior、logistic、price-only 及逐模態消融比較。

### Round 4 frontier

18. 各 encoder 的首版容量如何固定？建議共同 latent dimension 128；價量使用 causal masked TCN，6 個 residual blocks、kernel 3、dilation 1／2／4／8／16／32、128 channels，覆蓋 253 sessions；基本面 MLP 256→128、總體 MLP 128→128；文字用 2-head masked attention pooling 後投影到 128；market embedding 16。所有大小仍受 15M 總 trainable parameter 護欄。
19. 首版 feature schema 收哪些族群？建議價量包含內部調整 log return、range／gap、log volume／turnover、liquidity、realized volatility、市場／產業相對報酬及公司行動旗標；基本面包含成長、獲利、現金流、槓桿、流動性、估值及規模；總體包含利率／殖利率曲線、通膨、成長、就業、FX、商品、指數／波動與機構 vintage；文件包含 embedding、事件／市場影響、來源、角色、age、quality。禁用 nominal raw price、ticker／名稱字串及未版本化 provider 欄位。
20. 共享模型的 loss 如何避免被單一市場或 horizon 支配？建議先在每個 market × horizon cell 內計算受限類別權重的 mean cross-entropy，再等權平均六個 cell；掛牌與時間樣本不複製。所有單模態 auxiliary loss 合計最多占 10%，並另行記錄主 loss 與各 cell 指標。
21. `ForecastBatch` 必須維持哪些不變量？建議每個請求掛牌 × horizon 都回傳一筆結果或結構化不可用原因；可用結果含三類校準機率、信心熵、校準狀態、data support、前五正／負因素、gate reliance 及完整版本引用。單一掛牌失敗不拖垮整批；同一 FeatureBatch 的結果不因請求順序或批次組成改變，橫斷面特徵必須預先固定在 FeatureSnapshot。
22. HPO 可以搜尋哪些東西？建議 label／calendar／input windows 不屬模型 HPO；只搜尋 channels {64,128}、hidden {128,256}、learning rate 1e-4–3e-3、weight decay 1e-6–1e-2、dropout 0.10–0.35、optional modality dropout 0.10–0.40、auxiliary loss 0–0.10。每次最多 30 個可提早停止 trial，使用同一 chronological split 與預先登錄 seeds，測試季完全不可見。
23. 何時允許把台美拆成兩套模型？建議 MVP 禁止自動分拆；只有 shared＋adapter 與 separate-market 候選在至少 8 個季度、3 seeds 的預先登錄比較中，目標市場統計、校準與成本後經濟指標均穩定改善，另一市場與 degraded coverage 不退步，且仍符合 10 分鐘 CPU SLA／營運成本時，才能作新候選架構並走人工升版。確切改善門檻由模型生命週期票券固定。
24. OOD／drift 責任放在哪裡？建議 ModelArtifact 保存訓練參考分布；TrendForecaster 只做 feature schema 驗證並回傳 support distance／OOD flags，schema 不相容為結構化失敗、範圍外但可計算者為 degraded。跨日漂移判定、告警、重訓與升版屬外部監控／模型生命週期 module，不讓推論 implementation 自動更新自己。

### Round 4 decision

使用者確認七項全部採建議。固定 128 維共同表示與 253-session causal TCN 基準架構；正式 feature schema 採具經濟語意及版本的價量、基本面、總體、文件特徵；六個 market × horizon loss cell 等權；ForecastBatch 對部分失敗、批次順序及組成保持不變；HPO 僅搜尋模型容量與訓練參數；MVP 維持 shared＋adapter，市場分拆須經八季三 seeds 比較與人工升版；推論只回報 OOD 支援訊號，不自行漂移處置或更新。

### Shared-understanding checkpoint

四輪共 24 項決策已回答，設計樹 frontier 為空。使用者已明確確認共有理解成立。

## Answer

採單一共享台美多模態模型：價量 causal TCN 作每個 horizon 的 residual anchor，基本面／總體 MLP 與凍結多語文件表示作可缺失增量，透過 market embedding、小型 adapter 及三個 quality-aware gates／heads 融合，再按市場 × horizon 個別校準。只有合格價量是硬性必要模態；資料支援狀態與信心熵分開，缺失、空集合、逾時及 policy-blocked 具有不同語意。

外部 seam 固定為深 `TrendForecaster.train／predict` interface；模型成品離線、自包含且綁定 feature、normalizer、calibrator、資料／處理／來源政策及程式版本。訓練採六個 market × horizon cell 等權 loss、三 seeds、9 個月選模／3 個月校準、有限 HPO，並接受 prior、logistic、price-only 及逐模態消融。主要影響因素使用可回到文件片段的 Integrated Gradients；gate reliance 不是因果解釋。推論只回報 OOD／支援狀態，不自行重訓或升版。

- Design: [`docs/design/multimodal-trend-model.md`](../../../docs/design/multimodal-trend-model.md)
- ADR: [`docs/adr/0008-shared-multimodal-model-behind-deep-forecaster-seam.md`](../../../docs/adr/0008-shared-multimodal-model-behind-deep-forecaster-seam.md)
