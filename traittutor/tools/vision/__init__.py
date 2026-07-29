"""Vision tools for image processing and GeoGebra support."""

from traittutor.tools.vision.block_parser import (
    BlockType,
    GGBBlock,
    ParsedContent,
    StreamingBlockParser,
    parse_ggb_blocks,
)
from traittutor.tools.vision.coord_transform import (
    DEFAULT_GGB_COORD,
    GGBCoordSystem,
    ImageDimensions,
    Point,
    bbox_to_ggb,
    calculate_distance,
    calculate_midpoint,
    convert_bbox_elements_to_ggb,
    format_ggb_point,
    format_set_coord_system,
    ggb_to_bbox,
    is_parallel,
    is_perpendicular,
    suggest_coord_system,
    validate_point_in_bounds,
)
from traittutor.tools.vision.ggb_validator import (
    ValidationResult,
    get_command_help,
    validate_command,
    validate_ggbscript,
)
from traittutor.tools.vision.image_utils import (
    ImageError,
    fetch_image_from_url,
    image_bytes_to_base64,
    is_base64_image,
    is_valid_image_url,
    resolve_image_input,
    url_to_base64,
)

__all__ = [
    # Image utils
    "ImageError",
    "fetch_image_from_url",
    "image_bytes_to_base64",
    "is_base64_image",
    "is_valid_image_url",
    "resolve_image_input",
    "url_to_base64",
    # Coord transform
    "DEFAULT_GGB_COORD",
    "GGBCoordSystem",
    "ImageDimensions",
    "Point",
    "bbox_to_ggb",
    "calculate_distance",
    "calculate_midpoint",
    "convert_bbox_elements_to_ggb",
    "format_ggb_point",
    "format_set_coord_system",
    "ggb_to_bbox",
    "is_parallel",
    "is_perpendicular",
    "suggest_coord_system",
    "validate_point_in_bounds",
    # GGB validator
    "ValidationResult",
    "get_command_help",
    "validate_command",
    "validate_ggbscript",
    # Block parser
    "BlockType",
    "GGBBlock",
    "ParsedContent",
    "StreamingBlockParser",
    "parse_ggb_blocks",
]
