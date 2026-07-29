"""Coordinate transformation utilities.

Converts between BBox pixel coordinates and GeoGebra math coordinates.

BBox coordinate system:
- Origin at top-left
- X-axis: right is positive
- Y-axis: down is positive

GeoGebra coordinate system:
- Origin at center (or user-specified)
- X-axis: right is positive
- Y-axis: up is positive
"""

from dataclasses import dataclass
import math


@dataclass
class ImageDimensions:
    """Image dimensions."""

    width: int
    height: int


@dataclass
class GGBCoordSystem:
    """GeoGebra coordinate system range."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @property
    def width(self) -> float:
        """Coordinate system width."""
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        """Coordinate system height."""
        return self.y_max - self.y_min

    @property
    def center(self) -> tuple[float, float]:
        """Coordinate system center."""
        return ((self.x_min + self.x_max) / 2, (self.y_min + self.y_max) / 2)


@dataclass
class Point:
    """2D point."""

    x: float
    y: float

    def __repr__(self) -> str:
        return f"({self.x:.2f}, {self.y:.2f})"


# Default configuration
DEFAULT_GGB_COORD = GGBCoordSystem(x_min=-10, x_max=10, y_min=-8, y_max=8)


def bbox_to_ggb(
    bbox_x: float,
    bbox_y: float,
    img_dimensions: ImageDimensions,
    ggb_coord: GGBCoordSystem | None = None,
) -> Point:
    """Convert BBox pixel coordinates to GeoGebra math coordinates.

    Args:
        bbox_x: BBox X coordinate (pixels)
        bbox_y: BBox Y coordinate (pixels)
        img_dimensions: Image dimensions
        ggb_coord: GeoGebra coordinate range, default [-10, 10] x [-8, 8]

    Returns:
        Point in GeoGebra coordinate system
    """
    if ggb_coord is None:
        ggb_coord = DEFAULT_GGB_COORD

    # Normalize to [0, 1]
    norm_x = bbox_x / img_dimensions.width
    norm_y = bbox_y / img_dimensions.height

    # Map to GeoGebra coordinates
    # X: direct linear mapping
    ggb_x = ggb_coord.x_min + norm_x * ggb_coord.width

    # Y: need to flip (BBox Y down, GeoGebra Y up)
    ggb_y = ggb_coord.y_max - norm_y * ggb_coord.height

    return Point(x=ggb_x, y=ggb_y)


def ggb_to_bbox(
    ggb_x: float,
    ggb_y: float,
    img_dimensions: ImageDimensions,
    ggb_coord: GGBCoordSystem | None = None,
) -> Point:
    """Convert GeoGebra math coordinates to BBox pixel coordinates.

    Args:
        ggb_x: GeoGebra X coordinate
        ggb_y: GeoGebra Y coordinate
        img_dimensions: Image dimensions
        ggb_coord: GeoGebra coordinate range, default [-10, 10] x [-8, 8]

    Returns:
        Point in BBox pixel coordinate system
    """
    if ggb_coord is None:
        ggb_coord = DEFAULT_GGB_COORD

    # Normalize to [0, 1]
    norm_x = (ggb_x - ggb_coord.x_min) / ggb_coord.width
    norm_y = (ggb_coord.y_max - ggb_y) / ggb_coord.height  # Y-axis flip

    # Map to pixel coordinates
    bbox_x = norm_x * img_dimensions.width
    bbox_y = norm_y * img_dimensions.height

    return Point(x=bbox_x, y=bbox_y)


def convert_bbox_elements_to_ggb(
    bbox_output: dict,
    ggb_coord: GGBCoordSystem | None = None,
) -> dict:
    """Batch convert all element coordinates in BBox output.

    Args:
        bbox_output: BBox node output
        ggb_coord: GeoGebra coordinate range

    Returns:
        Converted BBox output (with ggb_position fields)
    """
    if ggb_coord is None:
        ggb_coord = DEFAULT_GGB_COORD

    # Get image dimensions
    img_dims_data = bbox_output.get("image_dimensions", {})
    img_dimensions = ImageDimensions(
        width=img_dims_data.get("width", 800),
        height=img_dims_data.get("height", 600),
    )

    # Convert each element
    result = bbox_output.copy()
    converted_elements = []

    for element in bbox_output.get("elements", []):
        converted = element.copy()

        # Convert point coordinates
        if "position" in element and element["position"]:
            pos = element["position"]
            ggb_point = bbox_to_ggb(
                pos.get("x", 0),
                pos.get("y", 0),
                img_dimensions,
                ggb_coord,
            )
            converted["ggb_position"] = {"x": ggb_point.x, "y": ggb_point.y}

        # Convert segment start and end
        if "start" in element and element["start"]:
            start = element["start"]
            ggb_start = bbox_to_ggb(
                start.get("x", 0),
                start.get("y", 0),
                img_dimensions,
                ggb_coord,
            )
            converted["ggb_start"] = {"x": ggb_start.x, "y": ggb_start.y}

        if "end" in element and element["end"]:
            end = element["end"]
            ggb_end = bbox_to_ggb(
                end.get("x", 0),
                end.get("y", 0),
                img_dimensions,
                ggb_coord,
            )
            converted["ggb_end"] = {"x": ggb_end.x, "y": ggb_end.y}

        # Convert polygon vertices
        if "vertices" in element and element["vertices"]:
            ggb_vertices = []
            for vertex in element["vertices"]:
                ggb_v = bbox_to_ggb(
                    vertex.get("x", 0),
                    vertex.get("y", 0),
                    img_dimensions,
                    ggb_coord,
                )
                ggb_vertices.append({"label": vertex.get("label", ""), "x": ggb_v.x, "y": ggb_v.y})
            converted["ggb_vertices"] = ggb_vertices

        # Convert circle center
        if "center" in element and element["center"]:
            center = element["center"]
            ggb_center = bbox_to_ggb(
                center.get("x", 0),
                center.get("y", 0),
                img_dimensions,
                ggb_coord,
            )
            converted["ggb_center"] = {"x": ggb_center.x, "y": ggb_center.y}

            # Convert radius (scale proportionally)
            if "radius" in element:
                pixel_radius = element["radius"]
                scale_x = ggb_coord.width / img_dimensions.width
                converted["ggb_radius"] = pixel_radius * scale_x

        converted_elements.append(converted)

    result["elements"] = converted_elements
    return result


def validate_point_in_bounds(
    point: Point,
    ggb_coord: GGBCoordSystem | None = None,
    tolerance: float = 0.1,
) -> tuple[bool, str]:
    """Validate if point is within GeoGebra coordinate bounds.

    Args:
        point: Point to validate
        ggb_coord: Coordinate range
        tolerance: Boundary tolerance

    Returns:
        (is_valid, error_message)
    """
    if ggb_coord is None:
        ggb_coord = DEFAULT_GGB_COORD

    x_valid = ggb_coord.x_min - tolerance <= point.x <= ggb_coord.x_max + tolerance
    y_valid = ggb_coord.y_min - tolerance <= point.y <= ggb_coord.y_max + tolerance

    if not x_valid:
        return (
            False,
            f"X coordinate {point.x:.2f} out of range [{ggb_coord.x_min}, {ggb_coord.x_max}]",
        )
    if not y_valid:
        return (
            False,
            f"Y coordinate {point.y:.2f} out of range [{ggb_coord.y_min}, {ggb_coord.y_max}]",
        )

    return True, ""


def calculate_distance(p1: Point, p2: Point) -> float:
    """Calculate distance between two points."""
    return math.sqrt((p2.x - p1.x) ** 2 + (p2.y - p1.y) ** 2)


def calculate_midpoint(p1: Point, p2: Point) -> Point:
    """Calculate midpoint of two points."""
    return Point(x=(p1.x + p2.x) / 2, y=(p1.y + p2.y) / 2)


def is_perpendicular(
    p1: Point,
    p2: Point,
    p3: Point,
    p4: Point,
    tolerance: float = 0.01,
) -> bool:
    """Check if two segments are perpendicular.

    Segment 1: p1 -> p2
    Segment 2: p3 -> p4
    """
    # Direction vectors
    v1 = (p2.x - p1.x, p2.y - p1.y)
    v2 = (p4.x - p3.x, p4.y - p3.y)

    # Dot product
    dot_product = v1[0] * v2[0] + v1[1] * v2[1]

    return abs(dot_product) < tolerance


def is_parallel(
    p1: Point,
    p2: Point,
    p3: Point,
    p4: Point,
    tolerance: float = 0.01,
) -> bool:
    """Check if two segments are parallel.

    Segment 1: p1 -> p2
    Segment 2: p3 -> p4
    """
    # Direction vectors
    v1 = (p2.x - p1.x, p2.y - p1.y)
    v2 = (p4.x - p3.x, p4.y - p3.y)

    # Cross product (parallel when 0)
    cross_product = v1[0] * v2[1] - v1[1] * v2[0]

    # Normalize
    len1 = math.sqrt(v1[0] ** 2 + v1[1] ** 2)
    len2 = math.sqrt(v2[0] ** 2 + v2[1] ** 2)

    if len1 < 1e-10 or len2 < 1e-10:
        return False  # Degenerate case

    normalized_cross = abs(cross_product) / (len1 * len2)

    return normalized_cross < tolerance


def suggest_coord_system(
    bbox_output: dict,
    padding_ratio: float = 0.2,
) -> GGBCoordSystem:
    """Suggest appropriate GeoGebra coordinate range based on BBox output.

    Args:
        bbox_output: BBox node output
        padding_ratio: Boundary padding ratio

    Returns:
        Suggested coordinate range
    """
    # Collect all coordinate points
    all_x: list[float] = []
    all_y: list[float] = []

    img_dims_data = bbox_output.get("image_dimensions", {})
    img_dimensions = ImageDimensions(
        width=img_dims_data.get("width", 800),
        height=img_dims_data.get("height", 600),
    )

    for element in bbox_output.get("elements", []):
        if "position" in element and element["position"]:
            all_x.append(element["position"].get("x", 0))
            all_y.append(element["position"].get("y", 0))

        if "start" in element and element["start"]:
            all_x.append(element["start"].get("x", 0))
            all_y.append(element["start"].get("y", 0))

        if "end" in element and element["end"]:
            all_x.append(element["end"].get("x", 0))
            all_y.append(element["end"].get("y", 0))

        if "vertices" in element:
            for v in element["vertices"]:
                all_x.append(v.get("x", 0))
                all_y.append(v.get("y", 0))

        if "center" in element and element["center"]:
            all_x.append(element["center"].get("x", 0))
            all_y.append(element["center"].get("y", 0))

    if not all_x or not all_y:
        return DEFAULT_GGB_COORD

    # Calculate bounds
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)

    # Calculate range
    range_x = max_x - min_x if max_x > min_x else img_dimensions.width
    range_y = max_y - min_y if max_y > min_y else img_dimensions.height

    # Maintain aspect ratio
    aspect_ratio = img_dimensions.width / img_dimensions.height

    # Estimate appropriate coordinate range
    ggb_range_x = range_x / img_dimensions.width * 20
    ggb_range_y = range_y / img_dimensions.height * 16

    # Add padding
    ggb_range_x *= 1 + padding_ratio
    ggb_range_y *= 1 + padding_ratio

    # Use larger range to ensure complete display
    max_range = max(ggb_range_x, ggb_range_y / aspect_ratio * aspect_ratio)

    # Ensure minimum range
    max_range = max(max_range, 10)

    # Center
    half_x = max_range / 2
    half_y = half_x / aspect_ratio

    return GGBCoordSystem(x_min=-half_x, x_max=half_x, y_min=-half_y, y_max=half_y)


def format_ggb_point(point: Point, name: str = "", decimals: int = 2) -> str:
    """Format as GeoGebra point definition command.

    Args:
        point: Point coordinates
        name: Point name (optional)
        decimals: Decimal places

    Returns:
        GeoGebra command string
    """
    x_str = f"{point.x:.{decimals}f}"
    y_str = f"{point.y:.{decimals}f}"

    if name:
        return f"{name} = ({x_str}, {y_str})"
    else:
        return f"({x_str}, {y_str})"


def format_set_coord_system(ggb_coord: GGBCoordSystem, decimals: int = 0) -> str:
    """Format as SetCoordSystem command."""
    return (
        f"SetCoordSystem[{ggb_coord.x_min:.{decimals}f}, "
        f"{ggb_coord.x_max:.{decimals}f}, "
        f"{ggb_coord.y_min:.{decimals}f}, "
        f"{ggb_coord.y_max:.{decimals}f}]"
    )
