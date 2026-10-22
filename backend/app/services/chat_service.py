"""Persistent assistant conversations that survive dropped connections.

Flow for one question (the browser generates `request_id` before sending):
  1. The question is stored and an empty 'pending' answer row is claimed (Repository.begin_chat_turn).
  2. The answer is generated in a task shielded from the HTTP request: if the connection drops, the task still
     finishes and saves the answer, so nothing is lost.
  3. When the browser reconnects it re-sends the same request_id. The server then returns the saved answer,
     waits for the one still being generated, or (if the previous attempt failed or the server restarted mid-way)
     generates it again. The question is never stored or answered twice.
Context: every answer is generated with the earlier completed messages of the same conversation, loaded from the
database, so a resumed or reopened conversation continues where it stopped.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from app.database.repositories import Repository
from app.llm.assistant import chat
from app.recommendations.engine import ProductNotFound
from app.security.permissions import CurrentUser

log = logging.getLogger("chat")
DEFAULT_TITLE = "New conversation"
_inflight: dict[UUID, asyncio.Task] = {}
META_KEYS = ("recommendation", "intent", "vendor_data_accessed", "llm_used", "needs_product", "products")


async def answer(repo: Repository, session: dict, request_id: UUID, question: str, *, user: CurrentUser,
                 product_id: str | None = None, preferences: dict | None = None) -> dict:
    """Return the assistant message row for this question (status 'complete', 'failed' or still 'pending')."""
    row, claimed = repo.begin_chat_turn(session["id"], request_id, question)
    if not claimed:
        task = _inflight.get(request_id)
        return await asyncio.shield(task) if task else row
    task = asyncio.create_task(_generate(repo, session, row, question, user, product_id, preferences or {}))
    _inflight[request_id] = task
    task.add_done_callback(lambda _: _inflight.pop(request_id, None))
    return await asyncio.shield(task)


async def _generate(repo: Repository, session: dict, row: dict, question: str, user: CurrentUser,
                    product_id: str | None, preferences: dict) -> dict:
    history = repo.chat_context(session["id"], row["request_id"])
    product_id = product_id or session.get("product_id")
    try:
        r = await chat(repo, question, product_id=product_id, history=history, preferences=preferences, user_id=user.id)
        product_id = (r.get("intent") or {}).get("product_id") or product_id
        done = repo.finish_chat_turn(row["id"], r["reply"], "complete", {k: r[k] for k in META_KEYS if k in r},
                                     product_id=product_id)
    except ProductNotFound:
        done = repo.finish_chat_turn(row["id"], "That product is not tracked.", "failed", {"error": "product_not_found"})
    except Exception:  # noqa: BLE001 - the user must get a retryable answer, never a lost question
        log.exception("assistant answer failed")
        done = repo.finish_chat_turn(row["id"], "The assistant could not answer this time. Press Retry to try again.",
                                     "failed", {"error": "internal"})
    if session.get("title") == DEFAULT_TITLE:
        title = " ".join(question.split())[:60]
        repo.rename_chat_session(session["id"], title)
        session["title"] = title
    repo.log_activity(user.id, "assistant_chat", f"Asked the assistant: {question[:200]}", product_id=product_id,
                      payload={"session_id": str(session["id"]), "status": done["status"]})
    return done
