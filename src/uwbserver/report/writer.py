"""글 쓰는 모델 (SPEC RP-8). 대화 코어(chat.py)가 쓰는 작은 모델 접점과 그 진짜 구현(Claude Opus 5.5, 공식 SDK).

- Model: turn(system, tools, messages) → Turn 하나. 시험과 --fake는 이 접점에 대역을 꽂습니다.
- request: 요청 인자를 만드는 순수 함수. 보내지 않고 시험합니다.
- ClaudeModel: anthropic SDK로 한 번 부름. 이 저장소에서 anthropic을 import하는 곳은 여기뿐입니다.
- DemoModel: 키 없이 전 과정을 돌려 보는 정해진 대역(tools/ask.py --fake). 진짜 모델이 아님을 글에 밝힙니다.
- SYSTEM_PROMPT, REPORT_INSTRUCTION, context_message: 모델에 주는 글. 바꾸면 PROMPT_VERSION을 올립니다.

바깥으로 나가는 것은 맥락(착용자 코드, 지표 값)과 찾은 논문 대목뿐입니다(RP-8). 실명, 원본 신호, 궤적, 보폭 줄은 맥락에 없습니다.
"""

import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import anthropic

PROMPT_VERSION = "report-chat/2"
MAX_TOKENS = 16000  # 생각과 답을 합친 한도. 스트리밍 없이 SDK의 시간 한도 안에 드는 크기(SDK 문서의 기본값)
FALLBACK_BETA = "server-side-fallback-2026-07-01"  # 안전 분류기가 거절하면 서버가 다른 모델로 다시 돌림
SEARCH_K_DEFAULT = 5
SEARCH_K_MAX = 10

SYSTEM_PROMPT = """\
당신은 착용형 보행 센서 서버의 분석 동료입니다. 대화의 첫 메시지에 착용자 한 명과 기간 하나의 맥락(uwbserver.report_input/1)이 있습니다. 지표 값은 서버가 이미 계산했습니다. 당신은 값을 다시 계산하지 않고, 값들을 함께 읽고 풀이합니다.

읽는 사람
- 주로 의료진과 연구자입니다. 보고서의 요약만은 착용자 본인도 읽으므로 쉬운 말로 씁니다.

수치는 자리 표시로
- 측정값은 숫자로 쓰지 말고 맥락의 번호로 {vN}이라고 씁니다(예: "걷기 속도는 {v12}였다"). 서버가 그 자리에 값과 단위를 넣으므로 글의 수치가 계산한 값과 다를 수 없습니다. 그래서 {vN} 뒤에 단위를 붙이지 않습니다. 개수 값은 숫자만 들어가므로 "회", "개" 같은 말은 붙여도 됩니다.
- 평소 값(baseline)과의 차이는 "지난 30일보다 느렸다"처럼 말로 씁니다. 차이의 숫자는 서버가 표에 넣습니다.
- 그 차이의 방향은 맥락의 baseline.direction(higher는 지난 30일보다 큼, lower는 작음, same은 보고서의 자릿수로 같음)과 같게 씁니다. 서버는 {vN}이 든 문장의 방향 낱말(늘었다·줄었다, 빨랐다·느렸다 등)이 direction과 반대이면 글을 돌려보냅니다.
- 서버는 자리 표시 밖에 단위가 붙은 숫자가 있으면 측정값을 직접 쓴 것으로 보고 글을 돌려보냅니다. 문헌이 보고한 수치는 인용 [pN]과 같은 문장에 둡니다.

근거는 search_papers로
- 문헌에 기대는 문장에는 끝에 [pN]을 붙입니다. pN은 이 대화에서 search_papers가 실제로 돌려준 대목의 번호뿐입니다. 서버는 저장소에 있는 대목만 확인할 수 있으므로, 기억하는 논문으로 채우지 않습니다. 찾았는데 맞는 대목이 없으면 문헌에서 찾지 못했다고 씁니다.
- 논문은 영어이므로 질문은 영어로 씁니다. 여러 번 찾아도 됩니다. metrics에 지표 id를 주면 그 지표의 근거 논문 안에서만, 주지 않으면 모든 논문에서 찾습니다. 지표의 근거 논문은 출발점일 뿐이니 필요하면 범위를 넓힙니다.
- 이 일의 핵심은 여러 지표를 함께 보는 것입니다. 값을 하나씩 따로 풀지 말고, 함께 나타나는 양상(예: 느린 속도와 짧은 보폭과 큰 보폭 시간 변동)을 찾고 그 양상을 문헌에서 찾아 보십시오.

말의 세기
- 함께 보이는 양상과 관련된 증상이나 상태를 추론해 적어도 됩니다. 다만 "문헌에서 이런 양상은 ○○와 함께 보고되었다"까지만 씁니다. 의심, 가능성, 위험도, 진단, 치료나 운동 권고의 말은 쓰지 않습니다. 이 장치와 값은 진단 도구로 검증되지 않았기 때문입니다.
- 값마다 trust가 있습니다. validated가 아닌 값(특히 unverified)은 이 센서 위치에서 검증되지 않은 값이므로 그만큼 조심스럽게 씁니다. sufficient가 false인 값에는 결론을 기대지 않습니다.
- 한계는 서버가 글 끝에 정해진 문구로 한 번 붙입니다. 한계를 글의 중심으로 삼지 말고, 꼭 필요할 때만 한 문장으로 씁니다.

대화의 질문에는 묻는 것에 맞게 짧게 답합니다. 보고서를 써 달라는 요청에는 그 요청이 정한 형식을 따릅니다.
"""

