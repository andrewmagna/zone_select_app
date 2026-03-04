from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import assets
from ..schemas import PartDetail, PartSection, PartSummary

router = APIRouter(prefix="/api", tags=["parts"])

@router.get("/parts", response_model=list[PartSummary])
def list_parts():
    parts = []
    for part_name in assets.list_part_names():
        parts.append(
            PartSummary(
                partName=part_name,
                displayName=assets.prettify_part_name(part_name),
                thumbnailUrl=f"/assets/parts/{part_name}.png",
            )
        )
    return parts

@router.get("/parts/{part_name}", response_model=PartDetail)
def get_part(part_name: str):
    # verify part exists
    if part_name not in set(assets.list_part_names()):
        raise HTTPException(status_code=404, detail="Part not found")

    sections = []
    for idx in assets.list_sections_for_part(part_name):
        sections.append(
            PartSection(
                sectionIndex=idx,
                imageUrl=f"/assets/sections/{part_name}/section_{idx}.png",
            )
        )

    return PartDetail(
        partName=part_name,
        displayName=assets.prettify_part_name(part_name),
        sections=sections,
    )
