from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set
import json
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Live Feedback System API")

# CORS Configuration - UPDATE THIS WITH YOUR VERCEL URL
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "https://your-frontend-name.vercel.app",  # ⚠️ REPLACE WITH YOUR VERCEL URL
        "https://*.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ... rest of the code stays the same
# Connection Manager for WebSocket
class ConnectionManager:
    def __init__(self):
        # Store active connections: {room_id: {role: [connections]}}
        self.active_connections: Dict[str, Dict[str, Set[WebSocket]]] = {}
        # Store student metadata: {room_id: {student_id: {name, connection}}}
        self.students_metadata: Dict[str, Dict[str, dict]] = {}
        
    async def connect(self, websocket: WebSocket, room_id: str, role: str, user_id: str = None, name: str = None):
        await websocket.accept()
        
        if room_id not in self.active_connections:
            self.active_connections[room_id] = {"teacher": set(), "student": set()}
            self.students_metadata[room_id] = {}
        
        self.active_connections[room_id][role].add(websocket)
        
        # Store student metadata
        if role == "student" and user_id:
            self.students_metadata[room_id][user_id] = {
                "name": name or f"Student {user_id}",
                "connection": websocket,
                "joined_at": datetime.now().isoformat(),
                "status": "active"
            }
            
            # Notify teacher about new student
            await self.broadcast_to_teachers(room_id, {
                "type": "student_joined",
                "student_id": user_id,
                "name": name or f"Student {user_id}",
                "timestamp": datetime.now().isoformat()
            })
        
        logger.info(f"{role.capitalize()} {user_id or 'unknown'} joined room {room_id}")
        
        # Send current participants list to the new connection
        if role == "teacher":
            await self.send_participants_list(websocket, room_id)
    
    def disconnect(self, websocket: WebSocket, room_id: str, role: str, user_id: str = None):
        if room_id in self.active_connections:
            if websocket in self.active_connections[room_id][role]:
                self.active_connections[room_id][role].remove(websocket)
            
            # Remove student metadata and notify teacher
            if role == "student" and user_id and room_id in self.students_metadata:
                if user_id in self.students_metadata[room_id]:
                    student_name = self.students_metadata[room_id][user_id]["name"]
                    del self.students_metadata[room_id][user_id]
                    
                    # Notify teacher about student leaving
                    import asyncio
                    asyncio.create_task(self.broadcast_to_teachers(room_id, {
                        "type": "student_left",
                        "student_id": user_id,
                        "name": student_name,
                        "timestamp": datetime.now().isoformat()
                    }))
            
            # Clean up empty rooms
            if not self.active_connections[room_id]["teacher"] and not self.active_connections[room_id]["student"]:
                del self.active_connections[room_id]
                if room_id in self.students_metadata:
                    del self.students_metadata[room_id]
        
        logger.info(f"{role.capitalize()} {user_id or 'unknown'} left room {room_id}")
    
    async def send_participants_list(self, websocket: WebSocket, room_id: str):
        """Send list of all participants in the room"""
        if room_id in self.students_metadata:
            participants = [
                {
                    "student_id": sid,
                    "name": info["name"],
                    "joined_at": info["joined_at"],
                    "status": info["status"]
                }
                for sid, info in self.students_metadata[room_id].items()
            ]
            
            await websocket.send_json({
                "type": "participants_list",
                "participants": participants,
                "count": len(participants)
            })
    
    async def broadcast_to_teachers(self, room_id: str, message: dict):
        """Send message to all teachers in the room"""
        if room_id in self.active_connections:
            disconnected = set()
            for connection in self.active_connections[room_id]["teacher"]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Error sending to teacher: {e}")
                    disconnected.add(connection)
            
            # Clean up disconnected connections
            for conn in disconnected:
                self.active_connections[room_id]["teacher"].discard(conn)
    
    async def send_to_student(self, room_id: str, student_id: str, message: dict):
        """Send message to a specific student"""
        if room_id in self.students_metadata and student_id in self.students_metadata[room_id]:
            connection = self.students_metadata[room_id][student_id]["connection"]
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending to student {student_id}: {e}")

manager = ConnectionManager()

@app.get("/")
async def root():
    return {
        "message": "Live Feedback System API",
        "version": "1.0.0",
        "status": "running"
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/rooms/{room_id}/stats")
async def get_room_stats(room_id: str):
    """Get statistics for a specific room"""
    if room_id not in manager.active_connections:
        return {
            "room_id": room_id,
            "exists": False
        }
    
    return {
        "room_id": room_id,
        "exists": True,
        "teachers_count": len(manager.active_connections[room_id]["teacher"]),
        "students_count": len(manager.active_connections[room_id]["student"]),
        "students": [
            {
                "student_id": sid,
                "name": info["name"],
                "joined_at": info["joined_at"]
            }
            for sid, info in manager.students_metadata.get(room_id, {}).items()
        ]
    }

@app.websocket("/ws/{room_id}/{role}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, role: str, user_id: str):
    """
    WebSocket endpoint for real-time communication
    - room_id: Unique identifier for the class/session
    - role: Either 'teacher' or 'student'
    - user_id: Unique identifier for the user
    """
    
    if role not in ["teacher", "student"]:
        await websocket.close(code=1008, reason="Invalid role")
        return
    
    # Get student name from query params if provided
    name = None
    try:
        query_params = dict(websocket.query_params)
        name = query_params.get("name")
    except:
        pass
    
    await manager.connect(websocket, room_id, role, user_id, name)
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message = json.loads(data)
            
            logger.info(f"Received from {role} {user_id}: {message.get('type', 'unknown')}")
            
            # Handle different message types
            if role == "student":
                # Student sends feedback events to teacher
                if message.get("type") in ["drowsy", "looking_away", "distracted", "engaged", "alert"]:
                    # Add metadata
                    message["student_id"] = user_id
                    message["student_name"] = manager.students_metadata.get(room_id, {}).get(user_id, {}).get("name", user_id)
                    message["timestamp"] = datetime.now().isoformat()
                    
                    # Broadcast to all teachers in the room
                    await manager.broadcast_to_teachers(room_id, message)
                
                # Handle status updates
                elif message.get("type") == "status_update":
                    if room_id in manager.students_metadata and user_id in manager.students_metadata[room_id]:
                        manager.students_metadata[room_id][user_id]["status"] = message.get("status", "active")
            
            elif role == "teacher":
                # Teacher sends commands or requests
                if message.get("type") == "request_participants":
                    await manager.send_participants_list(websocket, room_id)
                
                elif message.get("type") == "message_to_student":
                    # Send message to specific student
                    target_student = message.get("student_id")
                    if target_student:
                        await manager.send_to_student(room_id, target_student, {
                            "type": "teacher_message",
                            "message": message.get("message", ""),
                            "timestamp": datetime.now().isoformat()
                        })
    
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id, role, user_id)
        logger.info(f"{role.capitalize()} {user_id} disconnected from room {room_id}")
    
    except Exception as e:
        logger.error(f"Error in WebSocket connection: {e}")
        manager.disconnect(websocket, room_id, role, user_id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
