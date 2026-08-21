"""Device/state backends. Stage 1 ships the REST backend; MQTT comes later."""

from mygekko_mcp.backends.base import Backend
from mygekko_mcp.backends.rest import RestBackend

__all__ = ["Backend", "RestBackend"]
