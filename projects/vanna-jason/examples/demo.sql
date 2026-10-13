-- 仅供本地新建演示库使用；不包含真实客户或扫码记录。
-- 不会创建数据库用户，请另行配置仅有此库 SELECT 权限的账号。
CREATE DATABASE IF NOT EXISTS vanna_demo CHARACTER SET utf8mb4;
USE vanna_demo;

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

CREATE TABLE lnk_accnt (
 row_id VARCHAR(30) PRIMARY KEY, ACCT_CODE VARCHAR(30), ACCT_NAME VARCHAR(200),
 ACCT_TYPE VARCHAR(50), ACCT_STATUS VARCHAR(30), ACCT_LEVEL VARCHAR(50),
 PROVINCE VARCHAR(100), CITY VARCHAR(100), DISTRICT VARCHAR(100)
);
INSERT INTO lnk_accnt VALUES
('demo-001','DEMO-A001','演示门店一','Terminal','Y','levelA','四川','成都','演示区'),
('demo-002','DEMO-A002','演示经销商','Dealer','Y','levelB','浙江','杭州','演示区');
INSERT INTO lj_consumer_scan
(scan_time, product_id, province, city, acct_id, acct_type, is_winner,
 prize_amount, camp_type, differ_area_scan, writeoff) VALUES
('2025-01-15','DEMO-P01','四川','成都','demo-001','Terminal','Y','5','扫码领红包','N','N'),
('2025-01-29','DEMO-P02','浙江','杭州','demo-002','Dealer','N','0','再来一瓶','Y','N'),
('2025-02-02','DEMO-P01','四川','成都','demo-001','Terminal','Y','0','再来一瓶','N','Y'),
('2025-03-10','DEMO-P02','浙江','杭州','demo-002','Dealer','Y','10','扫码领红包','Y','N'),
('2025-04-10','DEMO-P01','四川','成都','demo-001','Terminal','N','0','积分兑换','N','N'),
('2025-06-10','DEMO-P02','浙江','杭州','demo-002','Dealer','Y','5','扫码领红包','N','N');
