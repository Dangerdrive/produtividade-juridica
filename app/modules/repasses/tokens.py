"""Links individuais de acompanhamento por cliente.

O token é um HMAC-SHA256 do nome normalizado do cliente com um segredo
configurado (``REPASSES_CLIENTE_TOKEN_SECRET``). Assim cada cliente recebe um
link próprio, sem precisar de conta/senha.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import quote

from app.modules.repasses.utils import normalizar_chave


class TokenClienteError(Exception):
    """Segredo não configurado."""


def _assinar(cliente: str, segredo: str) -> bytes:
    mensagem = normalizar_chave(cliente).encode("utf-8")
    return hmac.new(segredo.encode("utf-8"), mensagem, hashlib.sha256).digest()


def gerar_token_cliente(cliente: str, segredo: str) -> str:
    """Gera um token estável e curto para o cliente."""
    if not segredo:
        raise TokenClienteError(
            "REPASSES_CLIENTE_TOKEN_SECRET não configurado."
        )
    return base64.urlsafe_b64encode(_assinar(cliente, segredo)[:18]).decode(
        "ascii"
    ).rstrip("=")


def validar_token_cliente(cliente: str, token: str | None, segredo: str) -> bool:
    """Compara o token recebido em tempo constante."""
    if not segredo or not token:
        return False

    try:
        esperado = gerar_token_cliente(cliente, segredo)
    except TokenClienteError:
        return False

    return hmac.compare_digest(str(token), esperado)


def link_cliente(cliente: str, segredo: str) -> str:
    """Caminho relativo da página pública do cliente."""
    token = gerar_token_cliente(cliente, segredo)
    return f"/repasses/cliente/{quote(cliente, safe='')}?token={token}"
