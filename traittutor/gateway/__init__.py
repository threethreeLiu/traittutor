"""TraitTutor's internal, server-side model gateway."""

from .config import gateway_config_with_overrides
from .service import (
    GatewayAttachment,
    GatewayContentPart,
    GatewayMessage,
    GatewayReceipt,
    GatewayRequest,
    GatewayResponse,
    GatewayStreamEvent,
    GatewayStreamEventType,
    GatewayTool,
    GatewayToolCall,
    TraitTutorGateway,
    get_gateway,
)

__all__ = [
    "GatewayAttachment",
    "GatewayContentPart",
    "GatewayMessage",
    "GatewayReceipt",
    "GatewayRequest",
    "GatewayResponse",
    "GatewayStreamEvent",
    "GatewayStreamEventType",
    "GatewayTool",
    "GatewayToolCall",
    "TraitTutorGateway",
    "get_gateway",
    "gateway_config_with_overrides",
]
