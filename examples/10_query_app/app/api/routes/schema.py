"""Lazy, database-aware schema browser routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_schema_service
from app.services.schema_service import SchemaService, encode_node

router = APIRouter(tags=["schema"])


@router.get("/api/v1/schema/tree")
async def schema_tree(parent_id: str | None = Query(None), database: str | None = Query(None), service: SchemaService = Depends(get_schema_service)) -> dict[str, Any]:
    try:
        return await service.tree(parent_id, database)
    except Exception as exc:
        raise HTTPException(500, f"Schema tree failed: {exc}") from exc


@router.get("/api/v1/schema/search")
async def schema_search(q: str = Query(..., min_length=1), schema: str | None = Query(None), database: str | None = Query(None), service: SchemaService = Depends(get_schema_service)) -> dict[str, Any]:
    results = await service.search(q if "%" in q else f"%{q}%", schema=schema, database=database)
    nodes = []
    for item in results:
        object_type = str(item.get("object_type") or "TABLE").upper()
        object_name = str(item.get("object_name") or "")
        item = dict(item)
        item.update({
            "id": encode_node({
                "kind": "object",
                "database": database,
                "schema": item.get("schema") or schema,
                "object_name": object_name,
                "object_type": object_type,
            }),
            "kind": "object",
            "has_children": object_type in {"TABLE", "VIEW", "EXTERNAL TABLE"},
        })
        nodes.append(item)
    return {"results": nodes}


@router.get("/api/v1/schema/detail")
async def schema_detail(table: str = Query(...), schema: str | None = Query(None), database: str | None = Query(None), service: SchemaService = Depends(get_schema_service)) -> dict[str, Any]:
    if not table.strip():
        raise HTTPException(400, "table is required")
    return await service.table_detail(table.strip(), schema=schema, database=database)


@router.post("/api/v1/schema/refresh")
async def refresh_schema(payload: dict[str, Any] | None = None, service: SchemaService = Depends(get_schema_service)) -> dict[str, str]:
    payload = payload or {}
    service.invalidate(payload.get("database"), payload.get("schema"))
    return {"status": "invalidated"}


@router.get("/api/schemas")
async def list_schemas(database: str | None = Query(None), service: SchemaService = Depends(get_schema_service)) -> dict[str, Any]:
    return {"schemas": await service.get_schemas(database)}


@router.get("/api/tables")
async def list_tables(schema: str | None = Query(default=None), database: str | None = Query(None), service: SchemaService = Depends(get_schema_service)) -> dict[str, Any]:
    rows = await service.get_tables(schema=schema, database=database)
    return {"tables": [r.get("table_name", "") for r in rows]} if schema is None else {"tables": rows}


@router.get("/api/views")
async def list_views(schema: str | None = Query(default=None), database: str | None = Query(None), service: SchemaService = Depends(get_schema_service)) -> dict[str, Any]:
    return {"views": await service.get_views(schema=schema, database=database)}


@router.get("/api/columns")
async def list_columns(table: str = Query(...), schema: str | None = Query(None), database: str | None = Query(None), service: SchemaService = Depends(get_schema_service)) -> dict[str, Any]:
    return {"table": table.upper(), "schema": (schema or "").upper() or None, "columns": await service.get_columns(table.strip(), schema=schema, database=database)}


@router.get("/api/procedures")
async def list_procedures(schema: str | None = Query(default=None), database: str | None = Query(None), service: SchemaService = Depends(get_schema_service)) -> dict[str, Any]:
    return {"procedures": await service.get_procedures(schema=schema, database=database)}


@router.get("/api/search")
async def search_objects(q: str = Query(..., min_length=1), schema: str | None = Query(None), database: str | None = Query(None), service: SchemaService = Depends(get_schema_service)) -> dict[str, Any]:
    return {"results": await service.search(q if "%" in q else f"%{q}%", schema=schema, database=database)}


@router.get("/api/table-detail")
async def table_detail(table: str = Query(...), schema: str | None = Query(None), database: str | None = Query(None), service: SchemaService = Depends(get_schema_service)) -> dict[str, Any]:
    return await service.table_detail(table.strip(), schema=schema, database=database)


@router.get("/api/schema-cache")
async def schema_cache(service: SchemaService = Depends(get_schema_service)) -> dict[str, Any]:
    return await service.schema_cache()


@router.post("/api/schema/refresh")
async def refresh_schema_legacy(service: SchemaService = Depends(get_schema_service)) -> dict[str, str]:
    service.invalidate()
    return {"status": "invalidated"}
