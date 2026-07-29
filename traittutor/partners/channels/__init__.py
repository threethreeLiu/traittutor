"""Chat channels module with plugin architecture."""

from traittutor.partners.channels.base import BaseChannel
from traittutor.partners.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
