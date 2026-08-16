"""
Chat Module - conversational AI capabilities.

This module provides:
- AgenticChatPipeline: exploring agent loop + respond stage with autonomous tool use
- ChatCapability: the registered capability executed by CapabilityRegistry
  (imported directly from ``traittutor.agents.chat.capability`` by the
  CapabilityRegistry bootstrap, not re-exported here to avoid import cycles)
"""

from .agentic_pipeline import AgenticChatPipeline

__all__ = ["AgenticChatPipeline"]
