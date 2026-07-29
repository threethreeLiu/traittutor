"""Message bus module for decoupled channel-agent communication."""

from traittutor.partners.bus.events import InboundMessage, OutboundMessage
from traittutor.partners.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
