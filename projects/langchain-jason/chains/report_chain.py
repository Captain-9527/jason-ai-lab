"""兼容旧导入路径；终端报告渲染实现已迁至 cli.report。"""

from cli.report import render_markdown_report

__all__ = ["render_markdown_report"]
