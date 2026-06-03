from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from .models import AgentEvent, ChatRequest, ToolActivity
from .tools import compute_birth_chart, get_daily_transits, knowledge_lookup

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

SENSITIVE_TOPICS = ("medical", "medicine", "diagnosis", "legal", "lawsuit", "financial", "stock")


class AstroState(TypedDict, total=False):
    req: ChatRequest
    intent: str
    route: str
    chart: dict[str, Any]
    transits: dict[str, Any]
    notes: dict[str, Any]
    error_message: str
    final_text: str
    events: list[AgentEvent]


def _intent(message: str) -> str:
    text = message.lower()
    if any(topic in text for topic in SENSITIVE_TOPICS):
        return "general"
    if any(word in text for word in ("today", "daily", "transit", "energy")):
        return "daily"
    if any(word in text for word in ("career", "relationship", "purpose", "love", "family")):
        return "reading"
    if any(word in text for word in ("chart", "birth", "natal", "ascendant", "houses", "planetary positions")):
        return "chart"
    return "general"


def _safety_prefix(message: str) -> str:
    if any(topic in message.lower() for topic in SENSITIVE_TOPICS):
        return (
            "I can offer reflective astrology, but not medical, legal, or financial certainty. "
            "Please treat this as contemplative guidance and involve a qualified professional for decisions.\n\n"
        )
    return ""


async def stream_agent(req: ChatRequest) -> AsyncIterator[str]:
    """Stream newline-delimited JSON events for the UI."""
    try:
        async for event in _run(req):
            yield event.model_dump_json() + "\n"
    except Exception as exc:  # explicit error event keeps the frontend graceful
        message = _friendly_tool_error(str(exc))
        for token in _chunk(message):
            yield AgentEvent(type="token", content=token).model_dump_json() + "\n"
        yield AgentEvent(type="error", content=str(exc)).model_dump_json() + "\n"
        yield AgentEvent(type="done", metadata={"error": True}).model_dump_json() + "\n"


async def _run(req: ChatRequest) -> AsyncIterator[AgentEvent]:
    async for update in ASTRO_GRAPH.astream({"req": req}, stream_mode="updates"):
        for node_update in update.values():
            for event in node_update.get("events", []):
                yield event


def _router_node(state: AstroState) -> AstroState:
    req = state["req"]
    intent = _intent(req.message)
    text = req.message.lower()
    if any(topic in text for topic in SENSITIVE_TOPICS):
        route = "guardrail"
    elif req.birth_details is None and intent in {"daily", "chart", "reading"}:
        route = "missing_details"
    elif req.birth_details is not None and intent in {"daily", "chart", "reading"}:
        route = "chart_tool"
    else:
        route = "knowledge_tool"
    return {"intent": intent, "route": route, "events": []}


def _route_from_router(state: AstroState) -> Literal[
    "guardrail", "missing_details", "chart_tool", "knowledge_tool"
]:
    return state["route"]  # type: ignore[return-value]


def _route_after_chart(state: AstroState) -> Literal["transit_tool", "knowledge_tool", "error_response"]:
    if state.get("error_message"):
        return "error_response"
    if state["intent"] == "daily":
        return "transit_tool"
    return "knowledge_tool"


def _route_after_tool(state: AstroState) -> Literal["response", "error_response"]:
    return "error_response" if state.get("error_message") else "response"


def _guardrail_node(state: AstroState) -> AstroState:
    req = state["req"]
    text = _safety_prefix(req.message) + (
        "Astrology can support reflection, journaling, and values-based choices, but I should not "
        "answer this as a prediction or certainty claim. Bring this question to a qualified "
        "professional, and I can help reframe it into a reflective astrology prompt if useful."
    )
    events = [AgentEvent(type="token", content=token) for token in _chunk(text)]
    events.append(AgentEvent(type="done", metadata={"intent": state["intent"], "guardrail": True}))
    return {"final_text": text, "events": events}


def _missing_details_node(state: AstroState) -> AstroState:
    text = (
        "To read your chart with care, I need birth date, exact birth time, and birth place. "
        "Share those and I will ground the reading in the chart rather than guessing."
    )
    return {
        "final_text": text,
        "events": [
            AgentEvent(type="token", content=text),
            AgentEvent(type="done", metadata={"intent": state["intent"]}),
        ],
    }


