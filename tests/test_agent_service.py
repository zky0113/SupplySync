"""Failure-guard regression tests for the optional live tool-calling Agent."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from demo.agent_service import live_answer


def tool_call(name, arguments, call_id="call1"):
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def tool_reply(*calls):
    return {"content": None, "tool_calls": list(calls)}


def final_reply(content="根据已返回的工具证据回答。"):
    return {"content": content}


class LiveAgentFailureGuardTests(unittest.TestCase):
    def answer(self):
        return live_answer(object(), "请分析库存和策略", "C04", "baseline", "test-key")

    def test_zero_tool_calls_are_rejected(self):
        with patch("demo.agent_service._deepseek_chat", return_value=final_reply("未经核实的结论")):
            with self.assertRaisesRegex(RuntimeError, "未调用业务工具"):
                self.answer()

    def test_scope_error_is_not_counted_as_evidence(self):
        replies = [tool_reply(tool_call("read_inventory", '{"node_id":"BAD"}')),
                   final_reply("虚构库存 99999 件")]
        with patch("demo.agent_service._deepseek_chat", side_effect=replies), \
             patch("demo.agent_service.execute_tool") as execute:
            with self.assertRaisesRegex(RuntimeError, "业务工具均调用失败"):
                self.answer()
        execute.assert_not_called()

    def test_invalid_arguments_are_not_counted_as_evidence(self):
        for arguments in ("{invalid", "[]", "{}"):
            with self.subTest(arguments=arguments):
                replies = [tool_reply(tool_call("read_inventory", arguments)), final_reply()]
                with patch("demo.agent_service._deepseek_chat", side_effect=replies), \
                     patch("demo.agent_service.execute_tool") as execute:
                    if arguments == "{}":
                        execute.side_effect = ValueError("缺少节点")
                    with self.assertRaisesRegex(RuntimeError, "业务工具均调用失败"):
                        self.answer()

    def test_error_empty_and_non_object_results_are_not_evidence(self):
        for result in ({"error": "业务失败"}, {}, None, []):
            with self.subTest(result=result):
                replies = [tool_reply(tool_call("read_inventory", '{"node_id":"C04"}')),
                           final_reply("虚构库存 99999 件")]
                with patch("demo.agent_service._deepseek_chat", side_effect=replies), \
                     patch("demo.agent_service.execute_tool", return_value=result):
                    with self.assertRaisesRegex(RuntimeError, "业务工具均调用失败"):
                        self.answer()

    def test_business_runtime_failure_is_not_evidence(self):
        replies = [tool_reply(tool_call("read_inventory", '{"node_id":"C04"}')),
                   final_reply()]
        with patch("demo.agent_service._deepseek_chat", side_effect=replies), \
             patch("demo.agent_service.execute_tool", side_effect=RuntimeError("仿真失败")):
            with self.assertRaisesRegex(RuntimeError, "业务工具均调用失败"):
                self.answer()

    def test_partial_success_keeps_failure_trace_without_run_id(self):
        replies = [tool_reply(
            tool_call("read_inventory", '{"node_id":"C04"}', "inventory"),
            tool_call("compare_strategies", '{"scenario_id":"baseline"}', "compare")),
            final_reply("初始库存 273 件；对照实验暂不可用。")]
        with patch("demo.agent_service._deepseek_chat", side_effect=replies), \
             patch("demo.agent_service.execute_tool", side_effect=[
                 {"initial_on_hand_units": 273}, {"error": "仿真暂不可用"}]):
            answer = self.answer()
        self.assertEqual([step["success"] for step in answer["trace"]], [True, False])
        self.assertIsNone(answer["run_id"])
        self.assertIn("error", answer["trace"][1]["result"])

    def test_failed_tool_can_retry_successfully(self):
        replies = [tool_reply(tool_call("read_inventory", '{"node_id":"C04"}', "first")),
                   tool_reply(tool_call("read_inventory", '{"node_id":"C04"}', "retry")),
                   final_reply("初始库存 273 件。")]
        with patch("demo.agent_service._deepseek_chat", side_effect=replies), \
             patch("demo.agent_service.execute_tool", side_effect=[
                 {"error": "暂不可用"}, {"initial_on_hand_units": 273}]):
            answer = self.answer()
        self.assertEqual([step["success"] for step in answer["trace"]], [False, True])

    def test_run_id_comes_only_from_successful_comparison(self):
        replies = [tool_reply(tool_call("compare_strategies", '{"scenario_id":"baseline"}', "first")),
                   tool_reply(tool_call("compare_strategies", '{"scenario_id":"baseline"}', "retry")),
                   final_reply("对照实验已完成。")]
        with patch("demo.agent_service._deepseek_chat", side_effect=replies), \
             patch("demo.agent_service.execute_tool", side_effect=[
                 {"error": "第一次失败"}, {"run_id": "verified-run"}]):
            answer = self.answer()
        self.assertEqual(answer["run_id"], "verified-run")

    def test_malformed_model_envelopes_are_rejected(self):
        for reply in (None, [], {"tool_calls": "not-a-list"},
                      tool_reply({"function": {"name": "read_inventory", "arguments": "{}"}}),
                      tool_reply({"id": "call1", "function": None})):
            with self.subTest(reply=reply), \
                 patch("demo.agent_service._deepseek_chat", return_value=reply):
                with self.assertRaisesRegex(RuntimeError, "模型返回无效"):
                    self.answer()

    def test_unauthorized_tool_and_nontext_final_are_rejected(self):
        with patch("demo.agent_service._deepseek_chat", return_value=tool_reply(
                tool_call("unsafe_tool", "{}"))):
            with self.assertRaisesRegex(RuntimeError, "未授权工具"):
                self.answer()
        replies = [tool_reply(tool_call("read_inventory", '{"node_id":"C04"}')),
                   final_reply({"made_up": 1})]
        with patch("demo.agent_service._deepseek_chat", side_effect=replies), \
             patch("demo.agent_service.execute_tool", return_value={"initial_on_hand_units": 273}):
            with self.assertRaisesRegex(RuntimeError, "未给出可展示答案"):
                self.answer()

    def test_tool_and_round_limits_remain_in_force(self):
        over_limit = tool_reply(*(tool_call("read_inventory", '{"node_id":"C04"}', f"call{i}")
                                  for i in range(9)))
        with patch("demo.agent_service._deepseek_chat", return_value=over_limit):
            with self.assertRaisesRegex(RuntimeError, "工具调用超过安全上限"):
                self.answer()
        replies = [tool_reply(tool_call("read_inventory", '{"node_id":"C04"}', f"call{i}"))
                   for i in range(4)]
        with patch("demo.agent_service._deepseek_chat", side_effect=replies), \
             patch("demo.agent_service.execute_tool", return_value={"initial_on_hand_units": 273}):
            with self.assertRaisesRegex(RuntimeError, "工具调用轮数超过上限"):
                self.answer()

    def test_failed_ui_attempt_clears_previous_answer(self):
        from demo.views import agent_view

        fake_st = MagicMock()
        fake_st.session_state = {"agent_response": {
            "node_id": "C04", "scenario_id": "baseline", "mode": "DeepSeek 工具调用",
            "answer": "旧结论", "run_id": None, "trace": []}}
        fake_st.text_area.return_value = "重新分析"
        fake_st.radio.return_value = "证据模式（离线可用）"
        fake_st.button.return_value = True
        bundle = SimpleNamespace(report={"checks": [], "seed": 1}, snapshot_id="snapshot")
        with patch("demo.views.st", fake_st), patch("demo.views.configured_key", return_value=""), \
             patch("demo.views.context", return_value=object()), \
             patch("demo.views.evidence_answer", side_effect=RuntimeError("工具失败")):
            agent_view(bundle, "C04", "baseline")
        self.assertNotIn("agent_response", fake_st.session_state)
        fake_st.error.assert_called_once()
        fake_st.success.assert_not_called()
        fake_st.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
