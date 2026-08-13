# 驗證趨勢標籤與防洩漏回測契約

Type: prototype
Status: resolved
Blocked by: 01, 02, 04, 05

## Question

以代表性台美股票與真實交易日曆建立可丟棄的資料／回測原型，驗證波動度感知盤整門檻是否造成合理且可學習的類別分布，並證明公司行動、資訊截止點、20 日 purge／embargo、成熟標籤與交易成本的實作不會洩漏未來資料。

## Prototype evidence

- Captured branch: `codex/prototype-trend-label-backtest`
- Captured commit: `cf25e1459974692f30651dbc4764a4a2be255bc5`
- Artifact in branch: `docs/prototypes/trend-label-backtest-PROTOTYPE.html`
- Scope: single-file logic prototype with free-play controls and six guided walkthroughs; aggregate research statistics only, no raw prices.
- Aggregate evidence: 10 Taiwan and 10 US representative stocks, 2015-01-01 through 2025-12-31, XTAI/XNYS sessions, 157,610 usable 1/5/20-session samples across both markets.
- Provisional finding: coefficient 0.35 with 0.60% Taiwan and 0.25% US floors leaves all three classes learnable; 20-session labels, especially in the US sample, retain an upward class skew that should be measured by regime rather than forced into equal buckets.
- Contract counterexample found: converting Taiwan daily timestamps through `America/New_York` created a 21.85% calendar mismatch and zeroed the compact label matrix; the prototype therefore makes exchange calendar and market timezone explicit state.
- Verification: embedded JSON parses, JavaScript compiles, 60 DOM ids are unique, and no raw prices are embedded. In-app visual inspection was unavailable because local `file://` navigation is blocked by browser security policy.

## Answer

共有理解已由使用者以「繼續下一步」確認。採用版本化波動度感知三分類標籤：`max(market floor, 0.35 × prior-20-session volatility × sqrt(horizon))`，台灣與美國 floor 分別為 0.60% 與 0.25%，並保留樣本期真實類別偏斜而不以測試分位數強迫平衡。標籤以 XTAI／XNYS realized sessions、內部調整版本及精確 t+h 端點生成；缺失端點不順延。

回測採季度 walk-forward：7 年訓練、1 年校準／驗證、固定 20-session purge、20-session embargo 及 1 季一次性測試，至少覆蓋最新 8 個完整季度。特徵、文件處理、標籤成熟、公司行動、測試選模與交易成本都必須遵守時間點血緣及不可變 manifest。

- Design contract: [`docs/design/trend-label-and-backtest-contract.md`](../../../docs/design/trend-label-and-backtest-contract.md)
- ADR: [`docs/adr/0007-fixed-label-semantics-and-purged-walk-forward.md`](../../../docs/adr/0007-fixed-label-semantics-and-purged-walk-forward.md)
