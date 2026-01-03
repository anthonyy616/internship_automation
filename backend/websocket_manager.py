"""
WebSocket connection manager for real-time updates.
Broadcasts activity logs to all connected clients.
"""

import json
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import WebSocket


class ConnectionManager:
    """Manages WebSocket connections and broadcasts messages."""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket):
        """Accept and store a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        print(f"WebSocket connected. Total connections: {len(self.active_connections)}")
    
    async def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        print(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")
    
    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast a message to all connected clients."""
        if not self.active_connections:
            return
        
        message_json = json.dumps(message)
        disconnected = []
        
        for connection in self.active_connections:
            try:
                await connection.send_text(message_json)
            except Exception as e:
                print(f"Error sending message: {e}")
                disconnected.append(connection)
        
        # Clean up disconnected clients
        async with self._lock:
            for conn in disconnected:
                if conn in self.active_connections:
                    self.active_connections.remove(conn)
    
    async def send_log(
        self,
        level: str,
        action: str,
        message: str,
        region: Optional[str] = None,
        metadata: Optional[Dict] = None
    ):
        """Send a log message to all connected clients."""
        log_entry = {
            'type': 'log',
            'timestamp': datetime.utcnow().isoformat(),
            'level': level,
            'action': action,
            'message': message,
            'region': region,
            'metadata': metadata or {}
        }
        await self.broadcast(log_entry)
    
    async def send_stats_update(self, stats: Dict[str, Any]):
        """Send statistics update to all clients."""
        await self.broadcast({
            'type': 'stats',
            'data': stats
        })
    
    async def send_job_update(self, job: Dict[str, Any], action: str = 'created'):
        """Send job update to all clients."""
        await self.broadcast({
            'type': 'job',
            'action': action,  # 'created', 'updated', 'applied', 'emailed'
            'data': job
        })
    
    async def send_status_update(self, status: str, details: Optional[str] = None):
        """Send bot status update to all clients."""
        await self.broadcast({
            'type': 'status',
            'status': status,  # 'running', 'paused', 'stopped', 'error'
            'details': details
        })


# Global WebSocket manager instance
ws_manager = ConnectionManager()


class Logger:
    """
    Unified logger that sends to both console, WebSocket, and database.
    """
    
    def __init__(self, ws_manager: ConnectionManager, db_service=None):
        self.ws = ws_manager
        self.db = db_service
    
    async def log(
        self,
        level: str,
        action: str,
        message: str,
        region: Optional[str] = None,
        metadata: Optional[Dict] = None
    ):
        """Log a message to all outputs."""
        # Console output with color
        color_codes = {
            'INFO': '\033[94m',      # Blue
            'SUCCESS': '\033[92m',   # Green
            'WARNING': '\033[93m',   # Yellow
            'ERROR': '\033[91m',     # Red
            'DEBUG': '\033[90m'      # Gray
        }
        reset = '\033[0m'
        color = color_codes.get(level, '')
        
        timestamp = datetime.utcnow().strftime('%H:%M:%S')
        region_str = f"[{region}]" if region else ""
        print(f"{color}[{timestamp}] [{level}] [{action}] {region_str} {message}{reset}")
        
        # WebSocket broadcast
        await self.ws.send_log(level, action, message, region, metadata)
        
        # Database logging (if available)
        if self.db and self.db.is_connected:
            await self.db.log_activity(level, action, message, region, metadata)
    
    async def info(self, action: str, message: str, region: Optional[str] = None, metadata: Optional[Dict] = None):
        await self.log('INFO', action, message, region, metadata)
    
    async def success(self, action: str, message: str, region: Optional[str] = None, metadata: Optional[Dict] = None):
        await self.log('SUCCESS', action, message, region, metadata)
    
    async def warning(self, action: str, message: str, region: Optional[str] = None, metadata: Optional[Dict] = None):
        await self.log('WARNING', action, message, region, metadata)
    
    async def error(self, action: str, message: str, region: Optional[str] = None, metadata: Optional[Dict] = None):
        await self.log('ERROR', action, message, region, metadata)
    
    async def debug(self, action: str, message: str, region: Optional[str] = None, metadata: Optional[Dict] = None):
        await self.log('DEBUG', action, message, region, metadata)
