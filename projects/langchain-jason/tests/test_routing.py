from __future__ import annotations

import unittest

from chains.intent_chain import route_request
from core.schemas import ExecutionRequest


class RoutingTest(unittest.TestCase):
    def test_explicit_audit_uses_lcel(self):
        route, task = route_request(ExecutionRequest(task="audit", directory="/tmp/example"))
        self.assertEqual(("lcel", "audit"), (route, task))

    def test_search_prompt_uses_direct_tool(self):
        route, task = route_request(ExecutionRequest(prompt="联网搜索 LangChain 最新文档"))
        self.assertEqual(("direct", "search"), (route, task))

    def test_open_ended_prompt_uses_react(self):
        route, task = route_request(ExecutionRequest(prompt="帮我研究这个问题并给出综合建议"))
        self.assertEqual(("react", "auto"), (route, task))


if __name__ == "__main__":
    unittest.main()