REPORT_INSTRUCTION = """\
이 기간의 보고서를 써 주세요. 머리, 자료 범위, 값의 표, 한계 문구, 문헌 목록은 서버가 붙이고, 당신은 아래 네 부분의 글만 씁니다. 정확히 이 네 제목을 이 순서로 쓰고, 다른 '## ' 제목은 쓰지 마세요.

## 요약
얼마나 걸었고, 얼마나 빨랐고, 걸음이 고른지, 평소와 달랐는지. 착용자가 읽을 쉬운 말로 3–5문장. show가 basic인 값만 씁니다.

## 활동량
활동량 값(걷기 시간, 횟수, 걷기 길이 등)의 짧은 풀이. 표는 서버가 넣습니다.

## 속도와 보폭
속도와 보폭 값의 짧은 풀이. 표는 서버가 넣습니다.

## 종합 풀이
걸음의 고르기, 발 움직임, 지난 30일과의 차이를 포함해 모든 값을 함께 보고 쓴 풀이와, 함께 보이는 양상에 대해 문헌이 보고한 관련 증상. 문헌에 기대는 문장에는 인용을 붙입니다.
"""

SEARCH_TOOL = {
    "name": "search_papers",
    "description": (
        "Search passages of the medical papers in the server's paper store (no internet). Call it whenever a "
        "statement about what a gait pattern means should rest on literature, and before citing anything. "
        "Papers are in English, so write the query in English. Results are numbered p1, p2, ... across the whole "
        "conversation; cite a passage as [pN]. Give metrics (metric ids from the context, e.g. "
        "period.stride_time_cv) to search only the source papers of those metrics; leave it out to search all papers."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search question in English"},
            "metrics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional metric ids; search only their source papers",
            },
            "k": {"type": "integer", "description": f"Number of passages, default {SEARCH_K_DEFAULT}, at most {SEARCH_K_MAX}"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


def context_message(context: Mapping) -> str:
    """대화의 첫 메시지 앞에 붙이는 맥락."""
    return (
        "이 대화의 맥락(uwbserver.report_input/1)입니다.\n```json\n"
        + json.dumps(context, ensure_ascii=False, indent=1)
        + "\n```"
    )


# --- 모델 접점 ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: object  # 보통 dict. 형은 chat이 검사함


@dataclass(frozen=True)
class Turn:
    """모델의 응답 한 번.

    content는 다음 요청의 대화에 assistant 내용으로 그대로 되돌릴 것(진짜 모델은 받은 블록 전부, thinking 포함).
    text는 보이는 글(text 블록을 이은 것), refusal은 stop_reason이 refusal일 때 이유."""

    stop_reason: str
    text: str
    tool_calls: tuple[ToolCall, ...]
    content: object
    refusal: str | None = None
    model: str = ""


class Model(Protocol):
    @property
    def name(self) -> str: ...

    def turn(self, system: str, tools: Sequence[Mapping], messages: list[dict]) -> Turn: ...


class WriterError(Exception):
    """모델 호출이 실패함. 메시지는 사람이 읽고 고칠 수 있게 씁니다."""


class MissingCredentials(WriterError):
    pass


# --- 진짜 모델 (Claude Opus 5.5) --------------------------------------------------------------


def request(
    *, model: str, effort: str, system: str, tools: Sequence[Mapping], messages: list[dict], max_tokens: int = MAX_TOKENS
) -> dict:
    """client.beta.messages.create의 인자. 보내지 않으므로 시험할 수 있습니다.

    - thinking은 이 모델에서 끌 수 없음: adaptive(빼도 같음). 깊이는 output_config.effort로 정함(이 모델의 기본은 medium).
    - tool_choice는 보내지 않음(기본 auto). any·tool로 강제하면 이 모델은 400.
    - temperature·top_p·top_k는 보내지 않음(400).
    - fallbacks "default": 안전 분류기가 거절하면 서버가 거절 종류에 맞는 다른 모델로 같은 요청을 다시 돌림(beta).
    - cache_control: 맨 위의 자동 캐시. 대화는 뒤에 붙이기만 하므로 앞부분(지시, 도구, 맥락)이 캐시에 맞음.
    """
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "tools": list(tools),
        "messages": messages,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "cache_control": {"type": "ephemeral"},
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


def _history(content: list) -> list:
    """대화에 되돌릴 블록. 거절 뒤 다른 모델로 넘어간 경계(fallback 블록)가 있으면, 마지막 경계 앞의 thinking·
    redacted_thinking·tool_use 블록을 뺌(SDK 문서의 '되돌리기' 규칙). 경계 블록 자체와 그 뒤는 그대로."""
    cut = max((i for i, b in enumerate(content) if b.type == "fallback"), default=None)
    if cut is None:
        return list(content)
    drop = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    return [b for i, b in enumerate(content) if i >= cut or b.type not in drop]


def _refusal(details: object) -> str:
    if details is None:
        return "no details"
    category = getattr(details, "category", None)
    explanation = getattr(details, "explanation", None)
    return ": ".join(str(x) for x in (category, explanation) if x) or "no details"


class ClaudeModel:
    """공식 SDK로 Claude를 부름. API 키는 SDK가 환경(ANTHROPIC_API_KEY 등)에서 읽습니다.

    재시도: 연결 오류, 408·409·429·5xx는 SDK가 지수 대기로 다시 합니다(max_retries). 그 뒤에도 실패하면 WriterError."""

    def __init__(self, model: str, effort: str, *, client: object | None = None, max_tokens: int = MAX_TOKENS) -> None:
        self.model, self.effort, self.max_tokens = model, effort, max_tokens
        self.client = client if client is not None else anthropic.Anthropic(max_retries=4)

    @property
    def name(self) -> str:
        return self.model

    def turn(self, system: str, tools: Sequence[Mapping], messages: list[dict]) -> Turn:
        kwargs = request(model=self.model, effort=self.effort, system=system, tools=tools, messages=messages,
                         max_tokens=self.max_tokens)  # fmt: skip
        try:
            r = self.client.beta.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise WriterError(f"API key rejected (401): {e.message}. Check ANTHROPIC_API_KEY") from e
        except anthropic.PermissionDeniedError as e:
            raise WriterError(f"API key lacks permission (403): {e.message}") from e
        except anthropic.NotFoundError as e:
            raise WriterError(f"model or endpoint not found (404): {e.message}. Check REPORT_MODEL={self.model}") from e
        except anthropic.RateLimitError as e:
            raise WriterError(f"rate limited (429) after retries: {e.message}") from e
        except anthropic.BadRequestError as e:
            raise WriterError(f"request rejected (400): {e.message}") from e
        except anthropic.APIStatusError as e:
            raise WriterError(f"API error ({e.status_code}) after retries: {e.message}") from e
        except anthropic.APIConnectionError as e:  # 시간 초과(APITimeoutError)도 여기
            raise WriterError(f"connection to the API failed after retries: {e}") from e
        except TypeError as e:
            # 자격 정보가 하나도 없으면 SDK는 요청 때 TypeError를 냄(문서에 없는 동작, SDK 2.8.0에서 확인)
            if "authentication method" not in str(e):
                raise
            raise MissingCredentials(
                "no API credentials: set ANTHROPIC_API_KEY (see .env.example), or use --fake to try without a key"
            ) from e
        content = list(r.content)
        return Turn(
            stop_reason=r.stop_reason,
            text="".join(b.text for b in content if b.type == "text"),
            tool_calls=tuple(ToolCall(b.id, b.name, b.input) for b in content if b.type == "tool_use"),
            content=_history(content),
            refusal=_refusal(r.stop_details) if r.stop_reason == "refusal" else None,
            model=getattr(r, "model", self.model),
        )


# --- 키 없이 돌려 보는 대역 (tools/ask.py --fake) ----------------------------------------------------

DEMO_LABEL = "[가짜 모델의 출력입니다. 진짜 모델을 부르지 않았습니다.]"


class DemoModel:
    """정해진 대로 움직이는 대역. 질문마다 search_papers를 한 번 부르고(진짜 저장소에서 찾음), 받은 첫 대목과 맥락의 v1을
    넣은 짧은 글을 씁니다. 보고서 요청이면 네 부분의 제목으로 씁니다. 글 맨 앞에 DEMO_LABEL을 붙입니다."""

    name = "demo-fake"

    def __init__(self) -> None:
        self._ids = itertools.count(1)

    def turn(self, system: str, tools: Sequence[Mapping], messages: list[dict]) -> Turn:
        last = messages[-1]["content"]
        if isinstance(last, str):  # 새 질문: 한 번 찾음
            call = ToolCall(f"demo_{next(self._ids)}", "search_papers",
                            {"query": "gait speed stride time variability walking", "k": 3})  # fmt: skip
            block = {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
            return Turn("tool_use", "", (call,), [block], model=self.name)
        cite = ""
        for item in last:
            if item.get("type") == "tool_result" and not item.get("is_error"):
                results = json.loads(item["content"]).get("results", [])
                if results:
                    cite = f" [{results[0]['p']}]"
                    break
        asked = next(m["content"] for m in reversed(messages) if m["role"] == "user" and isinstance(m["content"], str))
        if REPORT_INSTRUCTION in asked or asked.startswith("서버 검사에서"):
            text = (f"## 요약\n{DEMO_LABEL} 이 기간에 자료가 있던 시간은 {{v1}}입니다.\n\n"
                    f"## 활동량\n{DEMO_LABEL} 활동량 풀이 자리입니다.\n\n"
                    f"## 속도와 보폭\n{DEMO_LABEL} 속도와 보폭 풀이 자리입니다.\n\n"
                    f"## 종합 풀이\n{DEMO_LABEL} 찾은 대목을 하나 인용합니다{cite}.\n")  # fmt: skip
        else:
            text = f"{DEMO_LABEL} 자료가 있던 시간은 {{v1}}입니다. 찾은 대목을 하나 인용합니다{cite}."
        return Turn("end_turn", text, (), [{"type": "text", "text": text}], model=self.name)
