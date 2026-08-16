"""
Log Adapters
============

Adapters for forwarding logs from external libraries to the unified logging system.
"""

from .llamaindex import LlamaIndexLogContext, LlamaIndexLogForwarder

__all__ = [
    "LlamaIndexLogContext",
    "LlamaIndexLogForwarder",
]
