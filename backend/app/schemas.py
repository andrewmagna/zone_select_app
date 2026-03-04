from __future__ import annotations

from pydantic import BaseModel
from typing import List


class PartSummary(BaseModel):
    partName: str
    displayName: str
    thumbnailUrl: str


class PartSection(BaseModel):
    sectionIndex: int
    imageUrl: str


class PartDetail(BaseModel):
    partName: str
    displayName: str
    sections: List[PartSection]