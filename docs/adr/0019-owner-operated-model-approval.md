# 單一擁有者部署使用可揭露的模型自行核准政策

## Status

Accepted

## Context

本產品目前由同一名自然人使用、維護、訓練及核准模型；要求以兩個帳號冒充不同人不會形成獨立審查，反而會留下錯誤的職責分離證據。模型仍須有人類明確承擔決定，且不得因此繞過來源資格、hard gates、不可變證據、shadow 或服務指派前置條件。

## Decision

模型核准由內容定址且不可變的 `ModelApprovalPolicyVersion` 選擇 `separated_duties` 或 `owner_operated`。`owner_operated` 只允許政策指定的穩定 owner principal 核准自己發起或執行的訓練；每筆決定必須綁定該政策版本、exact artifact、evaluation、gate policy、expected assignment 與理由，並明記 `independent_review=false`，REST 與研究介面不得把它顯示成獨立審查。它不能把失敗或缺漏的 hard gate 改成通過。多人部署仍使用 `separated_duties`；切換輪廓要建立新的政策版本，不能用一次性 `allow_self_approval` bypass。

## Consequences

單一擁有者可以用一個真實 principal 完成模型生命週期，不需第二個人或假帳號；代價是模型風險沒有第二雙眼睛，這項限制成為核准與 acceptance bundle 的永久可見證據。ADR 0003 的人類核准、所有 hard gates 及 append-only 決定仍有效；本 ADR 只取代「每次模型核准都必須由另一名自然人完成」的假設，來源政策、安全、刪除等其他雙人控制不因此自動放寬。
