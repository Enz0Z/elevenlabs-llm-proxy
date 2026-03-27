# ElevenLabs LLM Proxy

OpenAI-compatible proxy for ElevenLabs Conversational AI.

This project exposes a minimal API surface that looks like OpenAI's Chat Completions API while routing requests to ElevenLabs APIs and WebSocket conversation endpoints.

## Features

- `GET /v1/models` to list available ElevenLabs LLMs for your API key.
- `POST /v1/chat/completions` to generate a chat response.
- Bearer-token auth using your ElevenLabs API key.
- Optional model override forwarded to ElevenLabs.
- Developer/system prompts forwarded as an agent prompt override.
- Multi-turn conversation history support.
- Configurable ElevenLabs API base URL via environment variable.

## How It Works

1. Client calls `POST /v1/chat/completions` with OpenAI-style messages.
2. The proxy requests a signed conversation WebSocket URL from ElevenLabs using:
   - `ELEVENLABS_AGENT_ID` (environment variable)
   - Bearer API key from `Authorization` header
3. It opens the WebSocket, sends:
   - conversation initiation payload (including prompt/model override)
   - the last `user` message
4. It waits for the first `agent_response` event and returns it as an OpenAI-style chat completion response.

### Conversation History

Since each request opens a new WebSocket (stateless), multi-turn context is handled by injecting previous messages into the system prompt.

When a request contains multiple `user`/`assistant` messages, the proxy:

1. Takes the **last `user` message** and sends it as the actual WebSocket user message.
2. All preceding `user` and `assistant` messages are formatted as a conversation history block and **appended to the system prompt**.

For example, given this request:

```json
{
  "messages": [
    {"role": "system", "content": "Be concise."},
    {"role": "user", "content": "Hi, my name is Enzo"},
    {"role": "assistant", "content": "Hello Enzo! How can I help you?"},
    {"role": "user", "content": "What is my name?"}
  ]
}
```

The proxy will:

- Set the agent prompt to:
  ```
  Be concise.

  ## Conversation history:
  User: Hi, my name is Enzo
  Assistant: Hello Enzo! How can I help you?
  ```
- Send `"What is my name?"` as the WebSocket user message.

If the request only has a single `user` message (no history), nothing extra is added to the prompt and it works as a simple single-turn request.

## Requirements

- Python 3.11+ (Docker image uses `python:3.11-slim`)
- ElevenLabs API key
- ElevenLabs agent ID

## Configuration

Create a `.env` file:

```env
ELEVENLABS_AGENT_ID=your_agent_id
ELEVENLABS_API_BASE_URL=https://api.elevenlabs.io
```

You can copy from `.env.example`.

Supported ElevenLabs API base URLs:

- `https://api.elevenlabs.io`
- `https://api.us.elevenlabs.io`
- `https://api.eu.residency.elevenlabs.io`
- `https://api.in.residency.elevenlabs.io`

## Run Locally

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the server:

```bash
uvicorn src.start:app --host 0.0.0.0 --port 10000
```

## Run with Docker

Build image:

```bash
docker build -t elevenlabs-llm-proxy .
```

Run container:

```bash
docker run --rm -p 10000:10000 --env-file .env elevenlabs-llm-proxy
```

Windows helper script (optional):

```bat
build.bat
```

This script builds the image and exports it to `elevenlabs-llm-proxy.tar`.

## API Usage

Set base URL:

```text
http://localhost:10000
```

### List Models

```bash
curl -X GET "http://localhost:10000/v1/models" \
  -H "Authorization: Bearer YOUR_ELEVENLABS_API_KEY"
```

`/v1/models` is backed by ElevenLabs `GET /v1/convai/llm/list`.

### Chat Completion

```bash
curl -X POST "http://localhost:10000/v1/chat/completions" \
  -H "Authorization: Bearer YOUR_ELEVENLABS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4.1-mini",
    "messages": [
      {"role": "developer", "content": "Be concise."},
      {"role": "user", "content": "Write a one-line product tagline."}
    ]
  }'
```

## Request/Response Notes

- `messages` must include at least one `user` message (and it must be the last non-system message).
- `developer` and `system` messages are merged and forwarded as prompt override.
- `assistant` and `user` messages prior to the last `user` message are injected as conversation history in the prompt.
- `tool` messages are accepted by schema but not used.
- Streaming is not implemented.
- `usage` fields are currently returned as `0`.

## Error Handling

Common cases:

- `401` invalid or missing `Authorization` header
- `500` missing `ELEVENLABS_AGENT_ID`
- `502` upstream ElevenLabs HTTP/WebSocket issues

## Project Files

- `src/start.py` - FastAPI app and endpoint logic
- `src/environ.py` - environment loading helper
- `Dockerfile` - container build/runtime
- `requirements.txt` - Python dependencies
