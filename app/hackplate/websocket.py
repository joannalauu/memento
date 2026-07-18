from app.hackplate.hackplate_types import HackplateWebSocket


class WSConnectionManager:
    """Broadcast manager for multi-client WebSocket connections."""

    def __init__(self):
        self.active: list[HackplateWebSocket] = []

    async def connect(self, ws: HackplateWebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: HackplateWebSocket) -> None:
        self.active.remove(ws)

    async def broadcast(self, message: str) -> None:
        for ws in self.active:
            await ws.send_text(message)

    async def broadcast_json(self, data: dict) -> None:
        for ws in self.active:
            await ws.send_json(data)


async def get_db_from_ws(websocket: HackplateWebSocket):
    """
    Async context manager equivalent of hackplate_get_session for WebSocket handlers.
    Usage:
        async with get_db_from_ws(websocket) as session:
            ...
    """
    return websocket.app.state.config.db.get_db()
