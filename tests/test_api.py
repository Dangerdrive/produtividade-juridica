"""Testes das rotas HTTP."""

from __future__ import annotations

from datetime import date, timedelta

from app.modules.prazos.models import Email, Prazo, StatusExtracao, utcnow


def semear(db) -> Email:
    email = Email(
        gmail_message_id="MSG-API",
        gmail_thread_id="thread-api",
        remetente_nome="Secretaria TJSP",
        remetente_email="protocolo@tjsp.jus.br",
        assunto="Intimação para manifestação",
        data_recebimento=utcnow(),
        status=StatusExtracao.OK.value,
    )
    email.prazos.append(
        Prazo(
            advogado="Dra. Maria Souza",
            tipo_prazo="Manifestação",
            data_prazo=date.today() + timedelta(days=10),
            numero_processo="0001234-56.2024.8.26.0100",
            status=StatusExtracao.OK.value,
        )
    )
    db.add(email)
    db.commit()
    return email


class TestHealth:
    def test_responde_ok(self, client):
        resposta = client.get("/health")
        assert resposta.status_code == 200

        dados = resposta.json()
        assert dados["status"] == "ok"
        assert dados["banco"] is True
        assert dados["gmail_configurado"] is False


class TestPaginas:
    def test_dashboard_vazio(self, client):
        resposta = client.get("/prazos")
        assert resposta.status_code == 200
        assert "Dashboard" in resposta.text
        assert "Nenhum prazo registrado" in resposta.text

    def test_dashboard_com_dados(self, client, db):
        semear(db)

        resposta = client.get("/prazos")
        assert resposta.status_code == 200
        assert "Dra. Maria Souza" in resposta.text
        assert "0001234-56.2024.8.26.0100" in resposta.text

    def test_listagem_de_prazos(self, client, db):
        semear(db)

        resposta = client.get("/prazos/lista")
        assert resposta.status_code == 200
        assert "0001234-56.2024.8.26.0100" in resposta.text
        assert "protocolo@tjsp.jus.br" in resposta.text or "Secretaria TJSP" in resposta.text

    def test_filtro_sem_resultado(self, client, db):
        semear(db)

        resposta = client.get("/prazos/lista", params={"advogado": "Inexistente"})
        assert resposta.status_code == 200
        assert "Nenhum prazo encontrado" in resposta.text

    def test_filtro_por_data_invalida_nao_quebra(self, client):
        resposta = client.get("/prazos", params={"inicio": "data-invalida"})
        assert resposta.status_code == 200


class TestApiJson:
    def test_stats(self, client, db):
        semear(db)

        resposta = client.get("/api/prazos/stats")
        assert resposta.status_code == 200

        dados = resposta.json()
        assert dados["resumo"]["total_emails"] == 1
        assert dados["resumo"]["total_prazos"] == 1
        assert dados["matriz"]["advogados"] == ["Dra. Maria Souza"]
        assert dados["matriz"]["total"] == 1

    def test_lista_prazos(self, client, db):
        semear(db)

        resposta = client.get("/api/prazos")
        assert resposta.status_code == 200

        dados = resposta.json()
        assert dados["total"] == 1
        item = dados["itens"][0]
        assert item["numero_processo"] == "0001234-56.2024.8.26.0100"
        assert item["email"]["remetente_email"] == "protocolo@tjsp.jus.br"

    def test_paginacao(self, client, db):
        semear(db)

        resposta = client.get("/api/prazos", params={"pagina": 5})
        assert resposta.status_code == 200
        assert resposta.json()["itens"] == []


class TestSincronizacao:
    def test_exige_token(self, client):
        resposta = client.post("/api/prazos/sync")
        assert resposta.status_code == 401

    def test_token_invalido(self, client):
        resposta = client.post(
            "/api/prazos/sync", headers={"Authorization": "Bearer errado"}
        )
        assert resposta.status_code == 401

    def test_token_valido_sem_credenciais(self, client):
        resposta = client.post(
            "/api/prazos/sync",
            headers={"Authorization": "Bearer token-de-teste"},
        )
        assert resposta.status_code == 503
        assert "Gmail" in resposta.json()["detail"]

    def test_cron_aceita_token_na_query(self, client):
        resposta = client.get(
            "/api/prazos/cron/sync", params={"token": "token-de-teste"}
        )
        assert resposta.status_code == 503


class TestProtecaoBasica:
    def test_sem_senha_nao_exige_autenticacao(self, client):
        assert client.get("/").status_code == 200

    def test_com_senha_exige_basic(self, client, monkeypatch):
        from app import main as main_module

        monkeypatch.setattr(main_module.settings, "app_password", "segredo", raising=False)

        assert client.get("/").status_code == 401
        assert client.get("/health").status_code == 200

        autenticado = client.get("/", auth=("admin", "segredo"))
        assert autenticado.status_code == 200
