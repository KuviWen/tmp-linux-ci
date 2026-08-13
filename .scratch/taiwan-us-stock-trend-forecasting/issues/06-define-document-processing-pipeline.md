# 定義新聞、公告與財報處理管線

Type: grilling
Status: resolved
Blocked by: 01, 02, 04, 05

## Question

文件如何取得、保存授權證據、正規化、跨來源近似去重、語言辨識、標的連結、主題分類、情緒與事件抽取；更正、撤回、內容缺漏及只允許保存摘要或連結的來源如何保留可追溯性？

## Answer

完整決策見 [新聞、公告與財報處理管線](../../../docs/design/document-processing-pipeline.md)。

文件採 Document／Version／Rendition／Segment 四層不可變身分，內容權利由 `full_content`、`summary_only`、`metadata_link_only` 或 `disabled` 來源政策模式強制執行。管線逐階段發布不可變資料集版本，保留原始呈現、可回到原始座標的標準文本及 matching fingerprint；XBRL／iXBRL 是正式財務數值的優先來源，低品質或惡意文件可以 abstain／隔離。

完全及近似去重不刪除來源證據；標的連結帶有證據、角色、狀態及置信度，只有 confirmed 連結進入個股特徵。文件標註分離一般語氣與市場影響，先產生事件提及再聚合市場事件。`DocumentPipeline.process` 是外部深模組介面，每次固定不可變處理組合版本；更正、撤回、人工裁決及模型升級只產生新版本，並受首次取得時間、處理完成時間及 feature-freeze 約束。
