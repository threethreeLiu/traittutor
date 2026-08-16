"""Parser for GGBScript code blocks in LLM output."""

from dataclasses import dataclass, field
from enum import Enum
import re

from traittutor.tools.vision.ggb_validator import validate_ggbscript


class BlockType(str, Enum):
    """Types of special blocks in the response."""

    GGBSCRIPT = "ggbscript"
    GEOGEBRA = "geogebra"


@dataclass
class GGBBlock:
    """Represents a parsed GeoGebra script block."""

    page_id: str
    title: str
    content: str
    original_content: str = ""  # Original content before validation/fixing
    validation_warnings: list[str] = field(default_factory=list)
    block_type: BlockType = BlockType.GGBSCRIPT


@dataclass
class ParsedContent:
    """Result of parsing LLM output."""

    text_segments: list[str] = field(default_factory=list)
    ggb_blocks: list[GGBBlock] = field(default_factory=list)


# Regex pattern for matching GGBScript blocks
# Matches: ```ggbscript[page-id;optional-title] or ```geogebra[page-id;optional-title]
BLOCK_START_PATTERN = re.compile(
    r"```\s*(ggbscript|geogebra)\s*\[([^\]\s;]+)(?:;([^\]]*))?\]\s*\n?",
    re.IGNORECASE,
)

BLOCK_END_PATTERN = re.compile(r"```\s*(?:\n|$)")


def parse_ggb_blocks(text: str) -> ParsedContent:
    """Parse text to extract GeoGebra script blocks and regular text.

    Args:
        text: The full text response from the LLM

    Returns:
        ParsedContent containing text segments and GGB blocks
    """
    result = ParsedContent()
    current_pos = 0

    while current_pos < len(text):
        # Find the next block start
        start_match = BLOCK_START_PATTERN.search(text, current_pos)

        if not start_match:
            # No more blocks, add remaining text
            remaining = text[current_pos:].strip()
            if remaining:
                result.text_segments.append(remaining)
            break

        # Add text before the block
        text_before = text[current_pos : start_match.start()].strip()
        if text_before:
            result.text_segments.append(text_before)

        # Extract block metadata
        block_type_str = start_match.group(1).lower()
        page_id = start_match.group(2)
        title = start_match.group(3) or "Untitled"

        # Find the block end
        content_start = start_match.end()
        end_match = BLOCK_END_PATTERN.search(text, content_start)

        if not end_match:
            # No closing ```, treat rest as content
            content = text[content_start:].strip()
            current_pos = len(text)
        else:
            content = text[content_start : end_match.start()].strip()
            current_pos = end_match.end()

        # Validate and fix the content
        fixed_content, warnings, errors = validate_ggbscript(content)

        # Create the block with validated content
        block = GGBBlock(
            page_id=page_id,
            title=title.strip(),
            content=fixed_content,
            original_content=content if content != fixed_content else "",
            validation_warnings=warnings,
            block_type=BlockType(block_type_str),
        )
        result.ggb_blocks.append(block)

    return result


class StreamingBlockParser:
    """Stateful parser for streaming LLM output.

    Handles incremental parsing of text chunks.
    """

    def __init__(self):
        self.buffer = ""
        self.state = "idle"  # idle, await_block, in_block
        self.current_block: dict | None = None
        self.pending_text = ""

    def feed(self, chunk: str) -> list[dict]:
        """Feed a chunk of text and return any complete events.

        Args:
            chunk: New text chunk from the stream

        Returns:
            List of events: {"type": "text", "content": "..."} or
                           {"type": "ggb_block", "page_id": "...", "title": "...", "content": "..."}
        """
        self.buffer += chunk
        events = []

        while True:
            if self.state == "idle":
                # Look for block start
                start_match = BLOCK_START_PATTERN.search(self.buffer)

                if start_match:
                    # Emit text before block
                    text_before = self.buffer[: start_match.start()]
                    if text_before:
                        events.append({"type": "text", "content": text_before})

                    # Start collecting block
                    self.current_block = {
                        "type": start_match.group(1).lower(),
                        "page_id": start_match.group(2),
                        "title": (start_match.group(3) or "Untitled").strip(),
                        "content": "",
                    }
                    self.buffer = self.buffer[start_match.end() :]
                    self.state = "in_block"
                    continue
                else:
                    # Check if we might be at the start of a block pattern
                    if "```" in self.buffer:
                        idx = self.buffer.rfind("```")
                        # Check if this could be a block start
                        potential = self.buffer[idx:]
                        if len(potential) < 50:  # Reasonable max length for block header
                            # Keep it in buffer, emit text before
                            text_before = self.buffer[:idx]
                            if text_before:
                                events.append({"type": "text", "content": text_before})
                            self.buffer = potential
                            break

                    # No block pattern, emit all as text
                    if self.buffer:
                        events.append({"type": "text", "content": self.buffer})
                    self.buffer = ""
                    break

            elif self.state == "in_block":
                # Look for block end
                end_match = BLOCK_END_PATTERN.search(self.buffer)

                if end_match:
                    # Complete the block. The state machine guarantees a block
                    # dict exists while in ``in_block``; bind it so the type
                    # checker can narrow away the ``None`` initial value.
                    block = self.current_block
                    if block is None:  # defensive: never observed in practice
                        self.state = "idle"
                        continue
                    original_content = self.buffer[: end_match.start()].strip()

                    # Validate and fix the content
                    fixed_content, warnings, errors = validate_ggbscript(original_content)

                    block["content"] = fixed_content
                    block["original_content"] = (
                        original_content if original_content != fixed_content else ""
                    )
                    block["validation_warnings"] = warnings

                    events.append(
                        {
                            "type": "ggb_block",
                            "page_id": block["page_id"],
                            "title": block["title"],
                            "content": block["content"],
                            "original_content": block["original_content"],
                            "validation_warnings": block["validation_warnings"],
                        }
                    )
                    self.buffer = self.buffer[end_match.end() :]
                    self.current_block = None
                    self.state = "idle"
                    continue
                else:
                    # Keep collecting block content
                    break

        return events

    def flush(self) -> list[dict]:
        """Flush any remaining content when stream ends.

        Returns:
            List of final events
        """
        events = []

        if self.state == "in_block" and self.current_block:
            # Incomplete block, emit as block anyway
            original_content = self.buffer.strip()

            # Validate and fix the content
            fixed_content, warnings, errors = validate_ggbscript(original_content)

            self.current_block["content"] = fixed_content
            self.current_block["original_content"] = (
                original_content if original_content != fixed_content else ""
            )
            self.current_block["validation_warnings"] = warnings

            events.append(
                {
                    "type": "ggb_block",
                    "page_id": self.current_block["page_id"],
                    "title": self.current_block["title"],
                    "content": self.current_block["content"],
                    "original_content": self.current_block["original_content"],
                    "validation_warnings": self.current_block["validation_warnings"],
                }
            )
        elif self.buffer:
            # Remaining text
            events.append({"type": "text", "content": self.buffer})

        self.buffer = ""
        self.state = "idle"
        self.current_block = None

        return events
