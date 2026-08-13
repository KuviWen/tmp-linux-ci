# 原型化預測解釋與研究介面

Type: prototype
Status: resolved
Blocked by: 06, 08, 09

## Question

建立低成本互動原型，讓使用者實際評估股票搜尋、三期間趨勢機率、信心與缺資料狀態、前五項正負因素、新聞來源、模型／資料版本、歷史預測比較及回測視圖的資訊層級與必要操作。

## Prototype under review

- 原型：[research-experience-PROTOTYPE.html](../../../docs/prototypes/research-experience-PROTOTYPE.html)
- 性質：單檔、唯讀、合成資料；不連接行情、模型或正式服務，也不提供投資建議。
- A「研究工作台」：以單一標的的預測、資料支援、證據與版本並排，優先支援每日深度研究。
- B「證據卷宗」：以可閱讀、可複核的研究文件依序呈現預測、歸因、來源、歷史、回測與版本。
- C「比較矩陣」：先跨市場篩選訊號分歧與資料異常，再展開單一標的細節。
- 共同互動：搜尋股票、切換 1／5／20 日焦點、模擬完整／文件降級／價量不足狀態、切換細節頁籤，以及檢視完整原型狀態。
- 驗證狀態：JavaScript 已通過語法編譯檢查；待使用者比較三方案後決定保留或混合的資訊架構。

## Prototype evidence

- Captured branch: `codex/prototype-research-experience`
- Captured commit: `804bd2a429867e85fa2cce8b50f15f44310c2c20`
- Artifact in branch: `docs/prototypes/research-experience-PROTOTYPE.html`
- Scope: single-file read-only UI prototype with three structurally different variants, synthetic data, URL-stable state and explicit complete／degraded／unavailable scenarios.
- Verification: JavaScript syntax compiles and all required research views are present.

## Answer

使用者以「全部採建議」確認共有理解並採納混合方案。首版以 C「比較矩陣」作為跨台美股票池的研究入口，優先呈現三期間機率、信心與資料支援差異；點選掛牌後進入 A「研究工作台」，在同一視野核對三期間預測、前五項正負影響因素、來源證據、歷史預測、滾動回測與完整版本譜系。B「證據卷宗」延後作為唯讀報告／匯出投影，不建立另一套預測或解釋語意。

信心分數與資料支援狀態永遠分開展示；可選文件模態降級時保留機率並明示缺口，必要價量或版本資料不足時阻斷推論且不顯示替代機率。正式介面使用內部掛牌身分而非 ticker 作權威鍵，所有可分享檢視狀態寫入 URL，並以不可變預測紀錄及來源政策允許的證據為查詢基礎。

- Design contract: [`docs/design/research-experience.md`](../../../docs/design/research-experience.md)
