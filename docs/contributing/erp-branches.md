# ERP 独立仓库与对接说明

## 应该上传到哪里

2026-10-16 起，应用按一个 Frappe App 一个仓库管理，统一使用 `main` 开发和接收 PR。仓库名称使用 `deeplinkerp-` 前缀，Frappe App 名、Python 包和接口路径保持不变。

| 业务范围 | App | GitHub 仓库（组织为 `yueweiit`） |
| --- | --- | --- |
| ERPNext 定制核心 | `erpnext` | `deeplinkerp-erpnext` |
| 采购、销售、库存及共享界面 | `deeplinkerp_branding` | `deeplinkerp-deeplinkerp_branding` |
| CRM 订单与出库接口 | `crm_integration` | `deeplinkerp-crm_integration` |
| MES 集成 | `mes_integration` | `deeplinkerp-mes_integration` |
| 中国财务和凭证 | `china_finance` | `deeplinkerp-china_finance` |
| OA 采购申请与审批 | `oa_purchase_request` | `deeplinkerp-oa_purchase_request` |
| 海外成本核算 | `overseas_costing` | `deeplinkerp-overseas_costing` |
| 筛选和桌面扩展 | `custom_filters` | `deeplinkerp-custom_filters` |
| 客户定制与经营指标 | `client_akivision` | `deeplinkerp-client_akivision` |
| AI 助手 | `ai_assistant` | `deeplinkerp-ai_assistant` |
| 草稿通知 | `draft_notifications` | `deeplinkerp-draft_notifications` |
| 移动作业 | `mobile_operations` | `deeplinkerp-mobile_operations` |

按实际修改的 App 选择仓库，跨应用修改拆成相应仓库的 PR，并标明彼此依赖。原 `yueweiit/DeepLinkERP` 的应用分支和历史标签继续保留，用于追溯迁移前的版本。

## 同时修改三个部分时

ERP 页面改动提交到 Branding 仓库，ERP 侧 CRM 接口改动提交到 CRM Integration 仓库，财务改动提交到 China Finance 仓库；涉及 OA 申请和审批的代码另外提交到 OA Purchase Request 仓库。
各应用分别检出到 Bench 的 `apps/<App名称>`，为同一次对接的 PR 标明依赖、接口字段和验收顺序。
独立运行的 CRM 后端属于另一套仓库；这里的 `crm_integration` 是 ERP 侧接入应用，并非 CRM 网站后端源码。

## 取得应用主线

以下以已有本地 Branding 工作区为例：

```bash
git remote get-url origin
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c feature/your-change
```

Branding 的 `origin` 应为 `https://github.com/yueweiit/deeplinkerp-deeplinkerp_branding.git`。其他应用使用表中对应的仓库。新克隆仓库默认检出 `main`；已有未提交修改先正常提交或保存，再更新工作区。
PR 的目标选择本应用仓库的 `main`；完成合并后删除本人临时功能分支。
如果旧功能分支在本次整理前已经开发，应从更新后的固定分支新建功能分支，再有选择地移植所需提交并重新测试；不要把归档版本整批合回来。

## 本次基线如何确定

迁移以原仓库各应用固定分支已核实的提交为起点，保留其可达历史。仓库入口和 CI 调整以新提交记录，不改写已有业务提交。
此前按用户确认完成的生产对齐历史继续保留。原仓库的 `production/2026-10-16/<应用分支名>` 保存生产提交，`archive/2026-10-16/<应用分支名>-before-production-alignment` 保存对齐前主线。
原默认分支中的三个独有提交，以及 CRM 主线尚未部署的订单修订改动，本次不带入协作基线；需要恢复时独立评审。
已验证的 CRM 发布工具 `289690cd45944a3ccbfe8fb8e100c2fc1fe9791d` 继续保留在提交历史中。

线上 ERP 镜像包含多个应用的冻结版本，具体提交见根目录 `PRODUCTION_BASELINE.json`。
分支会随着后续开发继续前进，因此分支最新提交不自动代表已经部署；核对当前线上接口时，以该文件的生产提交、应用代码树和实际在线接口为依据。
OA 原先仅在本地保留的生产提交现已通过生产标签归档到 GitHub。

## 验证与发布

提交 PR 时运行对应应用现有测试，集成修改另外验证接口的请求、响应、权限、幂等键和来源关联。
本次整理不调整接口路径、字段、数据库结构或业务数据，也不触发服务器部署。
后续发布仍走现有发布锁、冻结提交、健康检查和业务页面验收，并在验收后更新生产快照。
