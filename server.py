import os

from dotenv import load_dotenv

from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    Request,
)

from fastapi.responses import Response
from utils.session_state import call_sessions
from agent_bengali import run_bot

load_dotenv()

app = FastAPI()

# --------------------------------------------------
# XML Endpoint
# --------------------------------------------------

@app.get("/answerCall")
async def get_answer_xml(request: Request):
    params = dict(request.query_params)

    call_uuid = params.get("CallUUID")

    from_number = params.get("From")

    # SAVE CALL DATA
    call_sessions[call_uuid] = {
        "from_number": from_number,
    }

    domain = os.getenv(
        "DOMAIN",
        "site-strain-journey-college.trycloudflare.com"
    )

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream
        bidirectional="true"
        keepCallAlive="true"
        contentType="audio/x-mulaw;rate=8000">
        wss://{domain}/ws
    </Stream>
</Response>"""

    return Response(
        content=xml_content,
        media_type="application/xml"
    )

# --------------------------------------------------
# Websocket
# --------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):

    await websocket.accept()

    print("✅ WebSocket connected")

    try:
        await run_bot(websocket)

    except WebSocketDisconnect:
        print("❌ WebSocket disconnected")

    except Exception as e:
        print(f"❌ Error: {e}")

# --------------------------------------------------
# Main
# --------------------------------------------------

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )