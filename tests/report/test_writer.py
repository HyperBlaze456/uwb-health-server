"""report/writer.py의 시험 (SPEC RP-8). 아무것도 보내지 않습니다: 요청은 만들기만 하고, SDK 클라이언트 자리에 대역을 꽂습니다."""

from types import SimpleNamespace as NS

import pytest

from uwbserver.pipeline import settings
from uwbserver.report import writer


def test_settings_defaults():
    s = settings.Settings.from_env({})
    assert (s.report_model, s.report_effort, s.report_max_searches) == ("claude-opus-5-5", "high", 12)
    s = settings.Settings.from_env({"REPORT_MODEL": "m", "REPORT_EFFORT": "max", "REPORT_MAX_SEARCHES": "3"})
    assert (s.report_model, s.report_effort, s.report_max_searches) == ("m", "max", 3)


def test_request_shape():
    msgs = [{"role": "user", "content": "hi"}]
    r = writer.request(model="claude-opus-5-5", effort="high", system="S", tools=[writer.SEARCH_TOOL], messages=msgs)
    assert r["model"] == "claude-opus-5-5"
    assert r["output_config"] == {"effort": "high"}
    assert r["system"] == "S" and r["messages"] is msgs
    assert r["max_tokens"] >= 16000
    assert r["thinking"] == {"type": "adaptive"}  # 끌 수 없음(disabled·budget_tokens는 400)
    assert r["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in r["betas"]
    for forbidden in ("temperature", "top_p", "top_k", "tool_choice", "output_format"):
        assert forbidden not in r
    tool = r["tools"][0]
    assert tool["name"] == "search_papers" and tool["strict"] is True
    schema = tool["input_schema"]
    assert schema["additionalProperties"] is False and schema["required"] == ["query"]
    assert set(schema["properties"]) == {"query", "metrics", "k"}


def test_prompt_is_versioned_and_says_the_rules():
    assert writer.PROMPT_VERSION
    p = writer.SYSTEM_PROMPT
    for must in ("{vN}", "[pN]", "search_papers", "영어", "함께 보고되었다", "진단"):
        assert must in p


class Client:
    """anthropic.Anthropic 자리. beta.messages.create가 받은 인자를 적고 정해 둔 응답을 돌려줌."""

    def __init__(self, response=None, error: Exception | None = None):
        self.calls: list[dict] = []
        self._response, self._error = response, error
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


def response(content, stop_reason="end_turn", stop_details=None, model="claude-opus-5-5"):
    return NS(content=content, stop_reason=stop_reason, stop_details=stop_details, model=model)


def test_turn_reads_text_and_tool_calls_by_block_type():
    content = [NS(type="thinking", thinking=""), NS(type="text", text="찾아 보겠습니다."),
               NS(type="tool_use", id="tu1", name="search_papers", input={"query": "gait speed", "k": 3})]  # fmt: skip
    c = Client(response(content, "tool_use"))
    m = writer.ClaudeModel("claude-opus-5-5", "high", client=c)
    t = m.turn("S", [writer.SEARCH_TOOL], [{"role": "user", "content": "q"}])
    assert t.stop_reason == "tool_use" and t.text == "찾아 보겠습니다."
    assert t.tool_calls == (writer.ToolCall("tu1", "search_papers", {"query": "gait speed", "k": 3}),)
    assert t.content == content  # 받은 블록 전부를 그대로 대화에 되돌림(thinking 포함)
    assert c.calls[0]["model"] == "claude-opus-5-5" and c.calls[0]["output_config"] == {"effort": "high"}


def test_turn_refusal_carries_the_reason():
    details = NS(type="refusal", category="bio", explanation="declined")
    t = writer.ClaudeModel("m", "high", client=Client(response([], "refusal", details))).turn("S", [], [])
    assert t.stop_reason == "refusal" and t.refusal == "bio: declined"


def test_history_drops_blocks_before_a_fallback_boundary():
    content = [NS(type="thinking", thinking=""), NS(type="text", text="a"), NS(type="fallback"),
               NS(type="thinking", thinking=""), NS(type="text", text="b")]  # fmt: skip
    t = writer.ClaudeModel("m", "high", client=Client(response(content))).turn("S", [], [])
    assert [b.type for b in t.content] == ["text", "fallback", "thinking", "text"]


def test_missing_credentials_is_a_clear_error():
    err = TypeError("Could not resolve authentication method. Expected one of api_key, auth_token, or credentials")
    with pytest.raises(writer.MissingCredentials, match="ANTHROPIC_API_KEY"):
        writer.ClaudeModel("m", "high", client=Client(error=err)).turn("S", [], [])


def test_other_type_errors_are_not_hidden():
    with pytest.raises(TypeError, match="something else"):
        writer.ClaudeModel("m", "high", client=Client(error=TypeError("something else"))).turn("S", [], [])


def test_connection_error_becomes_writer_error():
    import anthropic
    import httpx2

    err = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://example.invalid/v1/messages"))
    with pytest.raises(writer.WriterError, match="connection"):
        writer.ClaudeModel("m", "high", client=Client(error=err)).turn("S", [], [])
