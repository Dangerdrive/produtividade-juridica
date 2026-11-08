"""Testes de configuração de múltiplas contas Gmail."""

from __future__ import annotations

import json

import pytest

from app.modules.google.contas import (
    ContasError,
    carregar_contas,
    nome_conta_padrao,
    obter_conta,
)
from app.modules.google.gmail import GmailClient, GmailError
from tests.conftest import settings_teste


def settings_contas(**overrides):
    base = {
        "gmail_client_id": "cliente-id",
        "gmail_client_secret": "cliente-secret",
        "gmail_refresh_token": "token-padrao",
    }
    base.update(overrides)
    return settings_teste(**base)


class TestCarregarContas:
    def test_conta_unica_vira_padrao(self):
        contas = carregar_contas(settings_contas())

        assert list(contas) == ["padrao"]
        assert contas["padrao"].configurada

    def test_multiplas_contas_reutilizam_cliente_oauth(self):
        settings = settings_contas(
            gmail_accounts_json=json.dumps(
                {
                    "trabalho": {"refresh_token": "token-trabalho"},
                    "pessoal": "token-pessoal",
                }
            )
        )
        contas = carregar_contas(settings)

        assert set(contas) == {"padrao", "trabalho", "pessoal"}
        assert contas["trabalho"].client_id == "cliente-id"
        assert contas["trabalho"].refresh_token == "token-trabalho"
        assert contas["pessoal"].refresh_token == "token-pessoal"

    def test_conta_com_cliente_proprio(self):
        settings = settings_contas(
            gmail_accounts_json=json.dumps(
                {
                    "outra": {
                        "client_id": "outro-id",
                        "client_secret": "outro-secret",
                        "refresh_token": "outro-token",
                        "query": "in:sent",
                        "rotulo": "Outra conta",
                    }
                }
            )
        )
        conta = obter_conta(settings, "outra")

        assert conta.client_id == "outro-id"
        assert conta.query == "in:sent"
        assert conta.descricao == "Outra conta"
        assert conta.configurada

    def test_lista_de_contas(self):
        settings = settings_contas(
            gmail_accounts_json=json.dumps(
                [
                    {"nome": "a", "refresh_token": "ta"},
                    {"nome": "b", "refresh_token": "tb"},
                ]
            )
        )
        assert set(carregar_contas(settings)) == {"a", "b", "padrao"}

    def test_conta_padrao_configuravel(self):
        settings = settings_contas(
            gmail_accounts_json=json.dumps({"trabalho": "token-trabalho"}),
            gmail_conta_padrao="trabalho",
        )
        contas = carregar_contas(settings)

        assert nome_conta_padrao(settings, contas) == "trabalho"
        assert obter_conta(settings).nome == "trabalho"

    def test_conta_inexistente(self):
        with pytest.raises(ContasError, match="não encontrada"):
            obter_conta(settings_contas(), "inexistente")

    def test_conta_incompleta(self):
        settings = settings_teste(
            gmail_accounts_json=json.dumps({"quebrada": {"query": "in:inbox"}})
        )
        with pytest.raises(ContasError, match="incompleta"):
            obter_conta(settings, "quebrada")

    def test_json_invalido(self):
        settings = settings_teste(gmail_accounts_json="{nao e json")
        with pytest.raises(ContasError, match="inválido"):
            carregar_contas(settings)


class TestGmailClientMultiConta:
    def test_cliente_usa_conta_escolhida(self):
        settings = settings_contas(
            gmail_accounts_json=json.dumps(
                {"trabalho": {"refresh_token": "token-trabalho"}}
            )
        )
        cliente = GmailClient(settings, conta="trabalho")

        assert cliente.nome_conta == "trabalho"
        assert cliente.conta.refresh_token == "token-trabalho"
        assert cliente.conta.client_id == "cliente-id"

    def test_cliente_sem_contas(self):
        settings = settings_teste()
        with pytest.raises(GmailError, match="Nenhuma conta"):
            GmailClient(settings)
