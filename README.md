### DeeplinkERP Branding and Shared Customizations

DeeplinkERP 的品牌、导航和跨业务通用定制。目前包含四个库存明细页面、库存快照及标准物料移动单预填入口。

四个库存明细 Desk 页面统一展示 ERP `Bin.actual_qty` 实时库存和当前 Warehouse 库位编码。
四页默认通过 `filters.all_companies=1` 查看当前账号有权访问的所有公司库存；
先按原生 Company 查询权限取得公司，再按 Warehouse 查询权限取得这些公司的叶级仓库。
每行及 Excel 增加所属“公司”，同一物料在不同公司/仓库的库存分行展示，不合并数量。
页面的“操作公司”只决定物料移动所属公司：其他公司行可查看，但需切换操作公司后才能勾选移动；
原生创建、提交及来源/目标仓库同公司校验继续生效。切换操作公司清空已选行，保留仓库和物料组等筛选，
不改变跨公司库存查询范围；旧单公司模式切换公司时仍清空仓库和物料组。
旧调用方不传 `all_companies` 时继续按传入公司查询；跨公司模式只读取实时 Bin，不使用某一公司的历史快照补行。
四页列表及分类 Excel 不展示参考库位、快照库位数量、库存差异、库存状态和快照日期，
每个物料仓库组仅占一行；页面也不提供参考库位/快照筛选或参考快照日期摘要。
接口保留快照数据及原有筛选参数以兼容既有调用方。
四页列表及分类 Excel 也不展示 DPCI、外部编码和原始标识/别名；这些辅助标识继续保存在 Item，
接口保留对应字段，关键词查询仍支持三项标识，供编码对照和既有集成使用。
半成品、成品和模具按各自根物料组及子组划分，物料页显示其余
库存物料，四页互不重叠。分类页默认“只看有库存”，取消勾选可查看零库存及未生成 Bin 的物料；
列表调用 `services.inventory_detail_service.get_inventory_detail_page`。
“下级物料组”选项通过原生 `Item Group.get_list` 权限查询，在分类树区间内返回可读取分组；
物料页同时排除另外三类分组，不返回无权读取的分组名称。
半成品、成品和模具的“物料组”列及 Excel 显示各自分类根节点下面的直属产品分组，
如 `NFA1010 Fantasía/注塑 TPU-Inyección` 显示为 `NFA Fantasía 骑士FA`；支持更深层工序。
已直接属于产品分组的记录及物料库存明细页保持原组名。响应的 `item_group` 保留实际分组，
`display_item_group` 仅用于显示；缺少可读取的产品祖先时回退到实际组名，原筛选和库存定位不变。
半成品、成品列表和 Excel 在物料名称后展示“颜色”，复用 Item 的“规格”字段
`custom_specifications`，接口字段为 `color`；缺少该字段、无字段读取权限或值为空时留空。
这两页的“物料组”紧跟“颜色”，随后为仓库、库位、实时库存、BOM 用料状态和库存单位，Excel 使用相同顺序。
跨公司模式在仓库前显示所属“公司”。
模具和物料库存明细页不展示颜色，不新增数据库字段，不从名称或变体属性猜测颜色。
叶级 Warehouse 名称符合“库位编码 + 父仓库名称”时，列表和 Excel 将父仓库与 ERP 库位分列展示；
库位编码仅识别含数字的英文字母、数字及分段连字符组合（如 `A-01`、`P04R01S`、`AG-B-E02`），
描述性或其他格式的前缀保留完整叶级仓库名，库位留空。库存定位仍使用叶级 Warehouse。
“仓库”筛选支持可访问公司的具体叶级仓库或父仓库；父仓库须有读取权限，只查询其下有权访问的
叶级仓库（包括多层子仓库），空父仓库返回空结果。列表及 Excel 导出沿用相同范围。
先在 SQL 中计数和分页物料仓库组；旧单公司模式继续读取当前页快照参考。导出按当前筛选条件逐页读取，
四页和旧快照导出共用 `Inventory Original Location Snapshot` 的原生 `export` 授权，
沿用本 App 已有库存功能角色配置；`Item`、`Bin`、`Warehouse` 仍须具有读取权限，
公司、物料及仓库的行级读取限制继续用于导出。此授权只允许导出库存报表，不授予基础表导出权限；
不要求派生库存汇总表 `Bin` 自身拥有 `export` 标志。导出超过 5000 行时使用流式 Excel，
每组仍只占一行；单次最多 100000 个物料仓库组、20 MB 文件。
旧 `get_inventory_location_detail` 和 `export_inventory_location_detail` 继续保留快照口径，供已有调用方使用。

