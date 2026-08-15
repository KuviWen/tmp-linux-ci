# Compose 完整運行，Kubernetes 單區 HA 加異地復原

> 自 P2 起，ADR 0017 將 Compose／本機開源輪廓定為必要產品輪廓；Kubernetes、三 failure domains 與異地復原只可作零成本選配實驗，不是 ticket 完成或產品發布門檻。

系統使用相同的部署成品與 module interface支援三種部署輪廓：Docker Compose提供完整開發與可受控停機的小型pilot，但不宣稱HA；正式Kubernetes在單一區域的三個failure domains運行stateless replicas、PostgreSQL同步副本及多副本物件，另一區域只維持暖資料／冷應用的人工災難復原，不做active-active。這避免為日終研究系統引入跨區寫入一致性與雙主風險，代價是區域災難時完整artifact-dependent能力接受最差24小時RPO及4小時RTO，並要求版本化復原集合、單一部署世代、定期failover／failback與容量驗收。
