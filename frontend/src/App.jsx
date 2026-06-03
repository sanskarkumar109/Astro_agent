import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Loader2, Send, Sparkles, Trash2 } from "lucide-react";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

const starterMessages = [
  {
    role: "assistant",
    content:
      "Share your birth details, then ask about your chart, today's energy, career themes, relationships, or purpose.",
  },
];

function loadState(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}

function App() {
  const [birth, setBirth] = useState(() =>
    loadState("astro.birth", { name: "", date: "", time: "", place: "" }),
  );
  const [messages, setMessages] = useState(() => loadState("astro.messages", starterMessages));
  const [input, setInput] = useState("");
  const [toolLog, setToolLog] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => localStorage.setItem("astro.birth", JSON.stringify(birth)), [birth]);
  useEffect(() => localStorage.setItem("astro.messages", JSON.stringify(messages)), [messages]);

  const isBirthValid = useMemo(() => birth.date && birth.time && birth.place.trim().length > 1, [birth]);

  async function sendMessage(event) {
    event.preventDefault();
    const text = input.trim();
    if (!text || isLoading) return;

    setInput("");
    setError("");
    setToolLog([]);
    setIsLoading(true);

    const nextMessages = [...messages, { role: "user", content: text }, { role: "assistant", content: "" }];
    setMessages(nextMessages);

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          birth_details: isBirthValid
            ? { name: birth.name || null, date: birth.date, time: birth.time, place: birth.place }
            : null,
          history: messages.slice(-8),
        }),
      });

      if (!response.ok || !response.body) throw new Error(`API returned ${response.status}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line);
          if (event.type === "token") {
            setMessages((current) => {
              const copy = [...current];
              copy[copy.length - 1] = {
                ...copy[copy.length - 1],
                content: copy[copy.length - 1].content + event.content,
              };
              return copy;
            });
          }
          if (event.type === "tool") {
            setToolLog((current) => [event.tool, ...current].slice(0, 8));
          }
          if (event.type === "error") {
            setError(event.content);
          }
        }
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  }

  function clearConversation() {
    setMessages(starterMessages);
    setToolLog([]);
    setError("");
  }

  return (
    <main className="shell">
      <section className="workspace">
        <aside className="panel birth-panel" aria-label="Birth details">
          <div className="brand">
            <Sparkles size={24} aria-hidden="true" />
            <div>
              <h1>AstroAgent</h1>
              <p>Reflective chart guidance grounded in ephemeris tools.</p>
            </div>
          </div>

          <label>
            Name
            <input value={birth.name} onChange={(e) => setBirth({ ...birth, name: e.target.value })} />
          </label>
          <label>
            Birth date
            <input
              type="date"
              value={birth.date}
              onChange={(e) => setBirth({ ...birth, date: e.target.value })}
              required
            />
          </label>
          <label>
            Birth time
            <input
              type="time"
              value={birth.time}
              onChange={(e) => setBirth({ ...birth, time: e.target.value })}
              required
            />
          </label>
          <label>
            Birth place
            <input
              placeholder="Delhi or 28.6139, 77.2090"
              value={birth.place}
              onChange={(e) => setBirth({ ...birth, place: e.target.value })}
              required
            />
          </label>

          <div className={isBirthValid ? "status ready" : "status"}>
            {isBirthValid ? "Birth details ready" : "Add date, time, and place for chart-based answers"}
          </div>

          <div className="tool-feed" aria-live="polite">
            <h2>Tool Activity</h2>
            {toolLog.length === 0 ? <p>No tools called yet.</p> : null}
            {toolLog.map((tool, index) => (
              <div className="tool-row" key={`${tool.name}-${tool.status}-${index}`}>
                <span className={tool.status}>{tool.status}</span>
                <strong>{tool.name}</strong>
                {tool.output ? <small>{JSON.stringify(tool.output)}</small> : null}
              </div>
            ))}
          </div>
        </aside>

        <section className="chat-panel">
          <header>
            <div>
              <h2>Conversation</h2>
              <p>Warm, grounded readings with clear guardrails.</p>
            </div>
            <button className="icon-button" onClick={clearConversation} aria-label="Clear conversation" title="Clear conversation">
              <Trash2 size={18} />
            </button>
          </header>

          <div className="messages" aria-live="polite">
            {messages.map((message, index) => (
              <article className={`message ${message.role}`} key={index}>
                <span>{message.role === "user" ? "You" : "AstroAgent"}</span>
                <p>{message.content || (isLoading && index === messages.length - 1 ? "Listening to the chart..." : "")}</p>
              </article>
            ))}
          </div>

          {error ? <div className="error">{error}</div> : null}

          <form className="composer" onSubmit={sendMessage}>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about your chart, today, career, or relationships"
              disabled={isLoading}
            />
            <button type="submit" disabled={isLoading || !input.trim()} aria-label="Send message" title="Send message">
              {isLoading ? <Loader2 className="spin" size={18} /> : <Send size={18} />}
            </button>
          </form>
        </section>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);

