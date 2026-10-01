"""Local Responses-to-Chat bridge for approved GLM Chat Completions providers.

Codex speaks Responses only. The bridge accepts the active connection's bearer
key and translates the subset of Responses messages and tools Codex sends.
It never accepts an upstream URL from the caller.
"""
from __future__ import annotations

import json
import time
from secrets import compare_digest, token_hex
from typing import Any, Iterator, Mapping

import requests
from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from packages.config import get_settings
from packages.model_policy import completion_endpoint

router = APIRouter(prefix="/api/codex-model", tags=["codex-model"])


def _content(value: Any) -> str | list[dict[str, Any]]:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[dict[str, Any]] = []
        for part in value:
            if not isinstance(part, Mapping):
                continue
            if part.get("type") in {"input_text", "output_text", "text"}:
                parts.append({"type": "text", "text": str(part.get("text") or "")})
            elif part.get("type") == "input_image" and isinstance(part.get("image_url"), str):
                parts.append({"type": "image_url", "image_url": {"url": part["image_url"]}})
        if any(part["type"] == "image_url" for part in parts):
            return parts
        return "".join(part["text"] for part in parts)
    return ""


def _converted_tools(
    tools: Any,
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str]]]:
    """Flatten Responses namespaces for Chat Completions and retain routing data."""

    if not isinstance(tools, list):
        return [], {}
    plain_names = {
        str(tool["name"])
        for tool in tools
        if isinstance(tool, Mapping)
        and tool.get("type") == "function"
        and isinstance(tool.get("name"), str)
    }
    child_counts: dict[str, int] = {}
    for tool in tools:
        if not isinstance(tool, Mapping) or tool.get("type") != "namespace":
            continue
        for child in tool.get("tools") or []:
            if (isinstance(child, Mapping) and child.get("type") == "function"
                    and isinstance(child.get("name"), str)):
                name = child["name"]
                child_counts[name] = child_counts.get(name, 0) + 1

    converted: list[dict[str, Any]] = []
    aliases: dict[str, tuple[str, str]] = {}
    for tool in tools:
        if not isinstance(tool, Mapping):
            continue
        candidates: list[tuple[str, Mapping[str, Any], str | None, str]] = []
        if tool.get("type") == "function" and isinstance(tool.get("name"), str):
            candidates.append((tool["name"], tool, None, tool["name"]))
        elif tool.get("type") == "namespace" and isinstance(tool.get("name"), str):
            namespace = tool["name"]
            for child in tool.get("tools") or []:
                if (isinstance(child, Mapping) and child.get("type") == "function"
                        and isinstance(child.get("name"), str)):
                    child_name = child["name"]
                    alias = child_name
                    if child_counts.get(child_name, 0) > 1 or child_name in plain_names:
                        alias = f"{namespace}__{child_name}"
                    candidates.append((alias, child, namespace, child_name))
        for alias, candidate, namespace, original_name in candidates:
            converted.append({"type": "function", "function": {
                "name": alias, "description": candidate.get("description") or "",
                "parameters": candidate.get("parameters") or {
                    "type": "object", "properties": {}},
            }})
            if namespace is not None:
                aliases[alias] = (namespace, original_name)
    return converted, aliases


def _normalise_arguments(arguments: Any) -> str:
    value = arguments if isinstance(arguments, str) and arguments else "{}"
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return value
    if isinstance(parsed, dict) and isinstance(parsed.get("request"), str):
        try:
            request = json.loads(parsed["request"])
        except (TypeError, ValueError):
            pass
        else:
            if isinstance(request, dict):
                parsed["request"] = request
                return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return value


def _chat_payload(body: Mapping[str, Any], model: str) -> dict[str, Any]:
    if body.get("model") != model:
        raise HTTPException(422, "The requested model is not the active connection")
    if body.get("previous_response_id"):
        raise HTTPException(422, "Stateful Responses requests are not supported")
    converted_tools, namespace_aliases = _converted_tools(body.get("tools"))
    reverse_aliases = {identity: alias for alias, identity in namespace_aliases.items()}
    messages: list[dict[str, Any]] = []
    if isinstance(body.get("instructions"), str) and body["instructions"]:
        messages.append({"role": "system", "content": body["instructions"]})
    incoming = body.get("input", [])
    if isinstance(incoming, str):
        incoming = [{"role": "user", "content": incoming}]
    if not isinstance(incoming, list):
        raise HTTPException(422, "Invalid Responses input")
    for item in incoming:
        if not isinstance(item, Mapping):
            continue
        kind = item.get("type")
        if kind == "reasoning":
            continue
        if kind == "function_call":
            function_name = item.get("name")
            namespace = item.get("namespace")
            if isinstance(namespace, str) and isinstance(function_name, str):
                function_name = reverse_aliases.get((namespace, function_name), function_name)
            call = {"id": item.get("call_id"), "type": "function",
                    "function": {"name": function_name,
                                 "arguments": _normalise_arguments(item.get("arguments"))}}
            if messages and messages[-1].get("role") == "assistant" and "tool_calls" in messages[-1]:
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif kind == "function_call_output":
            output = item.get("output")
            messages.append({"role": "tool", "tool_call_id": item.get("call_id"),
                             "content": output if isinstance(output, str) else json.dumps(output)})
        elif item.get("role") in {"system", "developer", "user", "assistant"}:
            role = "system" if item["role"] == "developer" else item["role"]
            messages.append({"role": role, "content": _content(item.get("content"))})
    if not messages:
        raise HTTPException(422, "Responses input is empty")
    payload: dict[str, Any] = {"model": model, "messages": messages,
                               "stream": bool(body.get("stream"))}
    if converted_tools:
        payload["tools"] = converted_tools
    choice = body.get("tool_choice")
    if isinstance(choice, str) and choice in {"auto", "none", "required"}:
        payload["tool_choice"] = choice
    elif isinstance(choice, Mapping) and choice.get("type") == "function":
        payload["tool_choice"] = {"type": "function", "function": {"name": choice.get("name")}}
    if isinstance(body.get("parallel_tool_calls"), bool):
        payload["parallel_tool_calls"] = body["parallel_tool_calls"]
    if isinstance(body.get("max_output_tokens"), int) and body["max_output_tokens"] > 0:
        payload["max_tokens"] = body["max_output_tokens"]
    return payload


