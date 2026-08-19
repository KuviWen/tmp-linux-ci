# Ticket 工程驗收與模型營運資格分離

## Status

Accepted

## Context

本產品是單一擁有者、loopback-only、個人非商業研究工具。若 implementation ticket 必須等候某個真實模型完成全部外部歷史資格、人工事件及五個不同日曆日期的 shadow，程式即使已完整交付也會長期無法結案；反之，把 fixture 當成正式模型證據又會留下不實資格。

## Decision

Implementation ticket 以「工程驗收」判斷程式交付。隔離的 contract scenario 可以在 ephemeral test store 中建立 deterministic、形式上完整的正向與反向 evidence，經公共 seam 證明政策的通過與否決、owner-operated 核准規則，以及五個不同且遞增 EOD cycle 的狀態轉移；test suite／case 本身界定其 engineering scope，事件在測試後丟棄且不能匯出為營運證據。部署型 evidence 則必須明確標記 `engineering_acceptance`／`engineering_example`，證明整合及 fail-closed 行為，並保持 `formal_model_qualification=not_claimed`。固定歷史日期可在同一次測試執行中完成，不要求等待五個真實交易日。

「模型營運資格」仍是另一個 runtime 判定。工程驗收不能宣稱特定真實模型通過 hard gates、不能代表 owner 已核准真實 artifact、不能冒充 production shadow 或預測價值，也不能建立 production 服務指派。真實候選日後仍須以實際合格資料、內容定址評估、全部 hard gates、owner 決定及五個實際 eligible EOD shadow cycles 通過既有 runtime 契約。

Ticket 09 的 AC 5–7 因此以公共 seam 的決定性工程證據完成；P2 phase exit 與任何 production assignment 仍以模型營運資格為準。ADR 0003、0009、0019 的 runtime hard-gate、append-only、核准與 shadow 約束不變，本決定只改變 delivery checkbox 所需的證據類型。

## Consequences

個人開發者可在一天內完成並重現 Ticket 09，而不用建立假角色、等待五個日曆日或偽造來源／模型結果。代價是「ticket 完成」不再表示已有可上 production 的模型；研究介面、acceptance bundle 與最終回報必須持續顯示 `formal_model_qualification=not_claimed`、serving blocked 及沒有 production assignment，直到另有真實營運證據。
