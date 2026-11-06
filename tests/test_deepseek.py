"""Testes do cliente DeepSeek (retry, erros, payload)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.deepseek import (
    DeepSeekClient,
    DeepSeekError,
    RespostaInvalida,
    montar_prompt_usuario,
)
from tests.conftest import settings_teste

PRAZO = {
    "advogado": "Dra. Maria",
    "tipo_prazo": "Manifestação",
    "data": "2026-11-20",
    "numero_processo": "0001234-56.2024.8.26.0100",
}


def resposta_ok(conteudo: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": conteudo}}]},
    )


def montar(respostas: list) -> tuple[DeepSeekClient, list[dict]]:
    chamadas: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(json.loads(request.content))
        indice = min(len(chamadas) - 1, len(respostas) - 1)
        item = respostas[indice]
        if isinstance(item, Exception):
            raise item
        return item

    cliente_http = httpx.Client(transport=httpx.MockTransport(handler))
    settings = settings_teste(deepseek_api_key="sk-teste", deepseek_max_tentativas=3)
    return DeepSeekClient(settings, client=cliente_http, sleep=lambda _s: None), chamadas


def extrair(cliente: DeepSeekClient):
    return cliente.extrair_prazos(
        assunto="Intimação",
        remetente_nome="Secretaria",
        remetente_email="protocolo@tjsp.jus.br",
        data_recebimento="05/11/2026 12:00",
        corpo="Prazo de 15 dias no processo 0001234-56.2024.8.26.0100.",
    )


class TestPayload:
    def test_formato_da_requisicao(self):
        cliente, chamadas = montar([resposta_ok(json.dumps({"prazos": [PRAZO]}))])
        extrair(cliente)

        payload = chamadas[0]
        assert payload["model"] == "deepseek-chat"
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["temperature"] == 0
        assert payload["stream"] is False
        assert "json" in payload["messages"][0]["content"].lower()
        assert "0001234-56.2024.8.26.0100" in payload["messages"][1]["content"]

    def test_prompt_inclui_metadados_do_email(self):
        prompt = montar_prompt_usuario(
            assunto="Intimação",
            remetente_nome="Secretaria",
            remetente_email="p@t.jus.br",
            data_recebimento="05/11/2026 12:00",
            corpo="x" * 20_000,
        )
        assert "05/11/2026 12:00" in prompt
        assert "Secretaria <p@t.jus.br>" in prompt
        assert "[... texto truncado ...]" in prompt


class TestSucesso:
    def test_extrai_prazos_normalizados(self):
        cliente, _ = montar([resposta_ok(json.dumps({"prazos": [PRAZO]}))])
        prazos = extrair(cliente)

        assert len(prazos) == 1
        assert prazos[0].numero_processo == "0001234-56.2024.8.26.0100"
        assert prazos[0].status == "OK"

    def test_sem_prazos(self):
        cliente, _ = montar([resposta_ok('{"prazos": []}')])
        assert extrair(cliente) == []


class TestErros:
    def test_json_invalido(self):
        cliente, _ = montar([resposta_ok("não sei responder")])
        with pytest.raises(RespostaInvalida) as exc:
            extrair(cliente)
        assert exc.value.status == "ERRO_JSON"

    def test_retry_em_500(self):
        cliente, chamadas = montar(
            [
                httpx.Response(500, text="boom"),
                resposta_ok(json.dumps({"prazos": [PRAZO]})),
            ]
        )
        prazos = extrair(cliente)
        assert len(chamadas) == 2
        assert len(prazos) == 1

    def test_sem_retry_em_401(self):
        cliente, chamadas = montar([httpx.Response(401, text="no auth")])
        with pytest.raises(DeepSeekError) as exc:
            extrair(cliente)
        assert exc.value.status == "ERRO_IA"
        assert len(chamadas) == 1

    def test_esgotar_tentativas_em_429(self):
        cliente, chamadas = montar([httpx.Response(429, text="rate limit")])
        with pytest.raises(DeepSeekError):
            extrair(cliente)
        assert len(chamadas) == 3

    def test_falha_de_rede(self):
        cliente, chamadas = montar([httpx.ConnectError("sem rede")])
        with pytest.raises(DeepSeekError):
            extrair(cliente)
        assert len(chamadas) == 3

    def test_sem_api_key(self):
        settings = settings_teste(deepseek_api_key="")
        cliente = DeepSeekClient(settings, client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={})
        )))
        with pytest.raises(DeepSeekError):
            extrair(cliente)
