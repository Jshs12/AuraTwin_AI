from backend.schemas.control import ControlEvent
from typing import List, Callable, Any
import uuid
import asyncio
from datetime import datetime
from backend.core.time import utc_now
import json
from contextvars import ContextVar
from contextlib import contextmanager

_event_context: ContextVar[dict] = ContextVar("auratwin_event_context", default={})

MAX_EVENT_HISTORY = 500

class EventBroadcaster:
    """Manages WebSocket subscriptions and broadcasts events."""
    _subscribers: List[Callable[[str], Any]] = []

    @classmethod
    def subscribe(cls, callback: Callable[[str], Any]):
        if callback not in cls._subscribers:
            cls._subscribers.append(callback)

    @classmethod
    def unsubscribe(cls, callback: Callable[[str], Any]):
        if callback in cls._subscribers:
            cls._subscribers.remove(callback)

    @classmethod
    async def broadcast(cls, event: ControlEvent):
        # We need a custom encoder for datetime
        def default_serializer(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serializable")
            
        message = json.dumps(event.model_dump(), default=default_serializer)
        # Iterate over a snapshot: disconnect cleanup may unsubscribe while a
        # broadcast is in progress.
        for sub in tuple(cls._subscribers):
            try:
                if asyncio.iscoroutinefunction(sub):
                    await sub(message)
                else:
                    sub(message)
            except Exception:
                # A failed send means this client is no longer a usable
                # subscriber. Drop it and keep the failure detail out of logs.
                cls.unsubscribe(sub)

class EventTrace:
    _events: List[ControlEvent] = []

    @classmethod
    @contextmanager
    def context(cls, **values):
        current = dict(_event_context.get())
        current.update(values)
        token = _event_context.set(current)
        try:
            yield
        finally:
            _event_context.reset(token)

    @classmethod
    def log_event(cls, event_type: str, zone_id: str, source: str, payload: dict, status: str = "SUCCESS") -> ControlEvent:
        payload = {**_event_context.get(), **payload}
        event = ControlEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            zone_id=zone_id,
            timestamp=utc_now(),
            source=source,
            payload=payload,
            status=status
        )
        cls._events.append(event)
        if len(cls._events) > MAX_EVENT_HISTORY:
            cls._events.pop(0)
            
        # Try to schedule a broadcast if we are inside a running event loop
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(EventBroadcaster.broadcast(event))
        except RuntimeError:
            pass # No running loop, probably in a unit test

        return event

    @classmethod
    def get_history(cls, zone_id: str) -> List[ControlEvent]:
        return [e for e in cls._events if e.zone_id == zone_id]
        
    @classmethod
    def clear(cls):
        cls._events.clear()

    @classmethod
    def clear_context_events(cls, key: str, value: str):
        cls._events = [event for event in cls._events if event.payload.get(key) != value]
