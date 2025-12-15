# app/ws.py
import json
import asyncio
from typing import Dict, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

router = APIRouter()

# connections[role][client_id] = WebSocket
connections: Dict[str, Dict[str, WebSocket]] = {
    "teacher": {},
    "student": {},
}

# which room each client is in: client_rooms[role][client_id] = room_id
client_rooms: Dict[str, Dict[str, str]] = {
    "teacher": {},
    "student": {},
}

# participants[room] = set(student_ids)
participants: Dict[str, Set[str]] = {}

# alerts[room][student_id] = {label, ts, payload}
alerts: Dict[str, Dict[str, dict]] = {}

ALERT_LABELS = {"looking-away", "drowsy"}


async def send_json_safe(ws: WebSocket, data: dict):
    try:
        await ws.send_text(json.dumps(data))
    except Exception:
        # ignore errors (closed/broken websockets)
        pass


def get_room_for(role: str, client_id: str) -> str:
    return client_rooms.get(role, {}).get(client_id, "")


async def broadcast_to_teachers(room: str, data: dict):
    """send a message only to teachers in the same room"""
    teacher_dict = connections["teacher"]
    for tid, ws in list(teacher_dict.items()):
        if get_room_for("teacher", tid) == room:
            await send_json_safe(ws, data)


async def send_participants_snapshot(room: str, ws: WebSocket):
    """send current participants list for this room to a teacher"""
    room_students = sorted(list(participants.get(room, set())))
    payload = [
        {"id": sid, "joined_at": 0}  # simple structure; can extend with timestamps
        for sid in room_students
    ]
    await send_json_safe(ws, {
        "type": "participants_snapshot",
        "room": room,
        "students": payload,
    })


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    role: str = Query(...),
    client_id: str = Query(...),
    room: str = Query("", alias="room"),
):
    """
    WebSocket endpoint.
    Query params:
      ?role=teacher|student&client_id=<id>&room=<MEETING_CODE>
    """
    await websocket.accept()

    role = role.lower()
    if role not in ("teacher", "student"):
        await websocket.close(code=4001)
        return

    # normalize empty room
    room = room or "DEFAULT"

    # register connection
    connections[role][client_id] = websocket
    client_rooms[role][client_id] = room

    # for students: add to participants
    if role == "student":
        participants.setdefault(room, set()).add(client_id)
        # notify teachers in room
        await broadcast_to_teachers(room, {
            "type": "participant_joined",
            "room": room,
            "student": {"id": client_id, "joined_at": 0},
        })

    # send system message
    await send_json_safe(websocket, {
        "type": "system",
        "message": "🟢 WebSocket connected to backend",
        "client_id": client_id,
        "role": role,
        "room": room,
    })

    # if teacher: send current alerts + participants snapshot
    if role == "teacher":
        await send_json_safe(websocket, {
            "type": "alerts_snapshot",
            "alerts": alerts.get(room, {}),
            "room": room,
        })
        await send_participants_snapshot(room, websocket)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                continue

            msg_type = data.get("type")

            # student feature events
            if msg_type == "feature" and role == "student":
                this_room = get_room_for("student", client_id)
                derived = data.get("derived") or {}
                events = derived.get("events") or []
                event_label = None
                if "drowsy" in events:
                    event_label = "drowsy"
                elif "looking-away" in events:
                    event_label = "looking-away"
                elif "looking-straight" in events:
                    event_label = "looking-straight"
                elif "not-visible" in events:
                    event_label = "not-visible"
                elif events:
                    event_label = events[0]

                alert = {
                    "label": event_label,
                    "ts": int(data.get("ts", 0)),
                    "payload": data.get("payload", {}),
                }

                if event_label in ALERT_LABELS:
                    alerts.setdefault(this_room, {})[client_id] = alert
                    await broadcast_to_teachers(this_room, {
                        "type": "alert",
                        "room": this_room,
                        "id": client_id,
                        "alert": alert,
                        "message": f"{client_id} is {event_label}",
                    })
                else:
                    # clear alert if present
                    room_alerts = alerts.get(this_room, {})
                    if client_id in room_alerts:
                        del room_alerts[client_id]
                        await broadcast_to_teachers(this_room, {
                            "type": "alert_cleared",
                            "room": this_room,
                            "id": client_id,
                            "message": f"{client_id} returned to normal",
                        })

            # teacher control messages (optional)
            elif msg_type == "control" and role == "teacher":
                cmd = data.get("cmd")
                this_room = get_room_for("teacher", client_id)
                if cmd == "list_alerts":
                    await send_json_safe(websocket, {
                        "type": "alerts_snapshot",
                        "alerts": alerts.get(this_room, {}),
                        "room": this_room,
                    })

    except WebSocketDisconnect:
        # cleanup on disconnect
        pass
    finally:
        # remove connection
        try:
            del connections[role][client_id]
        except KeyError:
            pass

        this_room = get_room_for(role, client_id)
        client_rooms[role].pop(client_id, None)

        if role == "student":
            # remove from participants
            room_students = participants.get(this_room, set())
            if client_id in room_students:
                room_students.remove(client_id)
                await broadcast_to_teachers(this_room, {
                    "type": "participant_left",
                    "room": this_room,
                    "id": client_id,
                })
            # clear any alert
            room_alerts = alerts.get(this_room, {})
            if client_id in room_alerts:
                del room_alerts[client_id]
                await broadcast_to_teachers(this_room, {
                    "type": "alert_cleared",
                    "room": this_room,
                    "id": client_id,
                    "message": f"{client_id} disconnected, alert cleared",
                })
