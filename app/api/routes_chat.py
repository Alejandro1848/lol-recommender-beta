"""Ruta del chatbot."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.container import ServiceContainer
from app.data.schemas import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


def _container(request: Request) -> ServiceContainer:
    return request.app.state.container


@router.post("/chat", response_model=ChatResponse)
def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    c = _container(request)
    result = c.chat.answer(payload.question)
    return ChatResponse(**result)


@router.get("/chat/proactive", response_model=ChatResponse)
def proactive_chat(request: Request) -> ChatResponse:
    c = _container(request)
    return ChatResponse(**c.chat.proactive_tip())
