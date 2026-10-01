import json

import pytest

from apps.api.model_adapter import _chat_payload, _normalise_arguments, _stream_response
from packages.codex_runtime.config import CodexHomeConfig
from packages.config import get_settings
from packages.matching.client import DeepSeekClient
from packages.model_policy import (
    ZHIPU_BASE, ZHIPU_CODING_BASE, ZAI_CODING_BASE,
    codex_adapter_base, validate_connection,
)
from tests.test_owner_configuration import owner


@pytest.mark.parametrize("provider,base", [
    ("zhipu", ZHIPU_BASE), ("zhipu_coding", ZHIPU_CODING_BASE),
    ("zhipu_coding", ZAI_CODING_BASE),
])
def test_approved_zhipu_endpoints(provider, base):
    assert validate_connection(provider, "openai", base, "glm-5.3-flash") == base


@pytest.mark.parametrize("base", [
    "http://open.bigmodel.cn/api/paas/v4",
    "https://open.bigmodel.cn.evil.test/api/paas/v4",
    "https://open.bigmodel.cn/api/paas/v4?key=secret",
    "https://open.bigmodel.cn/api/paas/v4/other",
])
def test_zhipu_rejects_unapproved_endpoints(base):
    with pytest.raises(ValueError):
        validate_connection("zhipu", "openai", base, "glm-4.7")


def test_zhipu_structured_client_uses_chat_completions_without_deepseek_fields():
    calls = []

    def transport(endpoint, headers, payload, timeout):
        calls.append((endpoint, headers, payload, timeout))
        return {"model": "glm-4.7", "choices": [{"finish_reason": "stop",
                "message": {"content": '{"status":"ok"}'}}],
                "usage": {"prompt_tokens": 13, "completion_tokens": 5}}

    client = DeepSeekClient(api_key="zhipu-secret", model="glm-4.7", provider="zhipu",
                            api_style="openai", endpoint=ZHIPU_BASE + "/chat/completions",
                            transport=transport, max_attempts=1)
    response = client.complete_structured(system_prompt="Return JSON", user_prompt="OK",
                                          schema={"type": "object", "properties": {
                                              "status": {"type": "string"}}})
    endpoint, headers, payload, _ = calls[0]
    assert endpoint == ZHIPU_BASE + "/chat/completions"
    assert headers["Authorization"] == "Bearer zhipu-secret"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning" not in payload
    assert response.content == '{"status":"ok"}'
    assert (response.input_tokens, response.output_tokens) == (13, 5)


def test_zhipu_structured_array_uses_json_object_envelope_and_returns_array():
    def transport(endpoint, headers, payload, timeout):
        assert '"result"' in payload["messages"][0]["content"]
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": '{"result":[{"id":"mail-1"}]}'}}]}

    client = DeepSeekClient(api_key="fixture", model="glm-4.7", provider="zhipu",
                           api_style="openai", endpoint=ZHIPU_BASE + "/chat/completions",
                           transport=transport, max_attempts=1)
    response = client.complete_structured(system_prompt="Return JSON", user_prompt="Classify",
        schema={"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}}}})
    assert json.loads(response.content) == [{"id": "mail-1"}]


def test_connection_switch_preserves_only_same_provider_key(owner):
    client, headers, _, _ = owner
    url = "/api/local-ui/configuration"
    first = {"id": "main", "name": "DeepSeek", "provider": "deepseek",
             "api_style": "anthropic", "base_url": "https://api.deepseek.com",
             "model": "deepseek-flash", "api_key": "deep-secret"}
    response = client.post(url + "/save", headers=headers, json={
        "model_connections": [first], "active_model_connection_id": "main"})
    assert response.status_code == 200, response.text
    changed = {**first, "name": "智谱", "provider": "zhipu", "api_style": "openai",
               "base_url": ZHIPU_BASE, "model": "glm-4.7", "api_key": ""}
    response = client.post(url + "/save", headers=headers, json={
        "model_connections": [changed], "active_model_connection_id": "main"})
    assert response.status_code == 200, response.text
    assert get_settings().model_provider == "zhipu"
    assert get_settings().llm_api_key == ""
    assert not get_settings().llm_enabled
    changed["api_key"] = "glm-secret"
    response = client.post(url + "/save", headers=headers, json={
        "model_connections": [changed], "active_model_connection_id": "main"})
    assert response.status_code == 200, response.text
    assert get_settings().llm_api_key == "glm-secret"
    assert get_settings().llm_endpoint == ZHIPU_BASE + "/chat/completions"
    public = client.post(url + "/read", headers=headers).json()
    assert "glm-secret" not in json.dumps(public)
    assert public["model_connections"][0]["key_configured"] is True


def test_codex_chat_provider_uses_only_local_responses_adapter(tmp_path):
    config = CodexHomeConfig(model="glm-4.7", provider_id="glm", provider_name="智谱",
        base_url=codex_adapter_base(8010), api_key_env="RECRUITOPS_LLM_API_KEY",
        mcp_command="python")
    rendered = config.render_toml()
    assert 'base_url = "http://127.0.0.1:8010/api/codex-model"' in rendered
    assert 'wire_api = "responses"' in rendered


def test_responses_input_converts_tool_history_and_rejects_wrong_model():
    body = {"model": "glm-4.7", "stream": True, "instructions": "Be helpful",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "find jobs"}]},
                {"type": "function_call", "call_id": "call_1", "name": "search_jobs",
                 "arguments": '{"query":"AI"}'},
                {"type": "function_call_output", "call_id": "call_1", "output": "[]"},
            ], "tools": [{"type": "function", "name": "search_jobs",
                          "description": "Search", "parameters": {"type": "object"}}],
            "tool_choice": {"type": "function", "name": "search_jobs"}}
    payload = _chat_payload(body, "glm-4.7")
    assert [message["role"] for message in payload["messages"]] == [
        "system", "user", "assistant", "tool"]
    assert payload["messages"][2]["tool_calls"][0]["id"] == "call_1"
    assert payload["messages"][3]["tool_call_id"] == "call_1"
    assert payload["tool_choice"] == {"type": "function", "function": {"name": "search_jobs"}}
    with pytest.raises(Exception):
        _chat_payload({**body, "model": "different"}, "glm-4.7")


