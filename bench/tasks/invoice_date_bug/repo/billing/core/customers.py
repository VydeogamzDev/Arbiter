from dataclasses import dataclass


@dataclass
class Customer:
    id: int
    name: str
    utc_offset_hours: float
    currency: str = "USD"
