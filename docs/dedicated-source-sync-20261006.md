# 专用来源同步

只执行既有 `operating_expenses.scheduled_sync` 和 `purchase_source_service.scheduled_sync`。宿主机 user timer 每个整点、15、30、45 分触发；两项任务串行、各自一个容器进程，单项总预算 300 秒。运营支出逐页提交缓存与游标，未完成页失败回滚；采购沿用既有 300 秒 Redis 锁及整个扫描的单事务。两项既有启用开关和手动同步保持原用途。

System Settings.enable_scheduler 必须保持 0；不能开启其余自动任务。执行前用 Frappe 原生无缓存读取重新合并 common/site 配置，检查维护模式、scheduler_disabled 和无缓存全局开关，运营支出每页重新检查；无效配置安全拒绝且不输出配置内容。两条固定原生 RQ job 任一 QUEUED/STARTED 均拒绝执行或 metadata 安装，不改变 RQ 状态。只允许两项固定方法，内部 runner 和日志安装函数均不提供 HTTP 白名单接口。业务分类、China/2026 范围、流程代码、原生订单/付款/凭证操作没有新增规则。

## 安装与启动

正常 image overlay 发布完成后，单独暂存 `deploy/production/dedicated_source_sync.py` 与 `systemd` 下的两份模板。使用既有 `yuewei` 用户；不需要 sudo。提供已经核验的完整 branding SHA、backend image ID 和安装后 runner 的 SHA-256：

```sh
python3 deploy/production/dedicated_source_sync.py install --revision <SHA> --image-id <sha256:IMAGE> --runner-sha256 <RUNNER_SHA256>
```

默认只为**原有两条** Scheduled Job Type 打开 create_log、保存原始值的持久证据、暂存 user units，timer 保持停止。写 create_log 前必须确认两条原生 job 均空闲，启动 timer 前再次核查。缺少、重复、脚本化、stopped 或 cron 不符的 job 会拒绝安装，不做 broad migration/sync_jobs。先采集部署所需的 `before-timer.json`，再用同样参数加 `--start-timer`；该命令不会手动执行任务，随后由日历的下个时点触发。`Persistent=false` 不补跑停机错过的轮次。

配置、原始 create_log 证据和当前运行记录存于 `/home/yuewei/.local/state/deeplinkerp-source-sync`；启动器独立保存于 `.local/share/deeplinkerp-source-sync/launcher.py`，不依赖临时发布目录。重复安装保留首次原始 create_log 证据。

身份核验同时固定实际运行的 immutable image ID、镜像内 `app-revisions.json` 的 `deeplinkerp_branding` 完整提交和容器 runner SHA-256。旧 `org.deeplinkerp.branding.revision` 标签可以缺省；若存在则必须与上述提交一致。缺失、无效或冲突的应用提交元数据仍拒绝执行，不能只换 image pin 或跳过版本核验。宿主工具来自独立仓库 `yueweiit/deeplinkerp-deeplinkerp_branding` 的已评审提交；`--revision` 指当前运行应用的提交，不是新宿主工具自身的提交。

## 故障与回滚

原生 Scheduled Job Log 记录 Start、Complete、Failed，creation/modified 及 last_execution 保留原生语义。runner 检查实际终态，Failed 的 details/debug 在保存前去除来源内容。运营支出错误提示单独提交，只写 last_error，避免覆盖手动同步刚更新的游标。

容器内 SIGALRM/SIGTERM 中断实际 Python 子进程；宿主机另有 30 秒清理宽限。超时先验证私有 PID marker 中的 PID 启动时间，再终止实际容器进程，必要时 SIGKILL 使未提交事务随数据库连接关闭回滚，随后修复对应唯一日志。终止或日志恢复无法确认时保留运行记录、停止后续任务；下一次启动先恢复它。不会只杀 docker 客户端后假报完成。

```sh
python3 deploy/production/dedicated_source_sync.py rollback
```

回滚先停用 timer 并停止 service，再取得互斥锁、核对已安装 image/revision/checksum、恢复证据中两项 create_log。来源缓存、付款证据、游标、原生日志均不删除。回滚 timer/日志应在回滚应用镜像之前进行。

## 后续 ERP 发布

发布前必须在**当前已核验镜像**上先用已安装 launcher 执行 `rollback`：停用 user timer、停止 service，并按首次安装证据恢复两项 `create_log`。仅暂停 timer 不够，原发布安装器会严格比对完整 Scheduled Job Type 定义，本次首次记录的两项日志初值均为 0；不要放宽校验或直接改写固定值。此操作不删除缓存、游标或原生日志，安装证据也保留，供新版本再次安装。

发布脚本和启动器复用 `/tmp/deeplinkerp-erp-release.lock`，同步另有自身运行锁，防止发布切换与同步重叠。只有定时 run 抢锁失败可返回 Skipped/exit 0；install/rollback 抢锁失败返回 Failed/非 0，控制动作不可误认成功。新镜像切换后按新 image ID、branding revision 和 runner checksum 重新 staged install，验收后显式启用 timer。旧 identity 会拒绝执行，不能直接重启旧配置绕过核验。

### Finance-only 发布的原任务接续

