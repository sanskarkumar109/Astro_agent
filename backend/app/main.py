from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .agent import graph_shape, stream_agent
from .models import ChatRequest
from .tools import compute_birth_chart, geocode_place, get_daily_transits, knowledge_lookup

app = FastAPI(title="AstroAgent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/graph")
def graph() -> dict[str, object]:
    return graph_shape()


@app.post("/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(stream_agent(req), media_type="application/x-ndjson")


@app.post("/tools/geocode_place")
def api_geocode(payload: dict[str, str]) -> dict[str, object]:
    return geocode_place(payload["place"])


@app.post("/tools/compute_birth_chart")
def api_birth_chart(payload: dict[str, str]) -> dict[str, object]:
    return compute_birth_chart(payload["date"], payload["time"], payload["place"])


@app.post("/tools/get_daily_transits")
def api_transits(payload: dict[str, object]) -> dict[str, object]:
    chart = payload["natal_chart"]
    return get_daily_transits(payload.get("date"), chart)  # type: ignore[arg-type]


@app.post("/tools/knowledge_lookup")
def api_knowledge(payload: dict[str, str]) -> dict[str, object]:
    return knowledge_lookup(payload["query"])

