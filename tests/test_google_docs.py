"""Testes do cliente de Google Docs (API mockada)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.modules.google.docs import (
    DocsClient,
    DocsError,
    extrair_texto,
    indice_final,
)
from tests.conftest import settings_teste

DOCUMENTO = {
    "documentId": "DOC1",
    "title": "Contrato",
    "body": {
        "content": [
            {"endIndex": 2, "sectionBreak": {}},
            {
                "startIndex": 2,
                "endIndex": 20,
                "paragraph": {
                    "elements": [{"textRun": {"content": "Primeira linha\n"}}]
                },
            },
            {
                "startIndex": 20,
                "endIndex": 40,
                "paragraph": {
                    "elements": [{"textRun": {"content": "Segunda linha\n"}}]
                },
            },
        ]
    },
}


class FakeAuth:
    def access_token(self, escopo: str) -> str:  # noqa: ARG002
        return "token-de-teste"


def montar(handler) -> DocsClient:
    settings = settings_teste(google_service_account_json="{}")
    cliente_http = httpx.Client(transport=httpx.MockTransport(handler))
    return DocsClient(settings, client=cliente_http, auth=FakeAuth())


class TestExtracao:
    def test_extrai_texto_dos_paragrafos(self):
        assert extrair_texto(DOCUMENTO) == "Primeira linha\nSegunda linha\n"

    def test_indice_final_seguro(self):
        assert indice_final(DOCUMENTO) == 39
        assert indice_final({}) == 1

    def test_extrai_texto_de_tabela(self):
        documento = {
            "body": {
                "content": [
                    {
                        "table": {
                            "tableRows": [
                                {
                                    "tableCells": [
                                        {
                                            "content": [
                                                {
                                                    "paragraph": {
                                                        "elements": [
                                                            {"textRun": {"content": "Célula"}}
                                                        ]
                                                    }
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
        }
        assert "Célula" in extrair_texto(documento)


class TestDocsClient:
    def test_ler_texto(self):
        cliente = montar(
            lambda request: httpx.Response(200, json=DOCUMENTO)
        )
        assert cliente.ler_texto("DOC1") == "Primeira linha\nSegunda linha\n"

    def test_criar_documento(self):
        cliente = montar(
            lambda request: httpx.Response(200, json={"documentId": "NOVO"})
        )
        assert cliente.criar_documento("Novo documento")["documentId"] == "NOVO"

    def test_anexar_texto_usa_indice_final(self):
        capturado: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json=DOCUMENTO)
            capturado.update(json.loads(request.content))
            return httpx.Response(200, json={})

        cliente = montar(handler)
        cliente.anexar_texto("DOC1", "Novo parágrafo")

        requisicao = capturado["requests"][0]["insertText"]
        assert requisicao["location"]["index"] == 39
        assert requisicao["text"] == "Novo parágrafo"

    def test_substituir_texto(self):
        capturado: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(request.content))
            return httpx.Response(200, json={})

        cliente = montar(handler)
        cliente.substituir_texto("DOC1", "antigo", "novo")

        requisicao = capturado["requests"][0]["replaceAllText"]
        assert requisicao["containsText"]["text"] == "antigo"
        assert requisicao["replaceText"] == "novo"

    def test_erro_da_api(self):
        cliente = montar(
            lambda request: httpx.Response(403, text="sem permissão")
        )
        with pytest.raises(DocsError, match="403"):
            cliente.ler_texto("DOC1")
