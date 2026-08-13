# 具證據的歷史重建不改寫首次取得時間

平台保留 `first_observed_at` 作實際取得內容的權威時間，另以版本化歷史可得性主張證明平台建置前某一來源版本在指定時點已可取得；只有 `platform_observed` 或可信 `archive_attested` 證據可進正式歷史訓練與回測，後者永遠不能冒充當時的 production 觀測。這增加archive資格審查、修訂語意、契約與譜系成本，但避免把回填日、發布日或目前最終值偽裝成歷史可知資料，同時讓具完整 as-of 證據的歷史資料能支援可稽核模型建置；證據等級與選擇模式由[時間點契約](../design/point-in-time-data-contracts.md)固定。
