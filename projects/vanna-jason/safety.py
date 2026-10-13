"""演示应用的纵深防护；数据库仍必须使用仅有 SELECT 权限的账号。"""
import os
import re

import sqlparse
from sqlparse import tokens as T


def allow_data():
    return os.getenv('ALLOW_LLM_TO_SEE_DATA', 'false').lower() == 'true'


def validate_sql(sql):
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError('SQL 不能为空')
    if '/*' in sql or '--' in sql or '#' in sql:
        raise ValueError('不接受 SQL 注释')
    statements = sqlparse.parse(sql)
    if len(statements) != 1 or statements[0].get_type() != 'SELECT':
        raise ValueError('只允许单条 SELECT 查询')
    forbidden = {'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE',
                 'TRUNCATE', 'REPLACE', 'CALL', 'EXECUTE', 'GRANT', 'REVOKE',
                 'INTO', 'OUTFILE', 'DUMPFILE', 'FOR', 'LOCK', 'SET',
                 'LOAD_FILE', 'SLEEP', 'BENCHMARK', 'GET_LOCK', 'RELEASE_LOCK'}
    for token in statements[0].flatten():
        if token.ttype in T.Literal.String:
            continue
        if token.value.strip('`').upper() in forbidden or '@' in token.value:
            raise ValueError('查询包含不允许的操作')
    if re.search(r':=', sql):
        raise ValueError('不允许变量赋值')


def safe_chart(df):
    import plotly.graph_objects as go
    if df is None or df.empty:
        return go.Figure()
    numeric = list(df.select_dtypes(include='number').columns)
    if not numeric:
        return go.Figure()
    data = df.head(100)
    labels = [c for c in data.columns if c not in numeric]
    x = data[labels[0]].astype(str) if labels else data.index.astype(str)
    return go.Figure(go.Bar(x=x, y=data[numeric[0]], name=str(numeric[0])))
