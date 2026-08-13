# 模組化資料平台搭配開放儲存 seam

資料平台採模組化單體，以 Dagster OSS 編排、PostgreSQL 保存權威狀態、不可變 Parquet 與內容定址物件保存大量資料，並透過 ObjectRepository、DatasetCatalog、DatasetReader 等小型介面隔離 SeaweedFS／S3-compatible 儲存與批次分析工具。這放棄首版的微服務、訊息代理、lakehouse、分散式 SQL／查詢及線上 feature store，以較低營運複雜度換取單機可運行與可復現性；若未來量測證明需要擴展，必須在既有 seam 後新增 adapter，而不是改變資料集版本與譜系語意。
