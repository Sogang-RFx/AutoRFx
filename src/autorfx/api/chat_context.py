"""What the two chat routes share: the request body and the context a question is answered from.

``POST /rfqs/{id}/chat`` and ``POST /rfqs/{id}/chat/stream`` take the same body and build the same context,
so the stream is the one path that makes an answer, watched (docs/design/immersive-ui.md §5).
"""

from __future__ import annotations

from pydantic import Field

from autorfx import access
from autorfx.api.scoping import RfqView
from autorfx.api.state import AppState
from autorfx.chat.models import ChatContext, Turn
from autorfx.models import Model


class ChatTurn(Model):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(max_length=4000)


class ChatBody(Model):
    question: str
    history: tuple[ChatTurn, ...] = Field(default=(), max_length=6)

    def turns(self) -> tuple[Turn, ...]:
        return tuple(Turn(question=t.question, answer=t.answer) for t in self.history)


def chat_context(rfq: RfqView, state: AppState) -> ChatContext:
    """Everything a question may be answered from: the clauses this account may see, and nothing else."""
    data, scope, visible = rfq.data, rfq.scope, rfq.visible
    visible_ids = frozenset(c.clause_id for c in visible)
    return ChatContext(
        index=data.index.scoped(visible_ids),
        clauses=visible,
        analyses=tuple(a for a in data.analyses if a.clause_id in visible_ids),
        issues=access.visible_issues(data.consistency.issues, scope=scope, visible=visible),
        files=access.package_view(data.manifest, visible, scope=scope).files,
        topics=state.chat_topics,
    )