def _response_base(model: str) -> dict[str, Any]:
    return {"id": "resp_" + token_hex(12), "object": "response",
            "created_at": int(time.time()), "model": model, "status": "in_progress",
            "output": [], "usage": None, "error": None,
            "incomplete_details": None, "parallel_tool_calls": True}


def _event(kind: str, data: dict[str, Any], sequence: int) -> str:
    return f"event: {kind}\ndata: {json.dumps({'type': kind, 'sequence_number': sequence, **data}, ensure_ascii=False)}\n\n"


def _stream_response(
    upstream: requests.Response,
    response: dict[str, Any],
    namespace_aliases: Mapping[str, tuple[str, str]] | None = None,
) -> Iterator[str]:
    sequence = 0

    def emit(kind: str, data: dict[str, Any]) -> str:
        nonlocal sequence
        sequence += 1
        return _event(kind, data, sequence)

    text_item: dict[str, Any] | None = None
    text_index = -1
    text_value = ""
    calls: dict[int, dict[str, Any]] = {}
    finished = False
    try:
        yield emit("response.created", {"response": response.copy()})
        for raw in upstream.iter_lines(chunk_size=1, decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            data = raw[5:].strip()
            if data == "[DONE]":
                finished = True
                break
            try:
                chunk = json.loads(data)
            except (ValueError, TypeError):
                continue
            if not isinstance(chunk, Mapping):
                continue
            usage = chunk.get("usage")
            if isinstance(usage, Mapping):
                response["usage"] = {"input_tokens": usage.get("prompt_tokens", 0),
                                     "output_tokens": usage.get("completion_tokens", 0),
                                     "total_tokens": usage.get("total_tokens", 0)}
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
                continue
            choice = choices[0]
            delta = choice.get("delta")
            if isinstance(delta, Mapping):
                content = delta.get("content")
                if isinstance(content, str) and content:
                    if text_item is None:
                        text_index = len(response["output"])
                        text_item = {"id": "msg_" + token_hex(12), "type": "message",
                                     "role": "assistant", "status": "in_progress", "content": []}
                        response["output"].append(text_item)
                        yield emit("response.output_item.added", {"output_index": text_index,
                                    "item": text_item.copy()})
                        yield emit("response.content_part.added", {"item_id": text_item["id"],
                                    "output_index": text_index, "content_index": 0,
                                    "part": {"type": "output_text", "text": "", "annotations": []}})
                    text_value += content
                    yield emit("response.output_text.delta", {"item_id": text_item["id"],
                                "output_index": text_index,
                                "content_index": 0, "delta": content})
                tool_deltas = delta.get("tool_calls")
                if isinstance(tool_deltas, list):
                    for tool in tool_deltas:
                        if not isinstance(tool, Mapping) or not isinstance(tool.get("index"), int):
                            continue
                        index = tool["index"]
                        call = calls.setdefault(index, {"call_id": "", "name": "", "arguments": "",
                                                       "output_index": -1})
                        function = tool.get("function") or {}
                        if isinstance(tool.get("id"), str):
                            call["call_id"] = tool["id"]
                        if isinstance(function, Mapping):
                            if isinstance(function.get("name"), str):
                                call["name"] = function["name"]
                            if isinstance(function.get("arguments"), str):
                                call["arguments"] += function["arguments"]
                        if call["output_index"] < 0 and call["name"] and call["call_id"]:
                            call["output_index"] = len(response["output"])
                            namespace, name = (namespace_aliases or {}).get(
                                call["name"], (None, call["name"]))
                            item = {"id": "fc_" + token_hex(12), "type": "function_call",
                                    "call_id": call["call_id"], "name": name,
                                    "arguments": "", "status": "in_progress"}
                            if namespace is not None:
                                item["namespace"] = namespace
                            response["output"].append(item)
                            yield emit("response.output_item.added", {
                                "output_index": call["output_index"], "item": item.copy()})
                        if call["output_index"] >= 0 and isinstance(function, Mapping) and function.get("arguments"):
                            yield emit("response.function_call_arguments.delta", {
                                "item_id": response["output"][call["output_index"]]["id"],
                                "output_index": call["output_index"],
                                "delta": function["arguments"]})
            if choice.get("finish_reason") in {"stop", "tool_calls"}:
                finished = True
            elif choice.get("finish_reason") in {"length", "content_filter"}:
                break
        if not finished:
            response["status"] = "incomplete"
            response["incomplete_details"] = {"reason": "max_output_tokens"}
            yield emit("response.incomplete", {"response": response})
            return
        if text_item is not None:
            part = {"type": "output_text", "text": text_value, "annotations": []}
            text_item["content"] = [part]
            text_item["status"] = "completed"
            yield emit("response.output_text.done", {"item_id": text_item["id"],
                        "output_index": text_index,
                        "content_index": 0, "text": text_value})
            yield emit("response.content_part.done", {"item_id": text_item["id"],
                        "output_index": text_index,
                        "content_index": 0, "part": part})
            yield emit("response.output_item.done", {"output_index": text_index,
                        "item": text_item})
        for call in calls.values():
            index = call["output_index"]
            if index < 0:
                continue
            item = response["output"][index]
            namespace, name = (namespace_aliases or {}).get(
                call["name"], (None, call["name"]))
            arguments = _normalise_arguments(call["arguments"])
            item.update(call_id=call["call_id"], name=name,
                        arguments=arguments, status="completed")
            if namespace is not None:
                item["namespace"] = namespace
            yield emit("response.function_call_arguments.done", {"item_id": item["id"],
                        "output_index": index, "name": name,
                        "arguments": arguments})
            yield emit("response.output_item.done", {"output_index": index, "item": item})
        response["status"] = "completed"
        yield emit("response.completed", {"response": response})
    finally:
        upstream.close()


def _nonstream_response(
    upstream: requests.Response,
    response: dict[str, Any],
    namespace_aliases: Mapping[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    try:
        data = upstream.json()
    except ValueError:
        raise HTTPException(502, "Model returned invalid JSON") from None
    choices = data.get("choices") if isinstance(data, Mapping) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise HTTPException(502, "Model returned invalid choices")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise HTTPException(502, "Model returned an invalid message")
    if choice.get("finish_reason") not in {"stop", "tool_calls"}:
        raise HTTPException(502, "Model response was incomplete")
    if isinstance(message.get("content"), str) and message["content"]:
        response["output"].append({"id": "msg_" + token_hex(12), "type": "message",
                                   "role": "assistant", "status": "completed", "content": [
                                       {"type": "output_text", "text": message["content"]}]})
    for tool in message.get("tool_calls") or []:
        if isinstance(tool, Mapping) and isinstance(tool.get("function"), Mapping):
            function = tool["function"]
            alias = function.get("name")
            namespace, name = (namespace_aliases or {}).get(alias, (None, alias))
            item = {"id": "fc_" + token_hex(12), "type": "function_call",
                    "status": "completed", "call_id": tool.get("id"), "name": name,
                    "arguments": _normalise_arguments(function.get("arguments"))}
            if namespace is not None:
                item["namespace"] = namespace
            response["output"].append(item)
    usage = data.get("usage")
    if isinstance(usage, Mapping):
        response["usage"] = {"input_tokens": usage.get("prompt_tokens", 0),
                             "output_tokens": usage.get("completion_tokens", 0),
                             "total_tokens": usage.get("total_tokens", 0)}
    response["status"] = "completed"
    return response


@router.post("/responses")
def model_responses(body: dict[str, Any] = Body(...), authorization: str | None = Header(None)):
    settings = get_settings()
    if settings.model_api_style != "openai" or not settings.llm_api_key:
        raise HTTPException(503, "A Chat Completions model is not active")
    bearer = authorization.removeprefix("Bearer ") if authorization else ""
    if not compare_digest(bearer, settings.llm_api_key):
        raise HTTPException(401, "Invalid model adapter credential")
    payload = _chat_payload(body, settings.model_name)
    _, namespace_aliases = _converted_tools(body.get("tools"))
    try:
        upstream = requests.post(
            completion_endpoint(settings.model_provider, settings.model_api_base_url),
            headers={"Authorization": f"Bearer {settings.llm_api_key}",
                     "Content-Type": "application/json"},
            json=payload, stream=payload["stream"], timeout=(10, 120), allow_redirects=False,
        )
    except requests.RequestException:
        raise HTTPException(502, "Could not reach model service") from None
    if upstream.status_code != 200:
        status = upstream.status_code
        upstream.close()
        raise HTTPException(502, f"Model service returned HTTP {status}")
    response = _response_base(settings.model_name)
    if payload["stream"]:
        return StreamingResponse(_stream_response(upstream, response, namespace_aliases), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    try:
        return JSONResponse(_nonstream_response(upstream, response, namespace_aliases))
    finally:
        upstream.close()
