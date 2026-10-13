import unittest
from safety import validate_sql, safe_chart


class SafetyTests(unittest.TestCase):
    def test_queries(self):
        for sql in ['SELECT COUNT(*) FROM lj_consumer_scan',
                    'WITH x AS (SELECT 1 AS n) SELECT n FROM x',
                    "SELECT 'UPDATE' AS label;", 'SELECT COUNT(*) OVER() FROM t']:
            validate_sql(sql)

    def test_reject(self):
        for sql in ['', 'DELETE FROM t', 'SELECT 1; DROP TABLE t',
                    'SELECT * INTO OUTFILE \'/tmp/x\' FROM t',
                    'SELECT SLEEP(10)', 'SELECT LOAD_FILE(\'/etc/passwd\')',
                    'SELECT 1 /*! INTO OUTFILE \'x\' */',
                    'SELECT * FROM t FOR UPDATE', 'SELECT @x:=1',
                    'WITH x AS (SELECT 1) DELETE FROM t']:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_sql(sql)

    def test_chart(self):
        import pandas as pd
        self.assertEqual(len(safe_chart(pd.DataFrame()).data), 0)
        fig = safe_chart(pd.DataFrame({'province': ['演示省'], 'count': [2]}))
        self.assertEqual(list(fig.data[0].y), [2])


if __name__ == '__main__':
    unittest.main()
