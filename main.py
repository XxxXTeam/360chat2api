import json
import os
import time
import uuid
from typing import Any, Dict, Generator, List, Literal, Optional

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

load_dotenv()

CHAT360_URL = os.getenv("CHAT360_URL", "https://chat.360.com/backend-api/api/common/chat")
CHAT360_DELETE_URL_TEMPLATE = os.getenv(
    "CHAT360_DELETE_URL_TEMPLATE",
    "https://chat.360.com/backend-api/api/ai/remove/conversation/{conversation_id}?search_action=",
)
CHAT360_COOKIE = os.getenv("CHAT360_COOKIE") or os.getenv("COOKIE", "")
CHAT360_ROLE = os.getenv("CHAT360_ROLE", "00000001")
CHAT360_SOURCE_TYPE = os.getenv("CHAT360_SOURCE_TYPE", "prophet_web")
CHAT360_IS_SO = os.getenv("CHAT360_IS_SO", "true").lower() == "true"
CHAT360_TYPE = int(os.getenv("CHAT360_TYPE", "0"))
CHAT360_AUTO_DELETE_PREVIOUS = os.getenv("CHAT360_AUTO_DELETE_PREVIOUS", "true").lower() == "true"
API_KEY = os.getenv("OPENAI_API_KEY", "")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DEFAULT_MODEL = os.getenv("OPENAI_MODEL_NAME", "360-chat")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "120"))

DEFAULT_HEADERS = {
    "Accept-Language": os.getenv(
        "CHAT360_ACCEPT_LANGUAGE",
        "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,zh-TW;q=0.5",
    ),
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Origin": os.getenv("CHAT360_ORIGIN", "https://chat.360.com"),
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": os.getenv(
        "CHAT360_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
    ),
    "accept": "text/event-stream",
    "sec-ch-ua": os.getenv(
        "CHAT360_SEC_CH_UA",
        '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
    ),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

app = FastAPI(title="360 Chat OpenAI-Compatible API", version="1.0.0")


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: Any
    name: Optional[str] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str = DEFAULT_MODEL
    messages: List[Message]
    stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    user: Optional[str] = None
    conversation_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "360"


conversation_map: Dict[str, str] = {}


def require_api_key(authorization: Optional[str]) -> None:
    if not API_KEY:
        return
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def normalize_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def build_prompt(messages: List[Message]) -> str:
    lines: List[str] = []
    for message in messages:
        content = normalize_message_content(message.content).strip()
        if not content:
            continue
        if message.role == "system":
            lines.append(f"系统提示：{content}")
        elif message.role == "assistant":
            lines.append(f"助手：{content}")
        elif message.role == "tool":
            lines.append(f"工具：{content}")
        else:
            lines.append(content)
    return "\n".join(lines).strip()


def make_headers(conversation_id: str) -> Dict[str, str]:
    headers = DEFAULT_HEADERS.copy()
    headers["Cookie"] = CHAT360_COOKIE
    if conversation_id:
        headers["Referer"] = f"https://chat.360.com/chat/{conversation_id}"
    else:
        headers["Referer"] = "https://chat.360.com/?src=ai_360_com"
    return headers


def parse_sse_lines(response: requests.Response) -> Generator[tuple[Optional[str], str], None, None]:
    current_event: Optional[str] = None
    data_lines: List[str] = []

    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue

        line = raw_line.strip()
        if not line:
            if data_lines:
                yield current_event, "\n".join(data_lines)
            current_event = None
            data_lines = []
            continue

        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())

    if data_lines:
        yield current_event, "\n".join(data_lines)


def parse_360_stream(
    response: requests.Response, client_conversation_id: str
) -> Generator[Dict[str, str], None, None]:
    for event, data in parse_sse_lines(response):
        if event == "100" and "####" in data:
            backend_conversation_id = data.split("####", 1)[1]
            conversation_map[client_conversation_id] = backend_conversation_id
            yield {"type": "conversation", "value": backend_conversation_id}
        elif event == "101" and "####" in data:
            yield {"type": "message", "value": data.split("####", 1)[1]}
        elif event == "200":
            yield {"type": "content", "value": data}
        elif event == "400":
            raise HTTPException(status_code=502, detail=f"360 API error: {data}")


def build_request_body(prompt: str, backend_conversation_id: str) -> Dict[str, Any]:
    return {
        "conversation_id": backend_conversation_id,
        "role": CHAT360_ROLE,
        "prompt": prompt,
        "source_type": CHAT360_SOURCE_TYPE,
        "is_regenerate": False,
        "is_so": CHAT360_IS_SO,
        "file": [],
        "page": [],
        "type": CHAT360_TYPE,
    }


