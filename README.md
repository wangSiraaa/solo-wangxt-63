# 街道环卫考核 API

Django REST Framework + Pillow（感知哈希）+ PostgreSQL/PostGIS 的环卫考核后端。
核心目标：**同一现场问题不被重复扣分，相似照片中的不同地点不被错误合并**；
每笔扣分都能追到唯一处罚单元与证据照片。

## 业务规则

| 规则 | 实现 |
| --- | --- |
| 哈希只生成疑似重复候选 | `Photo` 保存 sha256 + Pillow dHash；`DuplicateCandidate` 仅提示，不做任何自动合并 |
| 事件关联按位置、时间、人工判断 | 确认候选/人工关联必须通过 `≤150m`、`≤72h` 合理性校验，且目标事件未整改、未作废，否则 `409` |
| 同一问题多角度不重复计扣 | 确认合并后目标事件保留唯一有效 `PenaltyUnit`，被合并事件及其处罚作废留痕 |
| 整改后复发是新事件 | 已整改事件禁止被关联/合并（`409`），新照片自动开新事件与新处罚 |
| 扣分归属按事件发生时合同区间 | `PenaltyUnit.contract` 由 `occurred_at` 落入的责任区间 `[valid_from, valid_to)` 决定，与录入时间无关 |
| 逾期升级基于可注入时钟 | `assessment.clock` 支持 `freeze_time`；`POST /api/escalations/run/` 按逾期天数追加版本，同一逾期天数重复执行幂等 |
| 复核锁定、更正只能追加 | `approve-review` 锁定当前 `PenaltyVersion`；锁定版本模型层拒改，`correct` 只能追加新版本 |
| 整改回调幂等 | `idempotency_key` 唯一；重复回调返回原记录（200），不重复改状态 |

## 目录结构

```
config/               Django 项目配置（PostGIS 后端、drf-spectacular）
assessment/
  clock.py            可注入时钟
  hashing.py          Pillow dHash（64 位感知哈希）与汉明距离
  models.py           网格/合同/事件/照片/候选/整改回调/处罚单元/处罚版本
  services.py         全部业务规则（合并、驳回、回调、升级、复核、更正）
  views.py / urls.py  DRF API
  tests/              8 个场景测试
scripts/
  env.sh              环境变量（GDAL/GEOS 库路径、数据库连接）
  pg_start.sh         启动用户态 PostgreSQL+PostGIS
  make_mock_images.py 生成模拟现场照片到 mock_images/
mock_images/          模拟照片（同图副本、换角度变体、不同场景）
openapi.yaml          OpenAPI 3.0 规范（GET /api/schema/ 同源）
```

## 运行

```bash
source scripts/env.sh                 # 载入库路径与数据库连接
bash scripts/pg_start.sh              # 启动 PostgreSQL（首次自动 initdb + PostGIS）
python manage.py migrate
python manage.py seed_demo            # 可选：载入演示数据（走完整业务流）
python manage.py runserver
```

本环境中 PostgreSQL 15 + PostGIS 3.3 以用户态方式运行在 `127.0.0.1:54329`
（`.pgroot/` 为解包的 Debian 二进制，`.pgdata/` 为数据目录）；生产部署时
把 `DATABASES` 指向标准 PostGIS 实例即可。

## 测试

```bash
source scripts/env.sh
python manage.py test assessment
```

覆盖场景：

- `test_dedup.py` — 同图跨地点误传（精确指纹命中但距离 26km，确认被拒，误传作废，只剩一条有效扣分）；同一问题换角度（哈希相近 → 候选 → 人工确认合并 → 单条处罚、双证据）；相似照片不同地点禁止合并
- `test_recurrence.py` — 整改后同地点复发开新事件新处罚；重复整改回调按幂等键只处理一次，异键回调 409
- `test_penalties.py` — 扣分按发生时合同区间归属（甲/乙换班）；逾期升级随注入时钟逐日追加且幂等，锁定后仍只追加；复核锁定 + 追加式更正 + 全链路证据追溯

## API 一览

```
POST /api/photos/                          上传证据照片（multipart: image, taken_at, lon, lat, category）
POST /api/photos/{id}/link/                人工关联到既有事件（合理性校验）
GET  /api/candidates/                      疑似重复候选列表
POST /api/candidates/{id}/confirm/         确认同一问题 → 合并事件、作废多余处罚
POST /api/candidates/{id}/reject/          确认非同一问题（可作废误传照片）
POST /api/events/{id}/rectification-callback/  整改回调（幂等键）
GET  /api/penalties/{id}/                  处罚单元详情（版本链 + 证据）
POST /api/penalties/{id}/approve-review/   复核通过 → 锁定当前版本
POST /api/penalties/{id}/correct/          追加更正版本
POST /api/escalations/run/                 触发一轮逾期升级
GET  /api/schema/                          OpenAPI 3.0
```
