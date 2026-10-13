"""
BC白酒营销助手 — Vanna 训练脚本
==================================
将以下内容写入 ChromaDB 向量库，让 Vanna 学会白酒营销业务：

  1. DDL        → 建表语句（字段含义、分区键说明）
  2. 业务文档   → 关键业务规则（分区裁剪、中奖率、活动类型等）
  3. Q-SQL 对   → 8 条 Few-shot 示例（问题 + 标准 SQL）

训练完成后生成 chroma_db/ 目录，后续 app.py 直接加载无需重复训练。

使用方法：
  python train_vanna.py          # 首次或需要重新训练时执行
  python train_vanna.py --reset  # 清空向量库重新训练

作者：Jason
"""

import sys
import time

from bc_vanna import get_vanna

# ── DDL：完整建表语句（含字段中文注释）──
DDL = """
CREATE TABLE lj_consumer_scan (
    scan_time           DATETIME         COMMENT '扫码时间（分区键，查询必须带 scan_time 范围条件）',
    product_id          VARCHAR(50)      COMMENT '产品ID',
    province            VARCHAR(50)      COMMENT '省',
    city                VARCHAR(50)      COMMENT '市',
    district            VARCHAR(50)      COMMENT '区',
    from_dealer_id      VARCHAR(50)      COMMENT '来源经销商ID',
    from_dis_id         VARCHAR(50)      COMMENT '来源分销商ID',
    supplier_id         VARCHAR(50)      COMMENT '供货商ID',
    is_winner           VARCHAR(10)      COMMENT '是否中奖，Y=中奖，N=未中奖',
    prize_name          VARCHAR(100)     COMMENT '奖品名称',
    prize_amount        VARCHAR(50)      COMMENT '奖品金额',
    PRIZE_PROD_NUM      INT              COMMENT '再来一瓶实物数量',
    camp_type           VARCHAR(50)      COMMENT '活动类型，如：扫码领红包、再来一瓶',
    ACTIVITY_NAME       VARCHAR(200)     COMMENT '活动名称',
    camp_code           VARCHAR(50)      COMMENT '活动编码',
    differ_area_scan    VARCHAR(10)      COMMENT '是否异地扫码，Y=是，N=否',
    interval_days       INT              COMMENT '间隔天数',
    writeoff            VARCHAR(10)      COMMENT '是否核销产品，Y=已核销，N=未核销',
    open_serial_number  INT              COMMENT '开瓶抽奖序号',
    acct_id             VARCHAR(50)      COMMENT '客户ID',
    acct_type           VARCHAR(50)      COMMENT '客户类型',
    status              VARCHAR(20)      COMMENT '处理状态',
    BOTTLE_CODE         VARCHAR(100)     COMMENT '瓶码',
    CASE_CODE           VARCHAR(100)     COMMENT '盒码',
    ROOT_CODE           VARCHAR(100)     COMMENT '箱码',
    box_code            VARCHAR(100)     COMMENT '箱码（处理后）'
) COMMENT='消费者开瓶扫码主表，按月分区，分区键为 scan_time';
"""

