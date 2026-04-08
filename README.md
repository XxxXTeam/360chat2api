# 360Chat2API

把 360 智脑网页接口包装成 OpenAI 标准兼容的 `/v1/chat/completions` 服务。

## 已支持

- OpenAI 风格的 `/v1/chat/completions`
- OpenAI 风格的 `/v1/models`
- 流式响应 `stream: true`
- 非流式响应
- 多轮对话续聊
- 当前轮完成后自动删除上一条 360 会话
- 所有配置通过 `.env` 管理

## 安装

```bash
pip install -e .
```

## `.env` 配置

可直接参考 `.env.example`：

```env
HOST=0.0.0.0
PORT=8000

OPENAI_API_KEY=
OPENAI_MODEL_NAME=360-chat

CHAT360_URL=https://chat.360.com/backend-api/api/common/chat
CHAT360_DELETE_URL_TEMPLATE=https://chat.360.com/backend-api/api/ai/remove/conversation/{conversation_id}?search_action=
CHAT360_COOKIE=你的完整cookie
CHAT360_ROLE=00000001
CHAT360_SOURCE_TYPE=prophet_web
CHAT360_IS_SO=true
CHAT360_TYPE=0
CHAT360_AUTO_DELETE_PREVIOUS=true
REQUEST_TIMEOUT=120
```

说明：

- `CHAT360_COOKIE` 必填，直接填浏览器抓到的完整 cookie 串。
- `OPENAI_API_KEY` 选填；如果填写，则请求时必须带 `Authorization: Bearer 你的key`。
- `conversation_id` 是本项目暴露给客户端的会话 ID，可直接继续传回接口实现多轮对话。
- `CHAT360_AUTO_DELETE_PREVIOUS=true` 时，当前轮成功结束后会删除上一条 360 后端会话，避免会话列表持续堆积。

## 启动

```bash
python main.py
```

或：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## OpenAI 标准调用

### 1. 查看模型

```bash
curl http://127.0.0.1:8000/v1/models
```

如果你配置了 `OPENAI_API_KEY`：

```bash
curl http://127.0.0.1:8000/v1/models \
  -H "Authorization: Bearer your-key"
```

### 2. 非流式对话

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "360-chat",
    "messages": [
      {"role": "user", "content": "你好啊"}
    ]
  }'
```

返回里会额外带一个 `conversation_id` 字段，用于续聊。

### 3. 流式对话

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -N \
  -d '{
    "model": "360-chat",
    "stream": true,
    "messages": [
      {"role": "user", "content": "你好啊"}
    ]
  }'
```

### 4. 继续上一轮会话

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "360-chat",
    "conversation_id": "上一轮返回的conversation_id",
    "messages": [
      {"role": "user", "content": "你是什么模型啊"}
    ]
  }'
```

## Python OpenAI SDK 示例

```python
from openai import OpenAI

client = OpenAI(
    api_key="test",
    base_url="http://127.0.0.1:8000/v1",
)

resp = client.chat.completions.create(
    model="360-chat",
    messages=[
        {"role": "user", "content": "你好啊"}
    ],
)

print(resp.choices[0].message.content)
print(resp.model_extra.get("conversation_id"))
```

多轮对话：

```python
from openai import OpenAI

client = OpenAI(api_key="test", base_url="http://127.0.0.1:8000/v1")

first = client.chat.completions.create(
    model="360-chat",
    messages=[{"role": "user", "content": "你好啊"}],
)

conversation_id = first.model_extra.get("conversation_id")

second = client.chat.completions.create(
    model="360-chat",
    messages=[{"role": "user", "content": "你是什么模型啊"}],
    extra_body={"conversation_id": conversation_id},
)

print(second.choices[0].message.content)
```

## 注意

- 360 网页接口依赖登录态，`CHAT360_COOKIE` 失效后需要更新。
- 自动删除逻辑只会删除“上一条后端会话”，不会删除当前仍在使用的会话。
- 本项目会把 OpenAI 请求里的多条消息拼成单个 prompt 发给 360，这是为了兼容标准格式并保持实现简单。
- `temperature`、`top_p`、`max_tokens` 等参数当前仅做兼容接收，不会真正传给 360 后端。
