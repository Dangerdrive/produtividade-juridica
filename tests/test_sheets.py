"""Testes do espelhamento no Google Sheets (API mockada)."""

from __future__ import annotations

import json
from datetime import date, datetime
from urllib.parse import unquote

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.models import Email, Prazo, StatusExtracao, utcnow
from app.services.sheets import (
    CABECALHOS_PLANILHA,
    SheetsClient,
    SheetsError,
    linha_prazo,
    sincronizar_planilha,
)
from tests.conftest import settings_teste


@pytest.fixture(scope="module")
def chave_privada() -> str:
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return chave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


class FakeSheets:
    """Planilha em memória + contador de chamadas."""

    def __init__(self, cabecalho=None, linhas=None, falhar_append=False):
        self.cabecalho: list[str] = cabecalho or []
        self.linhas: list[list[str]] = linhas or []
        self.falhar_append = falhar_append
        self.chamadas: list[str] = []
        self.atualizacoes: list[list[str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok-teste", "expires_in": 3600})

        caminho = unquote(request.url.path)
        self.chamadas.append(f"{request.method} {caminho}")

        if "/values:batchUpdate" in caminho:
            corpo = json.loads(request.content)
            for item in corpo["data"]:
                self.atualizacoes.append(item["values"][0])
            return httpx.Response(200, json={})

        if caminho.endswith(":append"):
            if self.falhar_append:
                return httpx.Response(403, text="permission denied")

            corpo = json.loads(request.content)
            inicio = len(self.linhas) + 2
            self.linhas.extend(corpo["values"])
            fim = inicio + len(corpo["values"]) - 1
            return httpx.Response(
                200,
                json={"updates": {"updatedRange": f"'DB_Prazos'!A{inicio}:L{fim}"}},
            )

        if request.method == "GET":
            referencia = caminho.split("!")[-1]
            if referencia.startswith("A1:"):
                return httpx.Response(200, json={"values": [self.cabecalho]} if self.cabecalho else {})
            if referencia == "A2:A":
                return httpx.Response(200, json={"values": [[linha[0]] for linha in self.linhas]})
            return httpx.Response(200, json={})

        if request.method == "PUT":
            self.cabecalho = json.loads(request.content)["values"][0]
            return httpx.Response(200, json={})

        return httpx.Response(200, json={})


def montar(chave_privada: str, fake: FakeSheets):
    settings = settings_teste(
        google_service_account_json=json.dumps(
            {
                "client_email": "bot@projeto.iam.gserviceaccount.com",
                "private_key": chave_privada,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        google_sheet_id="PLANILHA_TESTE",
    )
    cliente_http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    return settings, SheetsClient(settings, client=cliente_http, sleep=lambda _s: None)


def criar_prazo(db, *, message_id="MSG1", processo="0100138-89.2023.5.01.0074") -> Prazo:
    email = Email(
        gmail_message_id=message_id,
        gmail_thread_id="t-" + message_id,
        remetente_nome="Secretaria TJSP",
        remetente_email="protocolo@tjsp.jus.br",
        assunto="Intimação para manifestação",
        data_recebimento=datetime(2026, 11, 5, 12, 0),
        numero_processo=processo,
        status=StatusExtracao.OK.value,
    )
    email.prazos.append(
        Prazo(
            advogado="Dra. Maria Souza",
            tipo_prazo="Manifestação",
            data_prazo=date(2026, 11, 20),
            numero_processo=processo,
            status=StatusExtracao.OK.value,
        )
    )
    db.add(email)
    db.commit()
    return email.prazos[0]


class TestLinhaPrazo:
    def test_tem_todas_as_colunas(self, db):
        prazo = criar_prazo(db)
        linha = linha_prazo(prazo, "America/Sao_Paulo")

        assert len(linha) == len(CABECALHOS_PLANILHA)
        assert linha[0] == str(prazo.id)
        assert linha[1] == "0100138-89.2023.5.01.0074"
        assert linha[2] == "Dra. Maria Souza"
        assert linha[3] == "Manifestação"
        assert linha[4] == "2026-11-20"
        assert linha[10] == "MSG1"

    def test_data_recebimento_no_fuso_local(self, db):
        prazo = criar_prazo(db)
        linha = linha_prazo(prazo, "America/Sao_Paulo")
        assert linha[6] == "2026-11-05 09:00"


class TestCabecalho:
    def test_cria_quando_vazio(self, chave_privada):
        fake = FakeSheets()
        _, cliente = montar(chave_privada, fake)

        assert cliente.garantir_cabecalho() is True
        assert fake.cabecalho == list(CABECALHOS_PLANILHA)

    def test_nao_reescreve_quando_igual(self, chave_privada):
        fake = FakeSheets(cabecalho=list(CABECALHOS_PLANILHA))
        _, cliente = montar(chave_privada, fake)

        assert cliente.garantir_cabecalho() is False
        assert all(not c.startswith("PUT") for c in fake.chamadas)


class TestSincronizacao:
    def test_envia_novos_e_marca_linha(self, db, chave_privada):
        prazo = criar_prazo(db)
        fake = FakeSheets(cabecalho=list(CABECALHOS_PLANILHA))
        settings, cliente = montar(chave_privada, fake)

        resultado = sincronizar_planilha(db, settings, cliente=cliente)

        db.refresh(prazo)
        assert resultado.ok
        assert resultado.enviados == 1
        assert len(fake.linhas) == 1
        assert prazo.sheet_linha == 2
        assert prazo.sheet_sincronizado_em is not None

    def test_reexecucao_nao_duplica(self, db, chave_privada):
        criar_prazo(db)
        fake = FakeSheets(cabecalho=list(CABECALHOS_PLANILHA))
        settings, cliente = montar(chave_privada, fake)

        sincronizar_planilha(db, settings, cliente=cliente)
        segundo = sincronizar_planilha(db, settings, cliente=cliente)

        assert len(fake.linhas) == 1
        assert segundo.enviados == 0
        assert segundo.atualizados == 0

    def test_atualiza_quando_conteudo_muda(self, db, chave_privada):
        prazo = criar_prazo(db)
        fake = FakeSheets(cabecalho=list(CABECALHOS_PLANILHA))
        settings, cliente = montar(chave_privada, fake)

        sincronizar_planilha(db, settings, cliente=cliente)

        prazo.tipo_prazo = "Contrarrazões"
        prazo.atualizado_em = utcnow()
        db.commit()

        resultado = sincronizar_planilha(db, settings, cliente=cliente)

        assert resultado.atualizados == 1
        assert fake.atualizacoes[0][3] == "Contrarrazões"

    def test_erro_da_api_nao_levanta_excecao(self, db, chave_privada):
        criar_prazo(db)
        fake = FakeSheets(cabecalho=list(CABECALHOS_PLANILHA), falhar_append=True)
        settings, cliente = montar(chave_privada, fake)

        resultado = sincronizar_planilha(db, settings, cliente=cliente)

        assert resultado.ok is False
        assert "permission denied" in (resultado.erro or "")

    def test_sem_configuracao(self, db):
        resultado = sincronizar_planilha(db, settings_teste())
        assert resultado.ok is False
        assert "não configurada" in (resultado.erro or "").lower()


class TestCredenciais:
    def test_json_em_base64(self, chave_privada):
        import base64

        info = json.dumps(
            {
                "client_email": "bot@projeto.iam.gserviceaccount.com",
                "private_key": chave_privada,
            }
        )
        settings = settings_teste(
            google_service_account_json=base64.b64encode(info.encode()).decode(),
            google_sheet_id="X",
        )
        cliente = SheetsClient(settings, client=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        )))

        assert cliente._access_token() == "t"  # noqa: SLF001

    def test_json_invalido(self):
        settings = settings_teste(
            google_service_account_json="{isso nao e json", google_sheet_id="X"
        )
        cliente = SheetsClient(settings, client=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={})
        )))

        with pytest.raises(SheetsError):
            cliente._access_token()  # noqa: SLF001

    def test_sem_json(self):
        settings = settings_teste(google_service_account_json="", google_sheet_id="X")
        cliente = SheetsClient(settings)
        with pytest.raises(SheetsError):
            cliente._access_token()  # noqa: SLF001