def count_tokens(text: str) -> int:
    return len(text)


def build_chat_id(message_id: Optional[str]) -> str:
    return message_id or f"chatcmpl-{uuid.uuid4().hex}"


def delete_conversation(conversation_id: str, current_conversation_id: str) -> None:
    if not conversation_id or not CHAT360_AUTO_DELETE_PREVIOUS:
        return

    response = requests.delete(
        CHAT360_DELETE_URL_TEMPLATE.format(conversation_id=conversation_id),
        headers=make_headers(current_conversation_id),
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code >= 400:
        detail = response.text[:500] if response.text else "request failed"
        raise HTTPException(status_code=502, detail=f"360 delete conversation failed: {detail}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="360 delete conversation returned invalid JSON") from exc

    context = payload.get("context", {})
    if context.get("code") not in (0, None):
        raise HTTPException(status_code=502, detail=f"360 delete conversation failed: {payload}")


def cleanup_previous_conversation(previous_conversation_id: str, current_conversation_id: str) -> None:
    if not previous_conversation_id or not current_conversation_id:
        return
    if previous_conversation_id == current_conversation_id:
        return
    delete_conversation(previous_conversation_id, current_conversation_id)


def post_to_360(prompt: str, backend_conversation_id: str) -> requests.Response:
    if not CHAT360_COOKIE:
        raise HTTPException(status_code=500, detail="CHAT360_COOKIE is not configured")

    response = requests.post(
        CHAT360_URL,
        headers=make_headers(backend_conversation_id),
        json=build_request_body(prompt, backend_conversation_id),
        stream=True,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code >= 400:
        detail = response.text[:500] if response.text else "request failed"
        raise HTTPException(status_code=502, detail=f"360 API request failed: {detail}")
    return response


@app.get("/")
def root() -> Dict[str, str]:
    return {"message": "360 Chat OpenAI-compatible API is running"}


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
def list_models(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_api_key(authorization)
    return {"object": "list", "data": [ModelCard(id=DEFAULT_MODEL).model_dump()]}


@app.post("/v1/chat/completions", response_model=None)
def chat_completions(
    request: ChatCompletionRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_api_key(authorization)

    prompt = build_prompt(request.messages)
    if not prompt:
        raise HTTPException(status_code=400, detail="messages must contain at least one text content")

    client_conversation_id = request.conversation_id or str(uuid.uuid4())
    previous_backend_conversation_id = conversation_map.get(client_conversation_id, request.conversation_id or "")
    backend_conversation_id = previous_backend_conversation_id

    if request.stream:
        def event_stream() -> Generator[str, None, None]:
            response = post_to_360(prompt, backend_conversation_id)
            message_id: Optional[str] = None
            latest_backend_conversation_id = backend_conversation_id

            try:
                for item in parse_360_stream(response, client_conversation_id):
                    if item["type"] == "conversation":
                        latest_backend_conversation_id = item["value"]
                    elif item["type"] == "message":
                        message_id = item["value"]
                    elif item["type"] == "content":
                        payload = {
                            "id": build_chat_id(message_id),
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": request.model or DEFAULT_MODEL,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": item["value"]},
                                    "finish_reason": None,
                                }
                            ],
                            "conversation_id": client_conversation_id,
                        }
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

                final_payload = {
                    "id": build_chat_id(message_id),
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": request.model or DEFAULT_MODEL,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "conversation_id": client_conversation_id,
                }
                yield f"data: {json.dumps(final_payload, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                cleanup_previous_conversation(previous_backend_conversation_id, latest_backend_conversation_id)
            finally:
                response.close()

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    response = post_to_360(prompt, backend_conversation_id)
    content_parts: List[str] = []
    message_id: Optional[str] = None
    latest_backend_conversation_id = backend_conversation_id

    try:
        for item in parse_360_stream(response, client_conversation_id):
            if item["type"] == "conversation":
                latest_backend_conversation_id = item["value"]
            elif item["type"] == "message":
                message_id = item["value"]
            elif item["type"] == "content":
                content_parts.append(item["value"])
    finally:
        response.close()

    cleanup_previous_conversation(previous_backend_conversation_id, latest_backend_conversation_id)

    content = "".join(content_parts)
    prompt_tokens = count_tokens(prompt)
    completion_tokens = count_tokens(content)

    payload = {
        "id": build_chat_id(message_id),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model or DEFAULT_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "conversation_id": client_conversation_id,
    }
    return JSONResponse(payload)


if __name__ == "__main__":
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
