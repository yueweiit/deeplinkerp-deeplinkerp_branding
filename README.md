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
