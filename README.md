# 数据服务开放与订阅管理平台

面向数据API注册、查询路由、订阅审批、用量计量与SLA保障的企业数据服务开放平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/views/SubscriptionUsageView.vue  订阅账期用量明细（换订阅追溯）
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/subscription_usage.py  换订阅追溯只读接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/subscription_usage.py  订阅账期用量明细追溯
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| API注册 | `api_registry` | 数据API | API编号、API名称、接口路径 |
| 查询路由 | `query_route` | 路由规则 | 规则编号、关联API、路由条件 |
| 订阅管理 | `subscription` | 订阅申请 | 申请编号、订阅API、申请方 |
| 频率限制 | `rate_limiter` | 限流策略 | 策略编号、目标API、调用方 |
| 用量计量 | `usage_billing` | 用量记录 | 记录编号、调用方、API名称 |
| 缓存分层 | `cache_layer` | 缓存策略 | 策略编号、关联API、缓存层级 |
| 数据过滤 | `data_filter` | 过滤规则 | 规则编号、关联API、过滤字段 |
| 字段脱敏 | `field_mask` | 脱敏规则 | 规则编号、目标字段、脱敏算法 |
| SLA监控 | `sla_monitor` | SLA指标 | 指标编号、关联API、可用率目标 |
| 结果格式化 | `result_format` | 格式模板 | 模板编号、关联API、输出格式 |
| 查询优化 | `query_optimize` | 优化规则 | 规则编号、关联API、索引建议 |
| 异常归类 | `error_classify` | 异常类型 | 类型编号、类型名称、错误码段 |
| 数据归并 | `data_merge` | 归并规则 | 规则编号、主API、从API |
| 导出任务 | `export_task` | 导出任务 | 任务编号、关联API、查询参数 |
| 调用审计 | `call_audit` | 调用记录 | 记录编号、调用方、目标API |
| 版本兼容 | `version_compat` | 版本映射 | 映射编号、关联API、旧版本 |
| 批量查询 | `batch_query` | 批量任务 | 任务编号、关联API、查询清单 |
| SDK配置 | `sdk_config` | SDK配置 | 配置编号、关联API、SDK语言 |

## 订阅账期用量明细（换订阅追溯）

已出账用量过去只能按账期翻账，遇到同一账期内同一 API 先后生效两份订阅时，
月底账单会按调用方合并成一行，财务看不出换订阅影响了哪些用量。新增只读追溯页
（前端 `/subscription-usage`）解决这条链路：

1. 按账期列出已出账原账单，保留原账单金额、调用次数和账单状态；
2. 结合订阅生效区间与订阅变化记录，还原账期内先后生效的订阅段；
3. 用原始用量明细切出每段订阅的命中区间、命中调用量，并按原始调用量
   分摊原账单金额（分摊合计恒等于原账单金额）；
4. 按订阅单价重算后给出差额和调整方向，用原用量记录判断能否发起更正；
5. 更正只登记申请单，不改写原账单，也不生成第二笔收费。

接口（只读）：

| 接口 | 说明 |
| --- | --- |
| `GET /api/subscription-usage/bills?billing_cycle=2026-10` | 按账期/调用方/API 列出账单，`changed_only` 只看换订阅 |
| `GET /api/subscription-usage/trace?billing_cycle=2026-10&caller=陈嘉禾` | 按账期追溯换订阅明细 |
| `GET /api/subscription-usage/trace?bill_id=4` | 打开原账单后按账单编号回查，仍为只读 |
| `GET /api/subscription-usage/details?application_no=SUBS-0012` | 原始用量明细，按订阅申请定位 |
| `GET /api/subscription-usage/changes?billing_cycle=2026-10` | 订阅变化记录 |
| `GET/POST /api/subscription-usage/corrections` | 差额更正申请查询/登记 |

示例场景（种子数据，账期 `2026-10`，`订单明细查询API`）：

- 陈嘉禾：10-01~10-15 基础套餐 1800 次 × 0.20 元，10-16~10-31 增强套餐
  1200 次 × 0.25 元；月底合并出账 600.00 元，按订阅单价重算 660.00 元，
  差额 -60.00 元（少收），命中区间 `2026-10-16~2026-10-31`，可发起差额补收更正。
- 林墨：整月单段订阅 1200 次，原账单 240.00 元与重算一致，不判为换订阅、
  不能发起更正。

追溯全程只读：不新增账单、不复制用量、不改原金额；每次查询记录一条
`charge_delta = 0` 的追溯日志，证明检索拆分没有构成二次收费。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
