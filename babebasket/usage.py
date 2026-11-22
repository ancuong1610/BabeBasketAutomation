"""What every OpenRouter call cost, per draft and stage (caption, packaging, image, fidelity, coupon).

Calls are recorded as they happen and written to the `ai_calls` table once the draft they
belong to is known (`flush`). OpenRouter reports the real cost when a request asks for it
(`REQUEST`); nothing is estimated here. No keys or image data are stored.
"""

from __future__ import annotations

import sqlite3

REQUEST = {"usage": {"include": True}}  # merge into the request body: OpenRouter then returns usage.cost
_pending: list[dict] = []


def record(stage: str, reply: dict | None = None, model: str = "", error: str = "", images_in: int = 0) -> None:
    reply = reply or {}
    u = reply.get("usage") or {}
    details = u.get("completion_tokens_details") or {}
    _pending.append({
        "stage": stage, "model": reply.get("model") or model, "generation_id": reply.get("id") or "",
        "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"), "cost_usd": u.get("cost"),
        "images_in": images_in, "ok": 0 if error or not reply else 1, "error": error[:200],
    })


def flush(conn: sqlite3.Connection, draft_id: int | None) -> None:
    from babebasket import db

    rows = [dict(r, at=db.now(), draft_id=draft_id) for r in _pending]
    _pending.clear()
    if rows:
        cols = list(rows[0])
        conn.executemany(f"INSERT INTO ai_calls ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         [tuple(r[c] for c in cols) for r in rows])
        conn.commit()


def report(conn: sqlite3.Connection, days: int = 7) -> str:
    since = f"-{int(days)} days"
    out = [f"OpenRouter calls, last {days} days (cost as reported by OpenRouter)"]
    rows = conn.execute(
        "SELECT stage, COUNT(*) n, SUM(1 - ok) failed, SUM(cost_usd) cost, SUM(prompt_tokens) p, "
        "SUM(completion_tokens) c, SUM(reasoning_tokens) r FROM ai_calls WHERE at >= datetime('now', ?) "
        "GROUP BY stage ORDER BY cost DESC", (since,)).fetchall()
    total = sum(r["cost"] or 0 for r in rows)
    for r in rows:
        out.append(f"  {r['stage']:<10} {r['n']:>4} calls  {r['failed'] or 0:>2} failed  ${r['cost'] or 0:7.3f}  "
                   f"tokens in {r['p'] or 0:>8}  out {r['c'] or 0:>7}  (reasoning {r['r'] or 0})")
    out.append(f"  {'total':<10} ${total:.3f}")
    top = conn.execute(
        "SELECT draft_id, COUNT(*) n, SUM(cost_usd) cost FROM ai_calls WHERE at >= datetime('now', ?) "
        "AND draft_id IS NOT NULL GROUP BY draft_id ORDER BY cost DESC LIMIT 10", (since,)).fetchall()
    if top:
        out.append("Most expensive drafts:")
        out += [f"  #{r['draft_id']:<5} {r['n']:>3} calls  ${r['cost'] or 0:.3f}" for r in top]
    return "\n".join(out)
