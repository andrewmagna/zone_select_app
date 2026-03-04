from pydantic import BaseModel


class PartSummary(BaseModel):
    id: int
    name: str
    description: str
    price: float


class PartDetail(BaseModel):
    id: int
    name: str
    description: str
    price: float
    specifications: dict
    section: 'PartSection'

class PartSection(BaseModel):
    section_name: str
    items: list