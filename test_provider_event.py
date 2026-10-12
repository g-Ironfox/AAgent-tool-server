import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from main import call_tool, handle_provider_event
from models import ProviderEvent, ToolCallRequest


class CallbackEventTypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unlisted_event_type_is_accepted(self):
        tool = SimpleNamespace(inputSchema={"type": "object"})
        registry = SimpleNamespace(
            get_tool=AsyncMock(return_value=tool),
            list_tools=AsyncMock(return_value=[{"name": "test.echo", "provider_id": "provider"}]),
            send=AsyncMock(return_value=True),
        )
        store = SimpleNamespace(
            create=AsyncMock(return_value=True),
            transition=AsyncMock(),
            get=AsyncMock(return_value={"status": "pending"}),
        )
        request = ToolCallRequest(
            arguments={}, mode="async",
            callback={"type": "redis", "queue": "results", "event_type": "custom_event"},
        )

        with patch("main.store", store), patch("main.registry", registry):
            response = await call_tool("test.echo", request)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(store.create.await_args.args[0]["callback"]["event_type"], "custom_event")


class ProviderResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_output_fails_task_and_next_result_still_works(self):
        tasks = {
            "bad": {"task_id": "bad", "tool": "test.echo", "provider_id": "provider"},
            "good": {"task_id": "good", "tool": "test.echo", "provider_id": "provider"},
        }
        store = SimpleNamespace(get=AsyncMock(side_effect=tasks.get), transition=AsyncMock())
        tool = SimpleNamespace(outputSchema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        })
        registry = SimpleNamespace(get_tool=AsyncMock(return_value=tool))
        connection = SimpleNamespace(provider_id="provider")

        with patch("main.store", store), patch("main.registry", registry):
            await handle_provider_event(connection, ProviderEvent(
                type="result", task_id="bad", success=True, output={"value": 42},
            ))
            await handle_provider_event(connection, ProviderEvent(
                type="result", task_id="good", success=True, output={"value": "ok"},
            ))

        bad_call, good_call = store.transition.await_args_list
        self.assertEqual(bad_call.args[:2], ("bad", {"pending", "working"}))
        self.assertEqual(bad_call.args[2]["status"], "failed")
        self.assertEqual(bad_call.args[2]["error"]["code"], "OutputValidationError")
        self.assertEqual(good_call.args[2], {
            "status": "completed", "result": {"value": "ok"}, "error": None,
        })


if __name__ == "__main__":
    unittest.main()