def _chart_tool_node(state: AstroState) -> AstroState:
    req = state["req"]
    birth = req.birth_details
    if birth is None:
        return {"error_message": "Birth details are required.", "events": []}
    tool_input = {"date": birth.date, "time": birth.time, "place": birth.place}
    events = [
        AgentEvent(
            type="tool",
            tool=ToolActivity(name="compute_birth_chart", status="started", input=tool_input),
        )
    ]
    try:
        chart = compute_birth_chart(birth.date, birth.time, birth.place)
    except Exception as exc:
        events.append(
            AgentEvent(
                type="tool",
                tool=ToolActivity(name="compute_birth_chart", status="failed", input=tool_input),
            )
        )
        return {"error_message": str(exc), "events": events}
    events.append(
        AgentEvent(
            type="tool",
            tool=ToolActivity(
                name="compute_birth_chart",
                status="completed",
                input=tool_input,
                output={
                    "sun": chart["planets"]["Sun"],
                    "moon": chart["planets"]["Moon"],
                    "ascendant": chart["angles"]["ascendant"],
                },
            ),
        )
    )
    return {"chart": chart, "events": events}


def _transit_tool_node(state: AstroState) -> AstroState:
    tool_input = {"date": None}
    events = [
        AgentEvent(
            type="tool",
            tool=ToolActivity(name="get_daily_transits", status="started", input=tool_input),
        )
    ]
    try:
        transits = get_daily_transits(None, state["chart"])
    except Exception as exc:
        events.append(
            AgentEvent(
                type="tool",
                tool=ToolActivity(name="get_daily_transits", status="failed", input=tool_input),
            )
        )
        return {"error_message": str(exc), "events": events}
    events.append(
        AgentEvent(
            type="tool",
            tool=ToolActivity(
                name="get_daily_transits",
                status="completed",
                input=tool_input,
                output={"date": transits["date"], "aspects": transits["aspects_to_natal"][:3]},
            ),
        )
    )
    return {"transits": transits, "events": events}


def _knowledge_tool_node(state: AstroState) -> AstroState:
    req = state["req"]
    if state.get("chart") and state["intent"] == "chart":
        query = "sun moon ascendant houses"
    else:
        query = req.message
    tool_input = {"query": query}
    # General questions use knowledge internally; chart readings expose it as a tool call.
    expose_tool = bool(state.get("chart"))
    events = []
    if expose_tool:
        events.append(
            AgentEvent(
                type="tool",
                tool=ToolActivity(name="knowledge_lookup", status="started", input=tool_input),
            )
        )
    try:
        notes = knowledge_lookup(query)
    except Exception as exc:
        if expose_tool:
            events.append(
                AgentEvent(
                    type="tool",
                    tool=ToolActivity(name="knowledge_lookup", status="failed", input=tool_input),
                )
            )
        return {"error_message": str(exc), "events": events}
    if expose_tool:
        events.append(
            AgentEvent(
                type="tool",
                tool=ToolActivity(
                    name="knowledge_lookup",
                    status="completed",
                    input=tool_input,
                    output={"matches": [m["title"] for m in notes["matches"]]},
                ),
            )
        )
    return {"notes": notes, "events": events}


async def _response_node(state: AstroState) -> AstroState:
    req = state["req"]
    chart = state.get("chart")
    transits = state.get("transits")
    notes = state.get("notes")
    if state["intent"] == "daily" and chart and transits:
        text = await _compose_response(
            req,
            fallback=lambda: _daily_response(req.message, chart, transits),
            chart=chart,
            transits=transits,
            notes=None,
        )
    elif state["intent"] in {"chart", "reading"} and chart and notes:
        text = await _compose_response(
            req,
            fallback=lambda: _chart_response(req.message, chart, notes),
            chart=chart,
            transits=None,
            notes=notes,
        )
    else:
        text = await _compose_response(
            req,
            fallback=lambda: _general_response(req.message, notes or {"matches": []}),
            chart=None,
            transits=None,
            notes=notes,
        )

    events = [AgentEvent(type="token", content=token) for token in _chunk(_safety_prefix(req.message) + text)]
    events.append(AgentEvent(type="done", metadata={"intent": state["intent"]}))
    return {"final_text": text, "events": events}


