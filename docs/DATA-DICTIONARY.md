# 定义契约 freightrate

## 数值

| 列 | 类型 | 量化 | 可空 | 说明 |
|---|---|---|---|---|
| weight_kg | Decimal | 0.001 kg | 否 | 单件实重，输入单位 g/kg/t 都换算到 kg |
| length_cm / width_cm / height_cm | Decimal | 0.001 cm | 否 | 单件尺寸，输入单位 cm/m/mm 都换算到 cm |
| volumetric_kg | Decimal | 0.001 kg | 否 | 单件：长×宽×高 ÷ volume_divisor |
| chargeable_kg | Decimal | 0.001 kg | 否 | 单件取大值 max(实重, 体积重)；票级为逐件取大值之和 |
| billable_kg | Decimal | 0.5 kg 向上 | 否 | 整票：逐件取大值累加后按台阶进位一次 |
| first_price / step_price / min_charge | Decimal | 0.01 元 | 否 | 运价表金额 |
| fuel_pct / insurance_pct | Decimal | 小数 | 否 | 8.5% 与 0.085 两种写法都归到小数 |
| declared_value | Decimal | 0.01 元 | 是 | 空值与哨兵都按 0 处理，表示未保价 |
| total_amount | Decimal | 0.01 元 | 否 | 由分项求和得出，不单独舍入 |

金额与用量一律 Decimal，对外 JSON 用字符串。禁止 float 累加。

## 票与件

- 票（shipment）：同一运单号的全部采信行；票级字段各件一致，首重、进位、账单都按票出一次
- 件（piece）：一行采信记录一件，`piece_seq` 是票内件号（按原始行号顺序编）
- `waybills` 表一件一行（主键 run_id + line_no），`piece_lines` 存逐件计费重依据，
  `weight_lines` 与 `charge_lines` 一票一行

## 单位

单位表只在 `app/config.py` 定义一份。重量 g=0.001 kg、t=1000 kg；尺寸 m=100 cm、mm=0.1 cm。
带单位后缀的单元格（如 3200g）先剥后缀，单位列非空时以单位列为准。

## 时间

- 存储：UTC，ISO 文本，秒精度，带 Z 后缀
- 输入：接受 Z、+hh:mm、无偏移三种写法；无偏移按 +08:00 解释
- 展示：页面显示 Asia/Shanghai，生成时间与批次时间都带时区名
- 运价版本窗口：`effective_from` 与 `effective_to` 也是 UTC，区间半开 [from, to)

## 空值

- `null` / 空串 / 哨兵 三者语义分开
- 哨兵清单：`-`、`--`、`/`、`N/A`、`NA`、`NULL`、`NONE`、`未知`、`无`、`空`
- 重量与尺寸出现哨兵即进隔离区；声明价值出现哨兵按未保价处理

## 半开区间

运价版本用 `[effective_from, effective_to)`：提货时刻等于 from 命中该版，等于 to 落到下一版。
相邻版本首尾相接时不重不漏。

## 去重键与多件

业务键是运单号 `waybill_no`。同一运单号出现多行时按载荷分三种情况：

- 票级字段（承运商、服务、分区、提货时刻、声明价值）一致、逐件测量（重量、三边尺寸）
  不同：同一票的多件，全部保留并编件号，各行标 MULTI_PIECE，逐件参与计费
- 票级字段与逐件测量完全一致：重复记录，记 DUPLICATE_WAYBILL，只保留首条
- 票级字段冲突：无法并成一票，记 DUP_CONFLICT，只保留首条

`pieces` 列是托运人申报件数，只做展示不参与判定；逐件证据以采信行为准。

## 空态

- 0 字节文件：拒绝采信，返回 EMPTY_FILE
- 只有表头：采信成功但 rows_in 为 0，页面显示空态
- 全是空行：empty_rows 计数，rows_in 为 0

## 原因码

原因码只在 `app/reasons.py` 定义，分两级：`block` 表示该行不进计费，`info` 表示只做标记。
每条隔离都带原始行号与原因码，可导出 CSV 复核。
