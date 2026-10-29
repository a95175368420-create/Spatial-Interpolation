"""Stateless authenticated HTTP API; clients cannot address server filesystem paths."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hmac
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from typing import Any
import zipfile

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, HTTPException, Security, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field

from .config import load_config
from .data import Dataset
from .io import FIELDS, load_dataset
from .interpolation import interpolate_layer
from .selection import layer_groups, require_metric_crs


@dataclass(frozen=True)
class Limits:
    body_bytes: int = 16 * 1024 * 1024
    expanded_bytes: int = 64 * 1024 * 1024
    rows: int = 20000
    points: int = 1000
    layer_points: int = 500
    grid_cells: int = 20000
    maps: int = 64


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    tables: dict[str, list[dict[str, Any]]]
    config: dict[str, Any] = Field(default_factory=dict)


class LayerRequest(Payload):
    # Explicit selection by group prevents accidental averaging across date/depth/method.
    group_index: int = Field(default=0, ge=0)


def records(frame):
    """Strict JSON, including null rather than NaN/Infinity and ISO dates."""
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _keys_from_environment():
    try:
        keys = json.loads(os.environ.get("SM_API_KEYS_JSON", "{}"))
    except ValueError as exc:
        raise ValueError("SM_API_KEYS_JSON must be a JSON object of client names and keys") from exc
    return keys


def _check_keys(keys):
    if not isinstance(keys, dict) or not keys:
        raise ValueError("API requires SM_API_KEYS_JSON or --keys-file; no anonymous mode is available")
    if any(not isinstance(name, str) or not isinstance(key, str) or len(key) < 32 for name, key in keys.items()):
        raise ValueError("Each API client needs a name and a key of at least 32 characters")
    if len(set(keys.values())) != len(keys):
        raise ValueError("API keys must be unique per client")
    return dict(keys)


class RequestBoundary:
    """Authenticate before parsing and enforce actual byte limits, including chunked bodies."""
    def __init__(self, app, *, keys, limits):
        self.app, self.keys, self.limits = app, keys, limits

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        public = scope["path"] in {"/healthz", "/docs", "/openapi.json", "/docs/oauth2-redirect"}
        if not public:
            key_values = [value for name, value in scope["headers"] if name == b"x-api-key"]
            candidate = key_values[0] if len(key_values) == 1 else b""
            found = any(hmac.compare_digest(candidate, key.encode()) for key in self.keys.values())
            if not found:
                return await JSONResponse({"detail": "Valid X-API-Key required"}, status_code=401)(scope, receive, send)
        total, messages = 0, []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            total += len(message.get("body", b""))
            if total > self.limits.body_bytes:
                return await JSONResponse({"detail": "Request body exceeds upload limit"}, status_code=413)(scope, receive, send)
            messages.append(message)
            if not message.get("more_body", False):
                break
        iterator = iter(messages)
        async def replay():
            try:
                return next(iterator)
            except StopIteration:
                return await receive()
        await self.app(scope, replay, send)


def create_app(*, api_keys=None, limits=None):
    keys = _check_keys(_keys_from_environment() if api_keys is None else api_keys)
    limits = limits or Limits()
    app = FastAPI(title="Spatial Monitor API", version="1.0.0", redoc_url=None,
                  description="Stateless monitoring/geology processing. Send X-API-Key; uploads are deleted after every request.")
    app.add_middleware(RequestBoundary, keys=keys, limits=limits)
    origins = [origin.strip() for origin in os.environ.get("SM_API_ALLOWED_ORIGINS", "").split(",") if origin.strip()]
    if "*" in origins:
        raise ValueError("List explicit SM_API_ALLOWED_ORIGINS rather than a wildcard")
    if origins:
        # Outer middleware handles preflight requests, before API authentication.
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                           allow_headers=["X-API-Key", "Content-Type"], expose_headers=["Content-Disposition"])
    gate = threading.BoundedSemaphore(1)
    security = APIKeyHeader(name="X-API-Key", auto_error=False)

    @contextmanager
    def capacity():
        if not gate.acquire(blocking=False):
            raise HTTPException(429, "An analysis is already running; retry later", headers={"Retry-After": "5"})
        try:
            yield
        except (ValueError, KeyError, zipfile.BadZipFile, OSError, TypeError) as exc:
            # Do not expose server filesystem details from exceptions.
            raise HTTPException(422, "Invalid input or unsupported analysis settings") from exc
        finally:
            gate.release()

    def configured(override):
        config = load_config(override)
        config["interpolation"]["max_cells"] = min(config["interpolation"].get("max_cells", limits.grid_cells), limits.grid_cells)
        return config

    def bounded(data):
        if len(data.points) > limits.points or sum(len(getattr(data, table)) for table in
                ["observations", "points", "geology", "water_levels"]) > limits.rows:
            raise HTTPException(413, "Dataset exceeds row or point limits")
        return data

    @contextmanager
    def from_payload(payload):
        if set(payload.tables) - set(FIELDS):
            raise HTTPException(422, "Unknown table name")
        if not {"samples", "analyses", "points"}.issubset(payload.tables):
            raise HTTPException(422, "samples, analyses and points tables are required")
        if sum(map(len, payload.tables.values())) > limits.rows:
            raise HTTPException(413, "Dataset exceeds row limit")
        if any(isinstance(value, (dict, list)) for rows in payload.tables.values() for row in rows for value in row.values()):
            raise HTTPException(422, "Table cells must contain scalar values")
        config = configured(payload.config)
        with TemporaryDirectory(prefix="spatial-api-") as directory:
            folder = Path(directory)
            # The API accepts contents, never paths supplied by a remote caller.
            # Normalize filenames so config overrides cannot reference server files.
            for name in FIELDS:
                config["tables"][name]["filename"] = f"{name}.csv"
            for name, rows in payload.tables.items():
                frame = pd.DataFrame(rows)
                if frame.empty:
                    frame = pd.DataFrame(columns=FIELDS[name])
                frame.to_csv(folder / f"{name}.csv", index=False)
            yield bounded(load_dataset(folder, config)), config

    def archive_check(raw):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > 2048 or sum(entry.file_size for entry in entries) > limits.expanded_bytes:
                    raise HTTPException(413, "Expanded archive exceeds limits")
                if any(entry.flag_bits & 1 for entry in entries):
                    raise HTTPException(422, "Encrypted archives are not supported")
        except zipfile.BadZipFile as exc:
            raise HTTPException(422, "Invalid workbook or ZIP") from exc

    @contextmanager
    def from_file(file):
        # Multipart parser is behind the request-byte limiter.
        raw = file.file.read(limits.body_bytes + 1)
        if len(raw) > limits.body_bytes:
            raise HTTPException(413, "Upload exceeds limit")
        archive_check(raw)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".xlsx", ".zip"}:
            raise HTTPException(422, "Upload an Excel workbook or a ZIP of canonical CSV tables")
        with TemporaryDirectory(prefix="spatial-api-") as directory:
            folder = Path(directory)
            if suffix == ".xlsx":
                target = folder / "upload.xlsx"
                target.write_bytes(raw)
            else:
                target = folder
                allowed = {f"{name}.csv" for name in FIELDS}
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    names = archive.namelist()
                    if len(names) != len(set(names)) or set(names) - allowed:
                        raise HTTPException(422, "ZIP must contain unique canonical CSV files at its root")
                    for name in names:
                        (folder / name).write_bytes(archive.read(name))
            yield bounded(load_dataset(target)), load_config()

    def summary(data):
        return {"observations": len(data.observations), "valid_observations": int(data.observations.valid.sum()),
                "points": len(data.points), "errors": int(data.issues.severity.eq("error").sum()),
                "issues": records(data.issues), "data": records(data.observations)}

    def bundle(data, config):
        from .export import export_dataset, zip_outputs
        if any(len(group) > limits.layer_points for _, group in layer_groups(data.observations)):
            raise HTTPException(413, "A layer exceeds the analysis point limit")
        with TemporaryDirectory(prefix="spatial-api-report-") as directory:
            output = Path(directory) / "report"
            export_dataset(data, config, output, max_maps=limits.maps)
            body = zip_outputs(output)
        return Response(body, media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="spatial-results.zip"', "Cache-Control": "no-store"})

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/healthz")
    def health():
        return {"status": "ok", "api_version": "1"}

    @app.get("/v1/parameters", dependencies=[Security(security)])
    def parameters():
        return {"parameters": load_config()["parameters"], "limits": limits.__dict__}

    @app.post("/v1/validate", dependencies=[Security(security)])
    def validate(payload: Payload):
        with capacity(), from_payload(payload) as (data, _):
            return summary(data)

    @app.post("/v1/validate/file", dependencies=[Security(security)])
    def validate_file(file: UploadFile = File(...)):
        with capacity(), from_file(file) as (data, _):
            return summary(data)

    @app.post("/v1/layers", dependencies=[Security(security)])
    def layers(payload: Payload):
        with capacity(), from_payload(payload) as (data, _):
            return {"groups": [{"group_index": index, "selection": json.loads(pd.Series(meta).to_json(date_format="iso")),
                                "count": len(rows)} for index, (meta, rows) in enumerate(layer_groups(data.observations))]}

    @app.post("/v1/interpolate", dependencies=[Security(security)])
    def interpolate(payload: LayerRequest):
        with capacity(), from_payload(payload) as (data, config):
            groups = list(layer_groups(data.observations))
            if payload.group_index >= len(groups):
                raise HTTPException(422, "group_index does not identify a valid layer; call /v1/layers first")
            _, rows = groups[payload.group_index]
            if len(rows) > limits.layer_points:
                raise HTTPException(413, "Layer exceeds point limit")
            require_metric_crs(rows)
            grid = interpolate_layer(rows, config["parameters"][rows.parameter.iloc[0]], config["interpolation"])
            if not grid.available:
                return {"available": False, "reason": grid.reason, "measurements": records(rows), "grid": []}
            values = pd.DataFrame({"x": grid.x.ravel(), "y": grid.y.ravel(), "value": grid.z.ravel(), "supported": grid.mask.ravel()})
            return {"available": True, "source_count": grid.source_count, "shape": list(grid.z.shape),
                    "unit": rows.unit.iloc[0], "grid": records(values), "note": "Unsupported cells are null, not zero; display distance is not validated accuracy."}

    @app.post("/v1/export", dependencies=[Security(security)])
    def export(payload: Payload):
        with capacity(), from_payload(payload) as (data, config):
            return bundle(data, config)

    @app.post("/v1/export/file", dependencies=[Security(security)])
    def export_file(file: UploadFile = File(...)):
        with capacity(), from_file(file) as (data, config):
            return bundle(data, config)

    return app