# ── 业务文档：关键规则 ──
BUSINESS_DOCS = [
    """
【分区裁剪规则（极其重要）】
lj_consumer_scan 表按 scan_time 月份分区。
所有查询必须包含 scan_time 的范围条件，格式为：
  WHERE scan_time >= '2025-01-01' AND scan_time < '2025-02-01'
禁止使用函数包裹分区键，例如：
  WHERE YEAR(scan_time) = 2025        -- 错误！会导致全表扫描
  WHERE DATE_FORMAT(scan_time, '%Y') = '2025'  -- 错误！
必须使用明确的日期范围：
  WHERE scan_time >= '2025-01-01' AND scan_time < '2026-01-01'  -- 正确（全年）
""",
    """
【中奖率计算方法】
is_winner 字段：Y=中奖，N=未中奖
中奖率 SQL 写法：
  ROUND(SUM(CASE WHEN is_winner = 'Y' THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS win_rate_pct
""",
    """
【异地扫码分析】
differ_area_scan 字段：Y=异地扫码，N=本地扫码
异地扫码是指消费者在与产品发货地不同的省份扫码，可能反映串货情况。
异地扫码率 = SUM(CASE WHEN differ_area_scan = 'Y' THEN 1 ELSE 0 END) / COUNT(*)
""",
    """
【核销分析】
writeoff 字段：Y=已核销，N=未核销
核销通常针对"再来一瓶"活动，核销率反映奖品兑现情况。
核销率 = SUM(CASE WHEN writeoff = 'Y' THEN 1 ELSE 0 END) / COUNT(*)
""",
    """
【活动类型（camp_type）常见值】
- 扫码领红包：消费者扫码即获得微信红包
- 再来一瓶：扫码中奖后可兑换一瓶同款白酒
- 积分兑换：扫码累积积分，换取礼品
按活动类型分析时，使用 GROUP BY camp_type
""",
    """
【常用聚合分析维度】
- 时间维度：DATE_FORMAT(scan_time, '%Y-%m') 按月，DATE_FORMAT(scan_time, '%Y-%u') 按周
- 地域维度：province（省）、city（市）、district（区）
- 产品维度：product_id
- 渠道维度：from_dealer_id（经销商）、from_dis_id（分销商）
- 活动维度：camp_type、ACTIVITY_NAME、camp_code
""",
    """
【数据库信息】
数据库名：vanna_demo
主表：lj_consumer_scan
MySQL 版本：8.0
支持窗口函数，如：
  COUNT(*) OVER() 求总数
  ROUND(COUNT(*) / SUM(COUNT(*)) OVER() * 100, 2) AS percentage  -- 占比计算
""",
    """
【lnk_accnt 客户基本表结构】
lnk_accnt 是客户（终端/经销商/分销商）的主档信息表，共约 330 万条记录。

核心字段：
  row_id      VARCHAR(30)  客户系统主键（18位数字字符串），与 lj_consumer_scan.acct_id 关联
  ACCT_CODE   VARCHAR(30)  客户编码（业务单号，如 A001011...），不能用于关联扫码表
  ACCT_NAME   VARCHAR(200) 客户名称（终端门店名、经销商名）
  ACCT_TYPE   VARCHAR(50)  客户类型，枚举值：Terminal（终端）/ Dealer（经销商）/ Distributor（分销商）/ Supplier（供应商）
  ACCT_STATUS VARCHAR(30)  客户状态：Y=活跃，N=停用，CANCEL=注销，Retreated=撤点
  ACCT_LEVEL  VARCHAR(50)  客户等级：levelA / level2A / level3A / belowA / levelB / NormalCatering 等
  PROVINCE    VARCHAR(100) 省
  CITY        VARCHAR(100) 市
  DISTRICT    VARCHAR(100) 县区

关联方法：
  lj_consumer_scan.acct_id = lnk_accnt.row_id  （99.8% 匹配率，唯一正确的关联键）
  注意：lj_consumer_scan.acct_id 与 lnk_accnt.ACCT_CODE 不匹配，禁止用 ACCT_CODE 关联！

典型 JOIN 写法：
  FROM lj_consumer_scan s
  LEFT JOIN lnk_accnt a ON s.acct_id = a.row_id
  WHERE s.scan_time >= '2025-01-01' AND s.scan_time < '2026-01-01'
""",
    """
【客户类型（acct_type / ACCT_TYPE）枚举值说明】
lj_consumer_scan.acct_type 与 lnk_accnt.ACCT_TYPE 枚举值相同：
  Terminal    终端（餐饮、烟酒店等零售终端）  — 占扫码记录约 49%
  Dealer      经销商                          — 占扫码记录约 8%
  Distributor 分销商                          — 占扫码记录约 4%
  NULL        未知/未登记客户                  — 占扫码记录约 39%

在 lnk_accnt 中：
  ACCT_STATUS = 'Y' 表示活跃客户
  ACCT_LEVEL 常见值：levelA（一级）、level2A、level3A、level4A、level5A、belowA、NormalCatering（普通餐饮）

若问题涉及"终端客户"，用 acct_type = 'Terminal' 或 a.ACCT_TYPE = 'Terminal' 过滤。
若问题涉及"活跃终端"，加条件 a.ACCT_STATUS = 'Y'。
""",
]

