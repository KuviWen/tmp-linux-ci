# 行動權限與來源使用資格交集治理

> ADR 0017 補充：官方公開資料的來源使用資格由版本化公開條款建立，不要求 principal-specific entitlement；非公開來源若存在仍須 fail closed，但不得成為 P2 及後續 ticket 的必要依賴。

人員或工作負載能執行某操作，不代表資料供應者允許其資料被用於該操作；反之，有效來源契約也不授予系統管理能力。因此所有入口先形成可信 `SecurityContext`，再由應用擁有的單一授權 module 將行動權限、來源使用資格、來源政策、用途、環境及資料保護類別取交集並 fail closed；IdP group、reverse proxy、Dagster、資料庫角色及外部 policy engine 只能作 adapter 或防禦縱深，不能另建權限真相。這增加版本化政策、決策稽核及雙人核准成本，但避免管理員角色、工具設定或網路位置繞過資料授權。
