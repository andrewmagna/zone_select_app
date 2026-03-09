from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.admin_auth import set_admin_cookie, clear_admin_cookie, is_admin, admin_dep
from app.audit import init_db, log_apply
from app.config_store import load_config, save_config, validate_parts_root, AppConfig
from app.opc_service import connect, write_zones, is_connected
from app.overlay_import import import_polygons_from_overlay
from app.parts_service import scan_parts, get_part


@asynccontextmanager
async def lifespan(app):
    connect()
    init_db()
    yield


app = FastAPI(title="ZoneSelect", lifespan=lifespan)

cfg = load_config()
parts_root = Path(cfg.parts_root)

if parts_root.exists():
    app.mount("/parts", StaticFiles(directory=parts_root), name="parts")


class ConfigResponse(BaseModel):
    parts_root: str


class ConfigUpdateRequest(BaseModel):
    parts_root: str


class ApplyRequest(BaseModel):
    part_id: str
    zones: dict


class AdminLoginRequest(BaseModel):
    password: str


class EditorSectionResponse(BaseModel):
    part_id: str
    section_index: int
    image_url: str
    image_size: dict
    zones: list


class EditorSaveRequest(BaseModel):
    image: str
    image_size: dict
    zones: list


@app.get("/api/admin/status")
def admin_status(request: Request):
    return {"admin": is_admin(request)}


@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest, response: Response):
    cfg = load_config()
    expected = getattr(cfg, "admin_password", "change_me")

    if req.password != expected:
        raise HTTPException(status_code=401, detail="Invalid password")

    set_admin_cookie(response)
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(response: Response):
    clear_admin_cookie(response)
    return {"ok": True}


@app.get("/api/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    cfg = load_config()
    return ConfigResponse(parts_root=cfg.parts_root)


@app.post("/api/config", response_model=ConfigResponse)
def set_config(req: ConfigUpdateRequest) -> ConfigResponse:
    err = validate_parts_root(req.parts_root)
    if err is not None:
        raise HTTPException(status_code=400, detail=err)

    cfg = AppConfig(parts_root=req.parts_root)
    save_config(cfg)
    return ConfigResponse(parts_root=cfg.parts_root)


@app.get("/api/parts")
def get_parts():
    return scan_parts()


@app.get("/api/parts/{part_id}")
def part_detail(part_id: str):
    return get_part(part_id)


@app.post("/api/apply")
def apply(req: ApplyRequest):
    if not is_connected():
        raise HTTPException(status_code=500, detail="OPC UA not connected")

    write_zones(req.part_id, req.zones)
    log_apply(req.part_id, req.zones)

    return {"status": "ok"}


def load_section_zones(zones_path: Path) -> list:
    if not zones_path.exists():
        return []
    try:
        data = json.loads(zones_path.read_text(encoding="utf-8"))
        return data.get("zones", [])
    except Exception:
        return []


def collect_part_zone_ids(part_dir: Path, exclude_section_index: int | None = None) -> dict:
    """
    Returns:
    {
      "used_ids": [1, 2, 11],
      "by_section": {
        1: [1, 2],
        2: [11]
      }
    }
    """
    zones_dir = part_dir / "zones"
    used_ids = set()
    by_section = {}

    for i in range(1, 5):
        if exclude_section_index is not None and i == exclude_section_index:
            continue

        section_file = zones_dir / f"section{i}.json"
        zones = load_section_zones(section_file)
        ids = []

        for z in zones:
            zone_id = z.get("zone_id")
            if isinstance(zone_id, int):
                ids.append(zone_id)
                used_ids.add(zone_id)

        if ids:
            by_section[i] = sorted(ids)

    return {
        "used_ids": sorted(used_ids),
        "by_section": by_section,
    }


@app.get("/api/editor/parts/{part_id}/sections/{section_index}", dependencies=[Depends(admin_dep)])
def editor_get_section(part_id: str, section_index: int):
    cfg = load_config()
    root = Path(cfg.parts_root)

    part_dir = root / part_id
    image_path = part_dir / "sections" / f"section{section_index}_clean.png"
    zones_path = part_dir / "zones" / f"section{section_index}.json"

    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Section image not found")

    zones_payload = {
        "image": image_path.name,
        "image_size": {"width": 1920, "height": 1080},
        "zones": [],
    }

    if zones_path.exists():
        try:
            zones_payload = json.loads(zones_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    part_usage = collect_part_zone_ids(part_dir, exclude_section_index=section_index)
    current_section_ids = sorted(
        [
            z.get("zone_id")
            for z in zones_payload.get("zones", [])
            if isinstance(z.get("zone_id"), int)
        ]
    )

    return {
        "part_id": part_id,
        "section_index": section_index,
        "image_url": f"/parts/{part_id}/sections/section{section_index}_clean.png",
        "image_size": zones_payload.get("image_size", {"width": 1920, "height": 1080}),
        "zones": zones_payload.get("zones", []),
        "part_used_zone_ids_other_sections": part_usage["used_ids"],
        "section_used_zone_ids": current_section_ids,
        "zone_ids_by_other_section": part_usage["by_section"],
    }


@app.post("/api/editor/parts/{part_id}/sections/{section_index}", dependencies=[Depends(admin_dep)])
def editor_save_section(part_id: str, section_index: int, req: EditorSaveRequest):
    cfg = load_config()
    root = Path(cfg.parts_root)

    part_dir = root / part_id
    zones_dir = part_dir / "zones"
    zones_dir.mkdir(parents=True, exist_ok=True)

    zones_path = zones_dir / f"section{section_index}.json"

    # Validate current section zone IDs and geometry
    current_ids = []
    for z in req.zones:
        zone_id = z.get("zone_id")
        points = z.get("points", [])

        if not isinstance(zone_id, int):
            raise HTTPException(status_code=400, detail="Each zone must have an integer zone_id")

        if zone_id < 1 or zone_id > 40:
            raise HTTPException(status_code=400, detail=f"Zone ID {zone_id} is out of range (1..40)")

        if not isinstance(points, list) or len(points) < 3:
            raise HTTPException(status_code=400, detail=f"Zone {zone_id} must have at least 3 points")

        for pt in points:
            if (
                not isinstance(pt, list)
                or len(pt) != 2
                or not isinstance(pt[0], (int, float))
                or not isinstance(pt[1], (int, float))
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Zone {zone_id} contains an invalid point"
                )

        current_ids.append(zone_id)

    if len(current_ids) != len(set(current_ids)):
        raise HTTPException(status_code=400, detail="Duplicate zone IDs exist within this section")

    # Validate against other sections in the same part
    part_usage = collect_part_zone_ids(part_dir, exclude_section_index=section_index)
    other_used_ids = set(part_usage["used_ids"])
    conflicts = sorted(set(current_ids).intersection(other_used_ids))

    if conflicts:
        raise HTTPException(
            status_code=400,
            detail=f"Zone IDs already used in other sections of this part: {conflicts}"
        )

    payload = {
        "image": req.image,
        "image_size": req.image_size,
        "zones": req.zones,
    }

    zones_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return {"ok": True}


@app.post("/api/editor/parts/{part_id}/sections/{section_index}/import", dependencies=[Depends(admin_dep)])
def editor_import_overlay(part_id: str, section_index: int):
    cfg = load_config()
    root = Path(cfg.parts_root)

    overlay_path = root / part_id / "sections" / f"section{section_index}_overlay.png"

    try:
        result = import_polygons_from_overlay(overlay_path)
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {e}")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/opc/status")
def opc_status():
    return {"connected": is_connected()}