# ── Q-SQL 训练对（Few-shot）──
TRAINING_PAIRS = [
    {
        "question": "2025年1月各产品的扫码开瓶数量排名",
        "sql": """SELECT product_id, COUNT(*) AS scan_count
FROM lj_consumer_scan
WHERE scan_time >= '2025-01-01' AND scan_time < '2025-02-01'
GROUP BY product_id
ORDER BY scan_count DESC
LIMIT 20""",
    },
    {
        "question": "2025年第一季度各省份的开瓶扫码量对比",
        "sql": """SELECT province, COUNT(*) AS scan_count
FROM lj_consumer_scan
WHERE scan_time >= '2025-01-01' AND scan_time < '2025-04-01'
GROUP BY province
ORDER BY scan_count DESC""",
    },
    {
        "question": "春节期间各活动类型的参与量排名",
        "sql": """SELECT camp_type, COUNT(*) AS participant_count
FROM lj_consumer_scan
WHERE scan_time >= '2025-01-28' AND scan_time < '2025-02-05'
GROUP BY camp_type
ORDER BY participant_count DESC""",
    },
    {
        "question": "2025年上半年每月的中奖率趋势",
        "sql": """SELECT
    DATE_FORMAT(scan_time, '%Y-%m') AS month,
    COUNT(*) AS total_scans,
    SUM(CASE WHEN is_winner = 'Y' THEN 1 ELSE 0 END) AS winner_count,
    ROUND(SUM(CASE WHEN is_winner = 'Y' THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS win_rate_pct
FROM lj_consumer_scan
WHERE scan_time >= '2025-01-01' AND scan_time < '2025-07-01'
GROUP BY DATE_FORMAT(scan_time, '%Y-%m')
ORDER BY month""",
    },
    {
        "question": "2025年第一季度各产品的扫码量占比",
        "sql": """SELECT
    product_id,
    COUNT(*) AS scan_count,
    ROUND(COUNT(*) / SUM(COUNT(*)) OVER() * 100, 2) AS percentage
FROM lj_consumer_scan
WHERE scan_time >= '2025-01-01' AND scan_time < '2025-04-01'
GROUP BY product_id
ORDER BY scan_count DESC""",
    },
    {
        "question": "异地扫码占比最高的5个省份",
        "sql": """SELECT
    province,
    COUNT(*) AS total_scans,
    SUM(CASE WHEN differ_area_scan = 'Y' THEN 1 ELSE 0 END) AS differ_scans,
    ROUND(SUM(CASE WHEN differ_area_scan = 'Y' THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS differ_rate_pct
FROM lj_consumer_scan
WHERE scan_time >= '2025-01-01' AND scan_time < '2025-04-01'
GROUP BY province
ORDER BY differ_rate_pct DESC
LIMIT 5""",
    },
    {
        "question": "再来一瓶活动在各省的核销情况",
        "sql": """SELECT
    province,
    COUNT(*) AS total_scans,
    SUM(CASE WHEN writeoff = 'Y' THEN 1 ELSE 0 END) AS writeoff_count,
    ROUND(SUM(CASE WHEN writeoff = 'Y' THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS writeoff_rate_pct
FROM lj_consumer_scan
WHERE scan_time >= '2025-01-01' AND scan_time < '2025-04-01'
  AND camp_type = '再来一瓶'
GROUP BY province
ORDER BY writeoff_rate_pct DESC""",
    },
    {
        "question": "2025年3月各城市扫码量Top10",
        "sql": """SELECT city, province, COUNT(*) AS scan_count
FROM lj_consumer_scan
WHERE scan_time >= '2025-03-01' AND scan_time < '2025-04-01'
GROUP BY city, province
ORDER BY scan_count DESC
LIMIT 10""",
    },
    {
        "question": "2025年每个月的总扫码量和中奖人数",
        "sql": """SELECT
    DATE_FORMAT(scan_time, '%Y-%m') AS month,
    COUNT(*) AS total_scans,
    SUM(CASE WHEN is_winner = 'Y' THEN 1 ELSE 0 END) AS total_winners
FROM lj_consumer_scan
WHERE scan_time >= '2025-01-01' AND scan_time < '2026-01-01'
GROUP BY DATE_FORMAT(scan_time, '%Y-%m')
ORDER BY month""",
    },
    {
        "question": "2025年上半年各经销商的扫码量排名",
        "sql": """SELECT
    from_dealer_id,
    COUNT(*) AS scan_count
FROM lj_consumer_scan
WHERE scan_time >= '2025-01-01' AND scan_time < '2025-07-01'
  AND from_dealer_id IS NOT NULL
  AND from_dealer_id != ''
GROUP BY from_dealer_id
ORDER BY scan_count DESC
LIMIT 20""",
    },
    {
        "question": "2025年第一季度终端客户的扫码开瓶量排名Top20",
        "sql": """SELECT
    a.ACCT_NAME   AS terminal_name,
    a.PROVINCE    AS province,
    a.CITY        AS city,
    COUNT(*)      AS scan_count
FROM lj_consumer_scan s
LEFT JOIN lnk_accnt a ON s.acct_id = a.row_id
WHERE s.scan_time >= '2025-01-01' AND s.scan_time < '2025-04-01'
  AND s.acct_type = 'Terminal'
GROUP BY a.ACCT_NAME, a.PROVINCE, a.CITY
ORDER BY scan_count DESC
LIMIT 20""",
    },
    {
        "question": "各省份的活跃终端客户开瓶数量",
        "sql": """SELECT
    a.PROVINCE    AS province,
    COUNT(DISTINCT s.acct_id) AS terminal_count,
    COUNT(*)      AS scan_count
FROM lj_consumer_scan s
LEFT JOIN lnk_accnt a ON s.acct_id = a.row_id
WHERE s.scan_time >= '2025-01-01' AND s.scan_time < '2026-01-01'
  AND s.acct_type = 'Terminal'
  AND a.ACCT_STATUS = 'Y'
GROUP BY a.PROVINCE
ORDER BY scan_count DESC""",
    },
    {
        "question": "不同等级终端客户的扫码量分布",
        "sql": """SELECT
    a.ACCT_LEVEL  AS acct_level,
    COUNT(DISTINCT s.acct_id) AS terminal_count,
    COUNT(*)      AS scan_count,
    ROUND(COUNT(*) / SUM(COUNT(*)) OVER() * 100, 2) AS pct
FROM lj_consumer_scan s
LEFT JOIN lnk_accnt a ON s.acct_id = a.row_id
WHERE s.scan_time >= '2025-01-01' AND s.scan_time < '2026-01-01'
  AND s.acct_type = 'Terminal'
GROUP BY a.ACCT_LEVEL
ORDER BY scan_count DESC""",
    },
]


