### DeeplinkERP Branding and Shared Customizations

DeeplinkERP 的品牌、导航和跨业务通用定制。目前包含四个库存明细页面、库存快照及标准物料移动单预填入口。

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