Finance-only 发布不重新安装上述原生 Scheduled Job Type。即使 Branding 源码完全未变，新镜像 ID 也必须通过现有 `retarget` 入口重新绑定，不能沿用旧配置；也不为此重新执行 `install`、重置日志或清空队列。

1. 发布前保留当前 timer 是否 active、`config.json`、完整 `installation.json`、`run.json` 和两条固定 job 元数据。没有已安装证据、账本存在未确认活动任务或配置与 receipt 不符时停止处理。
2. 等待原 Finance 发布流程成功退出，再核对最终服务镜像、`app-revisions.json` 和实际 runner 校验和。必须先释放该发布流程持有的 ERP/source/bench 锁，再调用 `retarget`；不要在继承这些锁的 wrapper 子进程中重新开锁，也不要绑定中间构建镜像。发布失败时不执行接续步骤。
3. 仅暂存已评审的宿主工具文件，复用原控制入口：

   ```sh
   python3 <已评审的 dedicated_source_sync.py> retarget \
     --revision <最终镜像内的 Branding SHA> \
     --image-id <最终 immutable image ID> \
     --runner-sha256 <最终容器内 runner SHA-256> \
     --start-timer
   ```

   `--start-timer` 仅用于发布前已经 active 的既有 timer；此前未运行则省略。保留原 backend container，不能通过改 container 扩大到其他任务。`retarget` 自身取得原两把锁、复查运行身份与旧安装证据，原子更新 host launcher/config/receipt，保留首次 `logging_before`，不调用原生日志安装、取消、恢复或业务任务。
4. 接续后只读确认新配置与 receipt 一致、日志初值证据未变、全局 scheduler 仍为 0、两项业务启用开关及原生 job 定义未变。等待下一个正常日历轮次，检查两条新的原生日志与来源更新；不得把 `Complete` 单独当作下游采购草稿已生成的证据，应另抽样核对既有来源资格、关联和 `docstatus=0`。

此接续只恢复原有 15 分钟任务及其既有业务行为，不另外补跑历史脚本、修改范围规则、启用全局 scheduler 或提交业务单据。每次后续 Finance-only 镜像发布都重复相同的最终身份核对和 `retarget`，包括仅修改 Finance 而 Branding 提交未变化的情况。宿主 focused 回归覆盖无标签的分层镜像、冲突标签/应用提交/旧镜像/runner 校验拒绝，以及同一 Branding 提交换镜像后的接续与日志证据保留。

## 验证与范围

CI 增加宿主机 focused unittest；原有 app pytest 自动包含两个新测试文件。真实隔离站点验证命令：

```sh
docker exec --workdir /home/frappe/frappe-bench dlp-operating-expenses-qa-backend-1 /home/frappe/frappe-bench/env/bin/python /workspace/deploy/local/test_expense_source_scheduler_native_qa.py -v
```

该 helper 限定既有合成 QA 站点，外部来源用合成读取替换；验证真实原生日志、超时、分页提交/失败回滚/恢复、仅两项 metadata、原生单据和主数据数量不变。只恢复其自身设置、删除其准确命名的合成缓存/日志，不操作生产。

本轮不增加业务 API、模型、并行规则引擎或自动清理任务。原生日志以及私有 PID marker 的长期保留/清理政策仍属未处理技术债；现有原生日志清理也随全局 scheduler 停用。采购未新增分页 checkpoint，失败仍按既有单事务回滚并重试。

## 只读查询的性能边界

共用 OA 查询先限定一页，再对该页涉及的业务号做一次全源查重；`counts AS MATERIALIZED` 阻止全源统计按每行重复执行。全源查重不能套用年份、公司或模板过滤，否则会漏掉范围外的同号申请。

运营路径只需申请类型、执行地区、金额、币种、事项说明、收款人、付款日期七类组件的 name/value。查询保留所有匹配组件及其顺序、原始类型、重复、null/空/0；包含式中文前缀预筛选故意宽于 Python 的 strip/startswith，以避免 Unicode 空白漏项。非数组表单原样返回；仍由既有 Python 解析与规范化作最终判定。采购路径保留完整表单，尤其 TableField 明细和 original_fields，不共享运营的精简投影。

生产只读 A/B 在同一冻结时间比较两页共 954 个读取记录（含一条分页预读），字段、范围判定、规范化来源、版本指纹和其他来源列相同。首页表单传输量由 3,540,585 降至 381,319 字节，尾页由 3,218,319 降至 341,745 字节。另有 15 个合成 JSON 的真实 PostgreSQL 边界对比通过。诊断均未提交数据库写入；这些比较不替代发布后的自然定时验收，也不证明网络没有偶发波动。

以 N 为全源查重行数、S 为本轮符合 SQL 范围的候选行数、P≤501 为分页含预读行数、C 为每行表单组件数，B/D 分别为分页原始/投影 JSON 的字节量。查询保留一次全源聚合、范围排序和有序组件聚合：哈希执行计划下时间 O(N + S log S + P·C log C + B + D)，空间 O(N + B + D)，不能把很大的字段值当作常数忽略。原先按分页行重复全源聚合的 O(P·N) 被删除；仅缩小传输，不降低查重范围、分页规模或 10 秒查询保护。数据库实际执行计划仍须现场验证，不能把 SQL 写法当作固定运行时间保证。