def _error_response_node(state: AstroState) -> AstroState:
    error = state.get("error_message", "Unknown tool error.")
    message = _friendly_tool_error(error)
    events = [AgentEvent(type="token", content=token) for token in _chunk(message)]
    events.append(AgentEvent(type="error", content=error))
    events.append(AgentEvent(type="done", metadata={"intent": state.get("intent"), "error": True}))
    return {"final_text": message, "events": events}


def _build_graph():
    graph = StateGraph(AstroState)
    graph.add_node("router", _router_node)
    graph.add_node("guardrail", _guardrail_node)
    graph.add_node("missing_details", _missing_details_node)
    graph.add_node("chart_tool", _chart_tool_node)
    graph.add_node("transit_tool", _transit_tool_node)
    graph.add_node("knowledge_tool", _knowledge_tool_node)
    graph.add_node("response", _response_node)
    graph.add_node("error_response", _error_response_node)

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        _route_from_router,
        {
            "guardrail": "guardrail",
            "missing_details": "missing_details",
            "chart_tool": "chart_tool",
            "knowledge_tool": "knowledge_tool",
        },
    )
    graph.add_conditional_edges(
        "chart_tool",
        _route_after_chart,
        {
            "transit_tool": "transit_tool",
            "knowledge_tool": "knowledge_tool",
            "error_response": "error_response",
        },
    )
    graph.add_conditional_edges(
        "transit_tool",
        _route_after_tool,
        {"response": "response", "error_response": "error_response"},
    )
    graph.add_conditional_edges(
        "knowledge_tool",
        _route_after_tool,
        {"response": "response", "error_response": "error_response"},
    )
    graph.add_edge("guardrail", END)
    graph.add_edge("missing_details", END)
    graph.add_edge("response", END)
    graph.add_edge("error_response", END)
    return graph.compile()


ASTRO_GRAPH = _build_graph()



def _chunk(text: str, size: int = 34) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    for i in range(0, len(words), size):
        chunks.append(" ".join(words[i : i + size]) + (" " if i + size < len(words) else ""))
    return chunks


def _friendly_tool_error(error: str) -> str:
    if "Could not geocode" in error:
        return (
            "I could not resolve that birth place, so I do not want to pretend the chart is accurate. "
            "Try a city I know, like Delhi, Mumbai, Kolkata, Chennai, Bengaluru, London, New York, "
            "or enter coordinates as latitude, longitude."
        )
    if "Date must" in error or "Birth dates" in error:
        return (
            "Something in the birth date or time looks invalid. Use a date between 1800 and 2100 "
            "and a local birth time in HH:MM format, then I can calculate the chart properly."
        )
    if "pyswisseph" in error:
        return (
            "The ephemeris package is missing, so I cannot calculate real planetary positions yet. "
            "Install the backend dependencies and I will use Swiss Ephemeris instead of guessing."
        )
    return (
        "I hit a tool problem while calculating the chart. I do not want to invent the result, "
        f"so here is the issue to fix first: {error}"
    )


async def _compose_response(
    req: ChatRequest,
    fallback,
    chart: dict[str, Any] | None,
    transits: dict[str, Any] | None,
    notes: dict[str, Any] | None,
) -> str:
    if not _has_groq_key():
        return fallback()
    try:
        return await asyncio.to_thread(_groq_response, req, chart, transits, notes)
    except Exception:
        return fallback()


def _has_groq_key() -> bool:
    if os.getenv("ASTRO_AGENT_DISABLE_LLM") == "1":
        return False
    key = os.getenv("GROQ_API_KEY", "").strip()
    return bool(key and key != "put_your_groq_api_key_here")


