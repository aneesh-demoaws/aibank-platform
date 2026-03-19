"""
Alma Public AI Assistant — Voice + Text Backend
Runs on EC2 (eu-west-1), serves both:
  - WebSocket /voice → Nova 2 Sonic bidirectional audio (eu-north-1)
  - POST /chat → Text Q&A via Strands Agent + KB (eu-west-1)

Deploy: same EC2 as bis-ai-assistant, different port (8090)
"""
import asyncio
import json
import logging
import os
import boto3
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from strands import Agent, tool
from strands.models import BedrockModel
from strands.experimental.bidi import BidiAgent
from strands.experimental.bidi.models import BidiNovaSonicModel
from strands.experimental.bidi.types.events import BidiAudioInputEvent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config (override via env vars or SSM) ──
KB_ID = os.environ.get("ALMA_KB_ID", "ONBKA3KQLO")
KB_REGION = os.environ.get("ALMA_KB_REGION", "eu-west-1")
SONIC_REGION = os.environ.get("ALMA_SONIC_REGION", "eu-north-1")
VOICE_NAME = os.environ.get("ALMA_VOICE", "tiffany")
BANK_NAME = os.environ.get("ALMA_BANK_NAME", "AI Bank")
ALLOWED_ORIGINS = os.environ.get("ALMA_CORS_ORIGINS", "https://aibank.demoaws.com,http://localhost:5173").split(",")

# ── Onboarding tag — agent includes this when user wants to open account ──
ONBOARDING_TAG = "[ACCOUNT_OPENING]"

SYSTEM_PROMPT = f"""You are Alma, the friendly AI assistant for {BANK_NAME}.

ROLE: Help visitors learn about {BANK_NAME}'s products and services. You answer questions using the knowledge base.

RULES:
1. ALWAYS use search_bank_info tool FIRST for any bank-related question
2. Answer ONLY based on search results — never make up products, rates, or policies
3. If no results found, say "I don't have that specific information. Would you like me to connect you with our team?"
4. Keep responses concise: 2-4 sentences for voice, can be longer for text
5. Be warm and professional like a premium bank concierge
6. ALWAYS complete your sentences fully

ACCOUNT OPENING:
When a user expresses interest in opening an account, creating an account, signing up, or joining the bank:
1. Respond enthusiastically about their interest
2. Include the exact tag {ONBOARDING_TAG} at the END of your response
3. Example: "Great choice! I'd love to help you get started with {BANK_NAME}. Let me pull up our quick account opening form for you. {ONBOARDING_TAG}"

GENERAL KNOWLEDGE:
- You may answer general greetings and simple questions without the search tool
- For anything about {BANK_NAME} products, rates, services — ALWAYS search first
"""

# ── KB Search Tool ──
@tool
def search_bank_info(query: str) -> str:
    """Search the bank's knowledge base for product and service information."""
    client = boto3.client("bedrock-agent-runtime", region_name=KB_REGION)
    response = client.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 10}}
    )
    results = []
    for r in response.get("retrievalResults", []):
        content = r.get("content", {}).get("text", "")
        score = r.get("score", 0)
        if content and score > 0.3:
            results.append(content[:2000])
    return "\n---\n".join(results) if results else "No specific information found in the knowledge base."


# ── Text Agent ──
text_model = BedrockModel(
    model_id="eu.amazon.nova-lite-v1:0",
    region_name=KB_REGION
)

def stream_text_response(message: str):
    """Generator that yields SSE events as the agent streams its response."""
    agent = Agent(
        model=text_model,
        system_prompt=SYSTEM_PROMPT,
        tools=[search_bank_info],
        callback_handler=None,
    )
    result = agent(message, stream=True)
    full_text = ""
    for event in result:
        if hasattr(event, "data"):
            chunk = event.data
        elif isinstance(event, str):
            chunk = event
        elif isinstance(event, dict) and "data" in event:
            chunk = event["data"]
        else:
            continue
        if chunk:
            full_text += chunk
            yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

    # Final event with onboarding flag
    yield f"data: {json.dumps({'type': 'done', 'has_onboarding': ONBOARDING_TAG in full_text})}\n\n"


