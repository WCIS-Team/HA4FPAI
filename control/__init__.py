# -*- coding: utf-8 -*-
"""control —— HA4FPAI 统一控制与业务资料层（unified-control 分支骨架）。

按 wiki/mainchain 联合规划实现目标契约的第一版骨架：

  * SQLite 业务主库：版本化迁移、单写入口、store generation / restore_epoch
  * 统一 person_id：人员、外部旧 ID 映射、人脸／声纹样本登记契约
  * 设置 desired/applied 与 (scope, epoch, revision) 防乱序
  * 命令状态机、幂等键、危险动作确认绑定、事件 envelope（实例级 producer）
  * /v1 HTTP 服务、klar V2 统一适配器、直接短语整句匹配
  * 旧声纹库（boards 单向量 / lang 多样本）只读预览适配

视频域适配器当前**有意留空**（所有 camera.* / recording.* 返回
video_domain_not_connected）：留空范围与逐项接法见
docs/VIDEO-DOMAIN-PLACEHOLDER.md；不搬运视频仓源码。

纯标准库，板上 py3.8 与本机 py3.13 均可运行。
"""
__version__ = '0.1.0'
