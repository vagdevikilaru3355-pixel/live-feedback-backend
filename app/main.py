# live-feedback-backend/app/main.py

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict

app = FastAPI()

# CORS so frontend (Vite dev server) can talk to backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- In-memory state --------------------------------------------------------

students: Dict[str, WebSocket] = {}
teachers: Dict[str, WebSocket] = {}
client_rooms: Dict[str, str] = {}  # client_id -> room_code


async def broadcast_to_teachers(room: str, payload: dict):
    """Send a JSON message to all teachers in a given room."""
    for t_id, ws in list(teachers.items()):
        if client_rooms.get(t_id) == room:
            try:
                await ws.send_json(payload)
            except Exception:
                # if sending fails, just ignore for now
                pass


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    role: str = Query(..., regex="^(student|teacher)$"),
    client_id: str = Query(...),
    room: str = Query("DEFAULT"),
):
    """
    Common WebSocket endpoint for both teacher and student.

    Query params:
      - role: "student" or "teacher"
      - client_id: any unique id (we use the name from the UI)
      - room: meeting code / room id
    """
    await websocket.accept()

    # track which room this client belongs to
    client_rooms[client_id] = room

    if role == "student":
        students[client_id] = websocket

        # notify teachers that a student joined
        await broadcast_to_teachers(
            room,
            {"type": "participant_joined", "room": room, "id": client_id},
        )

    elif role == "teacher":
        teachers[client_id] = websocket

        # when a teacher connects, send snapshot of current students in this room
        current_students = [
            s_id for s_id, r in client_rooms.items() if r == room and s_id in students
        ]
        await websocket.send_json(
            {
                "type": "participants_snapshot",
                "room": room,
                "ids": current_students,
            }
        )

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            # ---- Messages coming from STUDENT side -------------------------

            if role == "student":
                # feedback from the browser (looking_straight, looking_away, drowsy, …)
                if msg_type == "feedback":
                    feedback = data.get("feedback", "")
                    await broadcast_to_teachers(
                        room,
                        {
                            "type": "attention_feedback",
                            "room": room,
                            "id": client_id,
                            "status": feedback,
                        },
                    )

                # (optional) you can add more message types here, e.g. raw gaze data
                # elif msg_type == "gaze_points":
                #     ...

            # ---- Messages coming from TEACHER side (currently none) ------

            # if you want teacher → student messages, handle them here.

    except WebSocketDisconnect:
        # clean up when client disconnects
        try:
            websocket.close()
        except Exception:
            pass

        # remove from dicts
        if role == "student":
            students.pop(client_id, None)
        elif role == "teacher":
            teachers.pop(client_id, None)

        # tell teachers that this participant left
        room_code = client_rooms.pop(client_id, room)
        await broadcast_to_teachers(
            room_code,
            {"type": "participant_left", "room": room_code, "id": client_id},
        )