def get_text_response_sync(message: str) -> dict:
    """Non-streaming fallback."""
    agent = Agent(
        model=text_model,
        system_prompt=SYSTEM_PROMPT,
        tools=[search_bank_info]
    )
    result = agent(message)
    answer = str(result)
    return {"answer": answer, "has_onboarding": ONBOARDING_TAG in answer}


# ── FastAPI App ──
app = FastAPI(title="Alma Public AI Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str

@app.get("/health")
async def health():
    return JSONResponse({"status": "healthy", "service": "alma-public"})

@app.post("/chat")
async def chat(req: ChatRequest):
    """SSE streaming endpoint. Set Accept: text/event-stream for streaming, otherwise returns JSON."""
    try:
        return StreamingResponse(
            stream_text_response(req.message),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return JSONResponse({"answer": "I'm sorry, I'm having trouble right now. Please try again.", "has_onboarding": False}, status_code=500)


# ── Voice WebSocket ──
@app.websocket("/voice")
async def voice_chat(websocket: WebSocket):
    await websocket.accept()
    logger.info("Voice WebSocket connected")

    model = BidiNovaSonicModel(
        model_id="amazon.nova-2-sonic-v1:0",
        provider_config={
            "audio": {"voice": VOICE_NAME},
            "inference": {"max_tokens": 8192, "temperature": 0.7, "top_p": 0.9},
            "turn_detection": {"endpointingSensitivity": "LOW"}
        },
        client_config={"region": SONIC_REGION}
    )

    agent = BidiAgent(
        model=model,
        tools=[search_bank_info],
        system_prompt=SYSTEM_PROMPT
    )

    input_queue = asyncio.Queue()
    stop_event = asyncio.Event()

    async def ws_input():
        while not stop_event.is_set():
            try:
                data = await asyncio.wait_for(input_queue.get(), timeout=0.1)
                if data is None:
                    return None
                return data
            except asyncio.TimeoutError:
                continue
        return None

    async def ws_output(event):
        try:
            t = event.get("type", "")
            if t == "bidi_audio_stream":
                await websocket.send_json({"type": "audio", "data": event["audio"]})
            elif t == "bidi_transcript_stream":
                text = event.get("text", "")
                await websocket.send_json({
                    "type": "transcript",
                    "role": event.get("role", ""),
                    "text": text,
                    "is_final": event.get("is_final", False),
                    "has_onboarding": ONBOARDING_TAG in text if event.get("is_final") else False,
                })
            elif t == "bidi_interruption":
                await websocket.send_json({"type": "interruption"})
            elif t == "bidi_response_complete":
                await websocket.send_json({"type": "response_end"})
            elif t == "bidi_error":
                await websocket.send_json({"type": "error", "message": event.get("message", "")})
        except Exception as e:
            logger.error(f"Output error: {e}")

    async def receive_audio():
        try:
            while not stop_event.is_set():
                msg = await websocket.receive_text()
                data = json.loads(msg)
                if data.get("type") == "audio":
                    event = BidiAudioInputEvent(
                        audio=data["data"], format="pcm", sample_rate=16000, channels=1
                    )
                    await input_queue.put(event)
                elif data.get("type") == "stop":
                    stop_event.set()
                    await input_queue.put(None)
                    break
        except WebSocketDisconnect:
            stop_event.set()
            await input_queue.put(None)
        except Exception as e:
            logger.error(f"Receive error: {e}")
            stop_event.set()
            await input_queue.put(None)

    try:
        recv_task = asyncio.create_task(receive_audio())
        await agent.run(inputs=[ws_input], outputs=[ws_output])
    except Exception as e:
        logger.error(f"Agent error: {e}")
    finally:
        stop_event.set()
        recv_task.cancel()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
