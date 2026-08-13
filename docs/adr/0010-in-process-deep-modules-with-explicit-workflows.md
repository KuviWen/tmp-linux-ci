# 程序內深模組搭配明確工作流

首版將資料供應、文件情報、特徵、實驗、正式預測、模型治理、研究查詢與營運控制保留為同一 application image 中的程序內深模組，以普通 interface、不可變 artifact 及 PostgreSQL transactional outbox 協作；Dagster 明確編排長流程，REST 不成為內部管線或即時模型推論入口。這放棄按處理階段提早拆微服務及以事件隱式編舞的獨立擴縮彈性，換取 Docker Compose 端到端可運行、單一交易可守住權威狀態、低延遲本機呼叫與可在 interface 上驗證的不變量；只有通過量測、故障語意與雙 adapter 閘門後，才允許在不改變領域契約下建立遠端 seam。
