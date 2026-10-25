"""显式地区数字格式；不凭三位小数或题号猜测千分位。"""
import re

from ..schemas import PipelineError

NUMERIC_FORMATS = {'standard', 'decimal_comma', 'dot_grouping'}


def normalize_numeric_text(value, style='standard'):
    if not isinstance(style, str) or style not in NUMERIC_FORMATS:
        raise PipelineError('未知numeric_format，须为standard/decimal_comma/dot_grouping')
    text = str(value).strip()
    if style == 'standard':
        return text
    negative = text.startswith('(') and text.endswith(')')
    if negative:
        text = text[1:-1].strip()
    percent = text.endswith(('%', '％'))
    if percent:
        text = text[:-1].strip()
    pattern = (r'[+-]?\d+(?:,\d+)?' if style == 'decimal_comma'
               else r'[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?')
    if not re.fullmatch(pattern, text):
        raise PipelineError('数字与声明的地区格式不一致，不能猜测分隔符')
    if style == 'dot_grouping':
        text = text.replace('.', '')
    text = text.replace(',', '.')
    text += '%' if percent else ''
    return '(' + text + ')' if negative else text


def declared_format(value):
    style = value.get('numeric_format', 'standard')
    if not isinstance(style, str) or style not in NUMERIC_FORMATS:
        raise PipelineError('未知numeric_format')
    if style != 'standard' and (not isinstance(value.get('numeric_format_basis'), str)
                                or not value['numeric_format_basis'].strip()):
        raise PipelineError('非标准数字格式须声明语言、同列格式或单位等numeric_format_basis')
    return style