def reset_training_data(vn) -> None:
    """清空 ChromaDB 向量库中的三个 collection（ddl / documentation / sql）"""
    # ChromaDB_VectorStore 使用三个独立 collection
    for col_attr in ('ddl_collection', 'documentation_collection', 'sql_collection'):
        try:
            col = getattr(vn, col_attr, None)
            if col is None:
                continue
            col_name = col.name
            ef = col._embedding_function
            vn.chroma_client.delete_collection(col_name)
            new_col = vn.chroma_client.get_or_create_collection(
                name=col_name,
                embedding_function=ef,
            )
            setattr(vn, col_attr, new_col)
            print(f'  ✓ [{col_name}] 已清空')
        except Exception as e:
            print(f'  清空 {col_attr} 时出错（可忽略）: {e}')


def train_from_db(vn, reset: bool = False) -> None:
    """
    方式 B：从数据库自动拉取表结构训练（无需手工维护 DDL）。

    原理：
      1. 查询 INFORMATION_SCHEMA.COLUMNS 获取所有表的列信息（含注释）
      2. get_training_plan_generic() 按表分组生成训练文档
      3. vn.train(plan=...) 批量写入向量库

    适用场景：
      - 数据库表结构已存在，不想手写 DDL
      - 表结构有变更时，--reset --from-db 快速重新同步
    """
    from bc_vanna import fetch_training_plan_from_db

    if reset:
        print('\n[0] 重置向量库...')
        reset_training_data(vn)

    print('\n[1/3] 从 INFORMATION_SCHEMA 自动拉取表结构...')
    t0 = time.time()

    # 拉取扫码主表 + 客户基本表
    target_tables = ['lj_consumer_scan', 'lnk_accnt']
    plan = fetch_training_plan_from_db(vn, target_tables=target_tables)

    n_items = len(plan._plan)
    print(f'  ✓ 获取到 {n_items} 张表的结构信息（{time.time()-t0:.1f}s）')
    for item in plan._plan:
        print(f'    • {item.item_name}')

    print('\n[2/3] 将表结构写入向量库...')
    t0 = time.time()
    vn.train(plan=plan)
    print(f'  ✓ 表结构训练完成（{time.time()-t0:.1f}s）')

    # 追加业务文档和 Q-SQL（与手动模式共用）
    _train_docs_and_sql(vn, start_step=3)