def _groq_response(
    req: ChatRequest,
    chart: dict[str, Any] | None,
    transits: dict[str, Any] | None,
    notes: dict[str, Any] | None,
) -> str:
    from groq import Groq

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    compact_chart = _compact_chart(chart) if chart else None
    note_text = _compact_notes(notes) if notes else ""
    recent_history = [
        {"role": msg.role, "content": msg.content}
        for msg in req.history[-6:]
        if msg.role in {"user", "assistant"}
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "You are AstroAgent, a warm Indian spiritual astrology companion for Aradhana. "
                "Answer conversationally, like a thoughtful guide, not a generic horoscope. "
                "Use only the provided chart, transit, and reference data for astrological claims. "
                "Never invent placements. Mention uncertainty where birth data or interpretation is limited. "
                "Do not provide medical, legal, or financial certainty. Keep answers grounded, caring, "
                "specific, and around 3-5 short paragraphs."
            ),
        },
        *recent_history,
        {
            "role": "user",
            "content": (
                f"User question: {req.message}\n\n"
                f"Birth details: {req.birth_details.model_dump() if req.birth_details else None}\n\n"
                f"Computed natal chart: {compact_chart}\n\n"
                f"Daily transits: {transits}\n\n"
                f"Reference notes: {note_text}"
            ),
        },
    ]
    response = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        messages=messages,
        temperature=0.7,
        max_tokens=650,
    )
    return response.choices[0].message.content or ""


def _compact_chart(chart: dict[str, Any]) -> dict[str, Any]:
    return {
        "planets": chart.get("planets", {}),
        "houses": chart.get("houses", {}),
        "angles": chart.get("angles", {}),
        "location": chart.get("location", {}),
        "house_system": chart.get("house_system"),
        "ayanamsa": chart.get("ayanamsa"),
    }


def _compact_notes(notes: dict[str, Any] | None) -> str:
    if not notes:
        return ""
    return "\n\n".join(match["text"] for match in notes.get("matches", [])[:3])


def _chart_response(message: str, chart: dict[str, Any], notes: dict[str, Any]) -> str:
    sun = chart["planets"]["Sun"]
    moon = chart["planets"]["Moon"]
    mercury = chart["planets"]["Mercury"]
    venus = chart["planets"]["Venus"]
    mars = chart["planets"]["Mars"]
    saturn = chart["planets"]["Saturn"]
    jupiter = chart["planets"]["Jupiter"]
    asc = chart["angles"]["ascendant"]
    mc = chart["angles"]["midheaven"]
    tenth = chart["houses"]["10"]
    seventh = chart["houses"]["7"]
    context = ", ".join(match["title"] for match in notes["matches"]) or "core chart symbols"
    theme = _reading_theme(message)
    focus = _theme_guidance(theme, chart)

    return (
        f"I pulled your chart from the ephemeris, so I will stay close to the actual placements. "
        f"Your Sun is in {sun['sign']} at {sun['degree']} degrees, your Moon is in "
        f"{moon['sign']} at {moon['degree']} degrees, and your Ascendant is {asc['sign']} "
        f"at {asc['degree']} degrees. That gives the reading three layers: the Sun shows the "
        f"life-force you are learning to trust, the Moon shows what your nervous system keeps "
        f"asking for, and the {asc['sign']} rising style shows how you begin things before you "
        f"have even explained yourself.\n\n"
        f"For your question, the most relevant thread is {focus} I am also noting Mercury in "
        f"{mercury['sign']} for how you process choices, Venus in {venus['sign']} for what feels "
        f"harmonious, Mars in {mars['sign']} for how you act under pressure, Jupiter in "
        f"{jupiter['sign']} for growth, and Saturn in {saturn['sign']} for the lesson that asks "
        f"for maturity.\n\n"
        f"If I had to turn this into one grounded step: work with the {moon['sign']} Moon first. "
        f"When your inner life feels steadier, the {sun['sign']} Sun can make cleaner decisions. "
        f"The chart does not lock you into an outcome; it gives you a map for where attention, "
        f"practice, and devotion may help."
    )


def _daily_response(message: str, chart: dict[str, Any], transits: dict[str, Any]) -> str:
    sun = chart["planets"]["Sun"]["sign"]
    moon = chart["planets"]["Moon"]["sign"]
    asc = chart["angles"]["ascendant"]["sign"]
    aspect_lines = [
        f"{a['planet']} {a['aspect']} its natal position with a {a['orb']} degree orb"
        for a in transits["aspects_to_natal"][:3]
    ]
    aspect_text = "; ".join(aspect_lines) if aspect_lines else "no tight same-planet natal aspects"
    return (
        f"For today, {transits['date']}, the transit check shows {aspect_text}. With your natal "
        f"Sun in {sun}, Moon in {moon}, and {asc} rising, I would treat the day as a rhythm check: "
        f"what wants movement, what wants quiet, and what needs a cleaner boundary?\n\n"
        f"The guidance is to do one thing that gives the {sun} Sun direction, one thing that gives "
        f"the {moon} Moon reassurance, and one small outer-world action that fits your {asc} "
        f"Ascendant. Keep it practical: send the message, finish the task, take the walk, or make "
        f"the choice you have been circling. This is weather, not fate."
    )


