"""Testes da vitrine de módulos e das rotas transversais."""

from __future__ import annotations


class TestHome:
    def test_lista_os_modulos(self, client):
        resposta = client.get("/")

        assert resposta.status_code == 200
        assert "Prazos" in resposta.text
        assert "Repasses" in resposta.text
        assert "Integração Google" in resposta.text


class TestHealth:
    def test_agrega_estado_dos_modulos(self, client):
        dados = client.get("/health").json()

        assert dados["status"] == "ok"
        assert dados["modulos"]["prazos"]["gmail_configurado"] is False
        assert dados["modulos"]["repasses"]["planilha_configurada"] is False
        assert dados["modulos"]["google"]["conta_servico_configurada"] is False


class TestGoogle:
    def test_pagina_de_status(self, client):
        resposta = client.get("/google")

        assert resposta.status_code == 200
        assert "Integração Google" in resposta.text
        assert "Endpoints disponíveis" in resposta.text

    def test_api_status(self, client):
        dados = client.get("/api/google/status").json()

        assert dados["gmail_configurado"] is False
        assert dados["docs_disponivel"] is False

    def test_api_exige_token(self, client):
        resposta = client.get("/api/google/sheets/X/valores")
        assert resposta.status_code == 401


class TestRepassesPaginas:
    def test_painel_sem_configuracao_mostra_aviso(self, client):
        resposta = client.get("/repasses")

        assert resposta.status_code == 200
        assert "não configurada" in resposta.text

    def test_link_de_cliente_sem_segredo(self, client):
        resposta = client.get("/repasses/cliente/Qualquer Cliente")

        assert resposta.status_code == 503
        assert "REPASSES_CLIENTE_TOKEN_SECRET" in resposta.text