def _train_docs_and_sql(vn, start_step: int = 1) -> None:
    """训练业务文档 + Q-SQL 对（两种模式共用）"""
    # ── 业务文档 ──
    step = start_step
    print(f'\n[{step}/{step+1}] 训练业务文档（{len(BUSINESS_DOCS)} 条）...')
    t0 = time.time()
    for i, doc in enumerate(BUSINESS_DOCS, 1):
        vn.train(documentation=doc.strip())
        print(f'  ✓ 文档 {i}/{len(BUSINESS_DOCS)} 写入')
    print(f'  全部完成（{time.time()-t0:.1f}s）')

    # ── Q-SQL 对 ──
    step += 1
    print(f'\n[{step}/{step}] 训练 Q-SQL 示例（{len(TRAINING_PAIRS)} 对）...')
    t0 = time.time()
    for i, pair in enumerate(TRAINING_PAIRS, 1):
        vn.train(question=pair['question'], sql=pair['sql'])
        print(f'  ✓ [{i}/{len(TRAINING_PAIRS)}] {pair["question"][:35]}...')
    print(f'  全部完成（{time.time()-t0:.1f}s）')


def train(reset: bool = False, from_db: bool = False) -> None:
    """
    执行完整训练流程。

    Args:
        reset:   True 则先清空向量库再训练
        from_db: True 则使用方式 B（从数据库自动拉取表结构）
                 False 则使用方式 A（手工维护的 DDL 常量）
    """
    print('=' * 60)
    print('  BC白酒营销助手 — Vanna 训练脚本')
    mode_label = '模式 B：数据库自动拉取' if from_db else '模式 A：手工 DDL'
    print(f'  {mode_label}')
    print('=' * 60)

    vn = get_vanna(connect=from_db)

    if from_db:
        # 方式 B：直接从数据库 INFORMATION_SCHEMA 获取表结构
        train_from_db(vn, reset=reset)
    else:
        # 方式 A：使用手工维护的 DDL 常量
        if reset:
            print('\n[0/4] 重置向量库...')
            reset_training_data(vn)

        print('\n[1/4] 训练 DDL（手工建表语句）...')
        t0 = time.time()
        vn.train(ddl=DDL)
        print(f'  ✓ DDL 写入完成（{time.time()-t0:.1f}s）')

        _train_docs_and_sql(vn, start_step=2)

    # ── 汇总 ──
    training_data = vn.get_training_data()
    total = len(training_data) if training_data is not None else '?'
    print(f'\n训练完成！向量库共 {total} 条记录')
    print('现在可以运行：python app.py')


if __name__ == '__main__':
    reset_flag  = '--reset'   in sys.argv
    from_db_flag = '--from-db' in sys.argv
    train(reset=reset_flag, from_db=from_db_flag)
