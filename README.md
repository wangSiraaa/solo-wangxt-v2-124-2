# 材料实验室 · 拉伸试验载荷-位移分析系统

从试验机的**载荷 / 夹具位移 / 引伸计**原始记录计算弹性模量 E、0.2% 偏移法
条件屈服强度 Rp0.2、抗拉强度 Rm、断后伸长率 A、断面收缩率 Z，并在
Angular + Plotly.js 界面上展示工程/真实应力-应变曲线、弹性拟合残差与
区间影响，由 FastAPI 调用 SciPy/NumPy 完成拟合，PostgreSQL 保存试样尺寸、
设备参数、原始信号与计算方案。

## 设计约束（对应核对要求）

| 要求 | 实现位置 |
| --- | --- |
| 夹具位移与引伸计读数不能混为一列 | `raw_signals` 表按 `kind` 分行（`load` / `crosshead_displacement` / `extensometer_displacement`），各自带单位与 SI 副本；计算时由 `strain_source` 显式选择，标距分别为平行段 Lc / 引伸计标距 Le |
| 工程/真实应力应变分别处理，颈缩后不假装换算 | `mechanics/curves.py`：颈缩起点取最大工程应力点（载荷峰值），其后 `true_stress = null`、`true_stress_valid=false`，前端真实曲线在该处断开 |
| 选弹性区时看到拟合残差及区间影响 | `elasticity.py` 返回逐点残差、R²、RMSE、最大残差，并对区间做收缩/扩展 4 种敏感性计算；前端有残差图与区间影响表 |
| 0.2% 偏移法使用明确单位 | 偏移量 `OFFSET_YIELD_STRAIN = 0.002`（mm/mm），偏移线 σ = E·(ε−0.002)；内部 SI(Pa)，输出单位由用户选 MPa/GPa/Pa |
| 合成线弹性案例 | 无交点时 `found=false`，明确说明“曲线始终位于偏移线上方”，绝不编造屈服值；测试断言 E≈200 GPa 且 Rp0.2 缺失 |
| 无清晰屈服案例 | 交点附近局部刚度/E > 0.6 时 `yield_unclear=true`，界面标注“仅条件屈服值” |
| 尺寸缺失案例 | 缺 d0/b0/t0 直接 422 并给出中文原因；缺断后尺寸时 A/Z 为 null 并列入 `missing_inputs` |
| 原始信号保留 | 分析与排除只写入 `analyses.params`（排除点**必须带 reason**，空原因被 Pydantic 拒绝），`raw_signals.values/si_values` 永不回写 |
| 机器柔度修正 | 夹具位移可输入 `C`（m/N、mm/N、mm/kN、µm/N），逐点计算 `δcorr=(D−D0)−C(F−F0)`；修正曲线独立返回并用于 E、屈服与报告，原始夹具/引伸计曲线同时保留。负修正位移或载荷增加而位移回退时返回中文 422，拟合和入库均不执行 |
| 报告不能只有孤立数值 | `mechanics/report.py` 输出方法、区间、点数、R²/残差、区间敏感性、单位、缺失输入、颈缩规则、完整 provenance |

## 三个核对案例（信号空间合成，全链路可复算）

* `linear_elastic` — 纯线弹性（E=200 GPa），加载到 0.5% 应变终止；
  期望：E 恢复误差 <0.1%，R²≈1，**Rp0.2 无交点**。
* `no_clear_yield` — 0.1% 应变后刚度仅降至 0.75E；期望：有交点但
  `yield_unclear=true`（Rp0.2≈1400 MPa）。
* `clear_yield` — 弹性 + 屈服拐点（400 MPa）+ 1.5 GPa 硬化 + 峰值后跌落；
  期望：Rp0.2≈403 MPa、A=16%、Z=64%，颈缩后真实应力缺失。

夹具位移信号额外注入机器柔度位移，因此未校准修正时用它算模量会系统性偏低
（实测 200 GPa → ~91 GPa）；输入合成数据的已知校准值（约 6.11155 mm/kN）后，
修正曲线恢复约 200 GPa，原始夹具/引伸计信号仍保持不变。

## 运行

### 后端（开发默认 SQLite）

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
# 生产/PostgreSQL：
# export DATABASE_URL=postgresql+psycopg://lab:lab@localhost:5432/matlaboratory
```

### 前端

```bash
cd frontend
npm install
npm start          # http://localhost:4200，/api 代理到 8000
```

### 一键 PostgreSQL + API

```bash
docker compose up --build
```

### 测试

```bash
cd backend && ../.venv/bin/python -m pytest -q
# 13 passed：8 个计算内核 + 5 个端到端 API（含 422 与原始信号不可变校验）
```

## 界面操作

1. 左侧选择案例（可勾选“尺寸缺失”）→ **加载案例**；
2. 选择应变来源与应力单位；使用夹具位移时可勾选机器柔度修正并填写校准系数/单位；
3. 在曲线上**横向框选**弹性区间（或手填 ε 上下限）；
4. **单击修正后工程曲线点**可人工排除，必须填写原因；
5. **SciPy 拟合/计算**后查看 E、残差图、区间影响表、Rp0.2（绿色偏移线）、
   颈缩竖线及真实曲线缺口；
6. **生成溯源报告**得到完整 Markdown 并入库（`reports` 表）。

## 目录

```
backend/app/
  mechanics/curves.py       # 通道分离、工程/真实曲线、颈缩判定
  mechanics/elasticity.py   # OLS 模量、残差、区间敏感性
  mechanics/yield_.py       # 0.2% 偏移法（含无交点/无清晰屈服）
  mechanics/fracture.py     # Rm、A、Z 与缺失输入
  mechanics/synthetic.py    # 三个合成案例（载荷-位移空间）
  mechanics/report.py       # 溯源 Markdown
  routers/api.py            # FastAPI 路由
  models.py                 # SQLAlchemy（PG JSONB / SQLite JSON）
frontend/src/app/           # Angular standalone + Plotly.js
```

## 已知边界

* 颈缩后真实应力仅标记缺失，未实现 Bridgman / 修正本构反演；
* 矩形截面 Z 需录入断后截面积（当前仅圆截面由断后直径计算）；
* 引伸计摘除后的应变不能接续到夹具位移，检测到卸载/回退即截断并警告。