四个库存明细页面的“BOM 用料状态”按当前页面有权查看的物料编码，在本站点任一公司已提交、
已启用 BOM 的直接 `BOM Item.item_code` 中查找：有匹配显示“已用于 BOM”，否则显示“未用于 BOM”。
列表筛选及 Excel 导出沿用同一口径。该状态与库位快照的“库存状态”独立，实时计算且不写入
物料、BOM 或库存单据；查询不向页面返回 BOM 编号或所属公司。跨公司出现仅说明有用料引用，
不代表该 BOM 已适用于当前公司生产。列表和导出响应以 `bom_usage_scope=all_companies` 标识此口径。
为兼容仍打开的旧页面，旧筛选值 `other_company` 按 `used` 处理，`unknown` 返回空结果；新页面只提供两项。

### 协作主线与线上基线

本应用独立仓库为 [yueweiit/deeplinkerp-deeplinkerp_branding](https://github.com/yueweiit/deeplinkerp-deeplinkerp_branding)，统一使用 **`main`**。采购、销售、库存页面及共享服务的修改，从 `main` 拉取并向 `main` 提交 PR，Frappe App 名保持 `deeplinkerp_branding`。
`codex/sales-order-prepayments` 等是历史开发分支，不作为同事后续上传代码的固定入口。

本次以服务器正在运行的 Branding 提交 `3ff92df60f67d1cd76e79e2d063bd44b71a8a410` 为应用代码基线，保留已验证的发布工具和 Git 历史。
其他应用分别使用独立仓库的 `main` 分支；CRM、财务和 OA 接口的提交位置见 [独立仓库与对接说明](docs/contributing/erp-branches.md)。原共享仓库及其历史标签继续保留用于追溯。
实际运行的各应用提交记录在 [PRODUCTION_BASELINE.json](PRODUCTION_BASELINE.json)。这是发布快照，主线后续提交不代表已自动部署。

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app --branch main deeplinkerp_branding https://github.com/yueweiit/deeplinkerp-deeplinkerp_branding.git
bench install-app deeplinkerp_branding
```

### 采购冲销适配兼容性

`services/purchase_native_repost.py` 的 `PINS` 校验原生库存重算和总账源码，随后校验实际函数身份。
`purchase_repost_boundary.before_execution` 在 HTTP 请求及后台任务入口安装适配器，版本不匹配也可能使
`/api/method/ping` 返回 417 和“采购冲销证据尚未完成”，不能仅凭该提示判断存在未完成冲销单据。

当前总账指纹对应 ERPNext 提交 `b0d56f2f48`：仅为 `make_reverse_gl_entries` 的冻结检查补齐凭证公司，
并明确传递 `adv_adj`，适配器包装的函数及接口未改变。发布时必须同时核对 ERPNext 与本 App 的实际源提交；
不可删除校验、直接采用未经审核的哈希或改写冲销记录来恢复访问。

开发 bench 中可运行不连接站点数据库的兼容性回归：

```bash
env/bin/python -m unittest deeplinkerp_branding.tests.test_purchase_native_repost_compatibility -v
```

该回归使用真实已安装原生模块，检查重复安装、捕获函数身份及未知源码/运行时函数变更拒绝；
不代替数据库、真实出库/冲销或生产 HTTP 验收。

### 标准表单行间新增

`public/js/form_grid_insert.js` 和同名 CSS 通过 `hooks.py` 的 Desk 公共资源加载，在标准表单允许新增的子表行下方提供“＋”。鼠标经过、键盘聚焦或触屏操作时可见；点击在该行之后插入，原行保留、后续序号顺延，新行自动进入编辑。底部原生 Add row 显示为“末尾新增”，继续使用原生追加逻辑。

行间插入复用 Frappe 子文档创建及 `<子表字段>_add` 事件，并等待事件完成。Journal Entry 的 `accounts` 新行借贷及本位币金额归零、科目和往来留空、汇率恢复初始值，摘要沿用所点击行；成本中心等维度保留原生默认规则。其他单据继续使用自己的新增事件和默认值。插入仅修改当前表单，保存、核对和记账仍走原流程。

权限、表格只读、静态行、禁止新增、选中行及正在保存状态继续生效；新增前等待当前输入的原生异步校验，重复点击合并为一次。分页按实际子文档位置定位；有本表筛选时插入后清除该筛选并提示，以免空行被隐藏。业务新增事件报错时撤回新建子行并恢复行号；如果新增已完成但页面显示失败，则保留并明确提示核对单据。弹窗、Web Form、报表以及期初余额等自定义表格不接入这项标准表单扩展。

在包含 Frappe/ERPNext 源码的开发 bench 中可执行 `node --test tests/form-grid-insert.test.js`，用真实原生排序和凭证新增处理器核对行序、金额、权限及异步边界。该测试不连接数据库；生产发布后仍需在目标站点确认界面资源和各业务自定义新增规则。

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/deeplinkerp_branding
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade
### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI：在推送到 `main` 和创建 PR 时安装应用并运行测试。
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### License

mit
