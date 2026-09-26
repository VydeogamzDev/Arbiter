from dataclasses import dataclass, field


@dataclass
class Order:
    id: int
    lines: list[tuple[str, int]] = field(default_factory=list)
    status: str = "open"