def test_responses_input_flattens_namespaced_mcp_tools_for_chat_models():
    payload = _chat_payload({
        "model": "glm-4.7",
        "input": "run sync",
        "tools": [{
            "type": "namespace",
            "name": "mcp__recruitops",
            "description": "RecruitOps tools",
            "tools": [{
                "type": "function",
                "name": "daily_recruitment_sync",
                "description": "Run the recruitment sync",
                "parameters": {"type": "object", "properties": {"request": {"type": "object"}}},
            }],
        }],
    }, "glm-4.7")

    assert payload["tools"] == [{
        "type": "function",
        "function": {
            "name": "daily_recruitment_sync",
            "description": "Run the recruitment sync",
            "parameters": {"type": "object", "properties": {"request": {"type": "object"}}},
        },
    }]


def test_namespaced_chat_call_is_restored_for_codex_and_nested_request_is_object():
    class FakeResponse:
        def iter_lines(self, **_kwargs):
            yield "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "call_sync",
                "function": {
                    "name": "daily_recruitment_sync",
                    "arguments": '{"request":"{\\"mode\\":\\"full\\",\\"dry_run\\":true}"}',
                },
            }]}, "finish_reason": "tool_calls"}]})
            yield "data: [DONE]"

        def close(self):
            pass

    response = {"id": "resp_test", "object": "response", "created_at": 0,
                "model": "glm-4.7", "status": "in_progress", "output": [],
                "usage": None, "error": None, "incomplete_details": None,
                "parallel_tool_calls": True}
    events = list(_stream_response(FakeResponse(), response, {
        "daily_recruitment_sync": ("mcp__recruitops", "daily_recruitment_sync"),
    }))
    completed = json.loads(events[-1].split("data: ", 1)[1])
    call = completed["response"]["output"][0]
    assert call["name"] == "daily_recruitment_sync"
    assert call["namespace"] == "mcp__recruitops"
    assert json.loads(call["arguments"]) == {
        "request": {"mode": "full", "dry_run": True},
    }
    assert _normalise_arguments('{"request":"not-json"}') == '{"request":"not-json"}'


def test_local_adapter_streams_text_and_function_call(owner, monkeypatch):
    client, headers, _, _ = owner
    connection = {"id": "glm", "name": "智谱", "provider": "zhipu", "api_style": "openai",
                  "base_url": ZHIPU_BASE, "model": "glm-4.7", "api_key": "glm-secret"}
    saved = client.post("/api/local-ui/configuration/save", headers=headers, json={
        "model_connections": [connection], "active_model_connection_id": "glm"})
    assert saved.status_code == 200, saved.text

    class FakeResponse:
        status_code = 200

        def iter_lines(self, **_kwargs):
            chunks = [
                {"choices": [{"delta": {"content": "已找到"}, "finish_reason": None}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1",
                    "function": {"name": "search_jobs", "arguments": '{"query":"AI"}'}}]},
                    "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ]
            for chunk in chunks:
                yield "data: " + json.dumps(chunk, ensure_ascii=False)
            yield "data: [DONE]"

        def close(self):
            pass

    def fake_post(url, *, headers, json, **kwargs):
        assert url == ZHIPU_BASE + "/chat/completions"
        assert headers["Authorization"] == "Bearer glm-secret"
        assert json["model"] == "glm-4.7"
        return FakeResponse()

    monkeypatch.setattr("apps.api.model_adapter.requests.post", fake_post)
    request = {"model": "glm-4.7", "stream": True, "input": "帮我找工作",
               "tools": [{"type": "function", "name": "search_jobs"}]}
    denied = client.post("/api/codex-model/responses", json=request)
    assert denied.status_code == 401
    response = client.post("/api/codex-model/responses", json=request,
                           headers={"Authorization": "Bearer glm-secret"})
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ")]
    assert events[-1]["type"] == "response.completed"
    assert [item["type"] for item in events[-1]["response"]["output"]] == [
        "message", "function_call"]
    assert events[-1]["response"]["output"][1]["arguments"] == '{"query":"AI"}'
    assert all("item_id" in event for event in events
               if event["type"] in {"response.output_text.delta",
                                    "response.function_call_arguments.delta"})