def _general_response(message: str, notes: dict[str, Any]) -> str:
    if not notes["matches"]:
        return (
            "I can help with astrology, birth charts, transits, and reflective questions. "
            "Ask me about your chart, today's transits, career themes, relationships, or purpose."
        )
    title = notes["matches"][0]["title"]
    return (
        f"I found a useful reference around {title}. Astrology works best here as a mirror: "
        "we can look at the symbol, name the pattern it suggests, and turn that into one grounded "
        "choice you can test in real life."
    )


def _reading_theme(message: str) -> str:
    text = message.lower()
    if any(word in text for word in ("career", "work", "job", "calling")):
        return "career"
    if any(word in text for word in ("relationship", "love", "partner", "marriage")):
        return "relationship"
    if any(word in text for word in ("purpose", "meaning", "path")):
        return "purpose"
    if any(word in text for word in ("house", "houses", "ascendant", "rising")):
        return "chart"
    return "core"


def _theme_guidance(theme: str, chart: dict[str, Any]) -> str:
    sun = chart["planets"]["Sun"]
    moon = chart["planets"]["Moon"]
    venus = chart["planets"]["Venus"]
    mars = chart["planets"]["Mars"]
    saturn = chart["planets"]["Saturn"]
    jupiter = chart["planets"]["Jupiter"]
    asc = chart["angles"]["ascendant"]
    mc = chart["angles"]["midheaven"]
    tenth = chart["houses"]["10"]
    seventh = chart["houses"]["7"]

    if theme == "career":
        return (
            f"career: the Midheaven is in {mc['sign']} and the tenth house opens in "
            f"{tenth['sign']}, so vocation wants both visible skill and patience. Jupiter in "
            f"{jupiter['sign']} shows where growth comes naturally, while Saturn in "
            f"{saturn['sign']} shows the craft that has to be built slowly."
        )
    if theme == "relationship":
        return (
            f"relationships: the seventh house opens in {seventh['sign']}, Venus in "
            f"{venus['sign']} describes what feels affectionate and mutual, and Mars in "
            f"{mars['sign']} shows how desire and conflict move. The Moon in {moon['sign']} "
            f"is especially important because it describes what safety feels like."
        )
    if theme == "purpose":
        return (
            f"purpose: the Ascendant in {asc['sign']} shows the path that begins through lived "
            f"practice, while the Sun in {sun['sign']} points to the kind of vitality that grows "
            f"when you stop performing someone else's definition of success."
        )
    if theme == "chart":
        return (
            f"chart structure: the Ascendant is {asc['sign']}, the Midheaven is {mc['sign']}, "
            f"the seventh house begins in {seventh['sign']}, and the tenth house begins in "
            f"{tenth['sign']}. That gives a quick view of self, vocation, partnership, and public life."
        )
    return (
        f"the Sun-Moon-Ascendant pattern: {sun['sign']} Sun, {moon['sign']} Moon, and "
        f"{asc['sign']} rising. Read together, these describe vitality, emotional needs, and "
        f"the way you meet new experiences."
    )


def graph_shape() -> dict[str, Any]:
    return {
        "nodes": [
            "router",
            "chart_tool",
            "transit_tool",
            "knowledge_tool",
            "guardrail",
            "missing_details",
            "response",
            "error_response",
        ],
        "edges": [
            "START -> router",
            "router -> guardrail | missing_details | chart_tool | knowledge_tool",
            "chart_tool -> transit_tool | knowledge_tool | error_response",
            "transit_tool -> response | error_response",
            "knowledge_tool -> response | error_response",
            "guardrail | missing_details | response | error_response -> END",
        ],
        "framework": "LangGraph StateGraph",
    }
