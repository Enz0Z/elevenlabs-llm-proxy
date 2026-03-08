import json
import time
import uuid
from typing import Optional, List, Dict, Any, Literal

import requests
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

from environ import getenv

app = FastAPI()

ELEVENLABS_AGENT_ID = getenv("ELEVENLABS_AGENT_ID", "")
DEFAULT_ELEVENLABS_API_BASE_URL = "https://api.elevenlabs.io"
ELEVENLABS_API_BASE_URL = (
    getenv("ELEVENLABS_API_BASE_URL", DEFAULT_ELEVENLABS_API_BASE_URL) or DEFAULT_ELEVENLABS_API_BASE_URL
).rstrip("/")

CHAT_ROLE = Literal["developer", "system", "user", "assistant", "tool"]

class ChatMessage(BaseModel):
    role: CHAT_ROLE
    content: str

class ChatCompletionsRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]

class ResponseMessage(BaseModel):
    role: Literal["assistant"]
    content: str
    refusal: Optional[str] = None
    annotations: List[Any] = []

class Choice(BaseModel):
    index: int
    message: ResponseMessage
    logprobs: Optional[Any] = None
    finish_reason: str

class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class ChatCompletionsResponse(BaseModel):
    id: str
    object: str
    created: int
    model: str
    choices: List[Choice]
    usage: Usage
    service_tier: str = "default"

class ModelObject(BaseModel):
    id: str
    object: str = "model"
    created: int = 1700000000
    owned_by: str = "elevenlabs"

class ModelListResponse(BaseModel):
    object: str = "list"
    data: List[ModelObject]

def build_elevenlabs_api_url(path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{ELEVENLABS_API_BASE_URL}{normalized_path}"

def build_initiation_payload(model: Optional[str], developer_text: str) -> Dict[str, Any]:
    agent_prompt: Dict[str, Any] = { "prompt": developer_text or "" }

    if model:
        agent_prompt["llm"] = model

    return {
        "type": "conversation_initiation_client_data",
        "conversation_config_override": {
            "agent": {
                "prompt": agent_prompt
            }
        }
    }

def get_signed_url(api_key: str) -> str:
    url = build_elevenlabs_api_url("/v1/convai/conversation/get-signed-url")
    headers = { "xi-api-key": api_key }
    params = { "agent_id": ELEVENLABS_AGENT_ID }
    response = requests.get(url, headers=headers, params=params, timeout=20)

    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)

    data = response.json()
    signed_url = data.get("signed_url")

    if not signed_url:
        raise HTTPException(status_code=502, detail="Missing signed_url")

    return signed_url

@app.get("/v1/models", response_model=ModelListResponse)
async def list_models(
    authorization: Optional[str] = Header(default=None),
) -> ModelListResponse:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing Authorization header")

    api_key = authorization.split(" ")[1]
    url = build_elevenlabs_api_url("/v1/convai/llm/list")
    headers = { "xi-api-key": api_key }
    response = requests.get(url, headers=headers, timeout=20)

    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text)

    data = response.json()
    llms = data.get("llms", [])
    seen: set[str] = set()
    models: List[ModelObject] = []

    for item in llms:
        llm = item.get("llm")

        if isinstance(llm, str) and llm and llm != "custom-llm" and llm not in seen:
            seen.add(llm)
            models.append(ModelObject(id=llm))

    return ModelListResponse(data=models)

@app.post("/v1/chat/completions", response_model=ChatCompletionsResponse)
async def chat_completions(
    req: ChatCompletionsRequest,
    authorization: Optional[str] = Header(default=None),
) -> ChatCompletionsResponse:
    if not ELEVENLABS_AGENT_ID:
        raise HTTPException(status_code=500, detail="Missing ELEVENLABS_AGENT_ID env var")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing Authorization header")

    api_key = authorization.split(" ")[1]
    developer_parts = [m.content for m in req.messages if m.role in ("developer", "system")]
    developer_text = "\n".join([p for p in developer_parts if p.strip()])
    user_texts = [m.content for m in req.messages if m.role == "user"]

    if not user_texts:
        raise HTTPException(status_code=400, detail="No user messages provided")

    signed_url = get_signed_url(api_key)
    agent_response_text: Optional[str] = None
    websocket = None

    try:
        websocket = await connect(signed_url)

        await websocket.send(json.dumps(build_initiation_payload(req.model, developer_text)))

        for text in user_texts:
            await websocket.send(json.dumps({ "type": "user_message", "text": text }))

        async for message in websocket:
            event = json.loads(message)
            etype = event.get("type")

            if etype == "ping":
                ping_event = event.get("ping_event", {})
                event_id = ping_event.get("event_id")

                await websocket.send(json.dumps({ "type": "pong", "event_id": event_id }))
                continue

            if etype == "agent_response":
                agent_event = event.get("agent_response_event", {})
                text = agent_event.get("agent_response")

                if isinstance(text, str):
                    agent_response_text = text

                break

        if agent_response_text is None:
            raise HTTPException(status_code=502, detail="No agent_response received")

    except (ConnectionClosedOK, ConnectionClosedError) as e:
        raise HTTPException(status_code=502, detail=f"WebSocket closed: {e}")

    finally:
        if websocket:
            try:
                await websocket.close()
            except Exception:
                pass

    return ChatCompletionsResponse(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        object="chat.completion",
        created=int(time.time()),
        model=req.model or "unknown",
        choices=[
            Choice(
                index=0,
                message=ResponseMessage(role="assistant", content=agent_response_text),
                finish_reason="stop",
            )
        ],
        usage=Usage(),
    )