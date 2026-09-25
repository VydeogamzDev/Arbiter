from dataclasses import dataclass


@dataclass
class Product:
    sku: str
    name: str
    price_cents: int
    stock: int


@dataclass
class User:
    id: int
    email: str
    password_hash: str


@dataclass
class Order:
    id: int
    user_id: int
    total_cents: int
    status: str
