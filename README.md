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
# 服务监听地址，默认监听所有网卡
HOST=0.0.0.0

# 服务端口
PORT=8000

# OpenAI 兼容接口的 API Key，留空则不校验 Authorization
OPENAI_API_KEY=

# 对外暴露给 OpenAI 客户端看到的模型名
OPENAI_MODEL_NAME=360-chat

# 360 智脑聊天接口地址
CHAT360_URL=https://chat.360.com/backend-api/api/common/chat

# 360 智脑删除会话接口模板，{conversation_id} 会在运行时自动替换
CHAT360_DELETE_URL_TEMPLATE=https://chat.360.com/backend-api/api/ai/remove/conversation/{conversation_id}?search_action=

# 浏览器抓到的完整 Cookie，必填；失效后需要手动更新
CHAT360_COOKIE=

# 360 请求体里的角色 ID，通常保持默认即可
CHAT360_ROLE=00000001

# 360 请求来源类型，通常保持 prophet_web
CHAT360_SOURCE_TYPE=prophet_web

# 是否启用联网搜索参数，true / false
CHAT360_IS_SO=true

# 360 请求体里的 type 字段，通常保持 0
CHAT360_TYPE=0

# 当前轮成功后是否自动删除上一条 360 后端会话，true / false
CHAT360_AUTO_DELETE_PREVIOUS=true

# 请求 360 接口的超时时间，单位秒
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
