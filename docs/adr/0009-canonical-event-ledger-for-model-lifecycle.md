# 以 canonical 事件帳本治理模型生命週期

模型訓練、評估、閘門、人工核准、shadow、升版與回退以應用擁有的 append-only 事件帳本及內容定址成品作唯一權威，MLflow registry、serving cache 與其他工具只作可重建 projection；模型成品本身沒有可變 production stage，正式使用由原子服務指派決定。這增加事件投影、outbox 與 reconciliation 的實作成本，但避免工具 alias、部分故障或人工編輯破壞七年譜系，並確保任何緊急操作只能停用或回退到既有合格成品，不能繞過 hard gates 與人工核准。
