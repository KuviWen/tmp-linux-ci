# 定義標的身分、交易日曆與時間點資料契約

Type: grilling
Status: resolved
Blocked by: 01, 02

## Question

系統如何跨交易所、ticker 變更、公司行動與不同識別碼表示同一標的；事件時間、發布時間、首次取得時間、更正版本及資訊截止點如何形成 append-only 的時間點資料契約；哪些欄位是所有來源 adapter 必須提供的最小共同介面？

## Answer

完整決策見 [標的身分、交易日曆與時間點資料契約](../../../docs/design/point-in-time-data-contracts.md)。

系統以發行人、證券及掛牌三層不可變內部身分表示標的，外部識別碼只形成有來源、證據及有效期間的身分主張；每筆預測指向單一掛牌。資料採 append-only 雙時間證據鏈，以首次取得時間決定歷史可用性、以業務有效時間決定適用期間，並分離原始資料物件、來源紀錄版本、正規化紀錄版本及擷取收據。

來源套件在兩個 seam 提供 `SourceCollector.collect` 與 `SourceDecoder.decode`；平台統一擁有內部版本、時間點排序、checkpoint、來源政策、身分治理、結構化 outcome 與涵蓋報告。交易日曆區分預測時的 projected version 與成熟標籤使用的 realized version；行情調整由版本化公司行動及內部規則推導，不以供應商 adjusted close 作歷史真相。
