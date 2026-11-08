"""Testes do módulo de repasses (planilha em memória, domínio e API)."""

from __future__ import annotations

import re
from datetime import date

import pytest

from app.modules.repasses.colunas import mapear_colunas
from app.modules.repasses.modelos import StatusRepasse
from app.modules.repasses.services import dominio
from app.modules.repasses.services.planilha import RepassesError, RepassesPlanilha
from app.modules.repasses.tokens import (
    TokenClienteError,
    gerar_token_cliente,
    link_cliente,
    validar_token_cliente,
)
from tests.conftest import settings_teste

CABECALHO = [
    "ID",
    "Cliente",
    "Processo",
    "Valor",
    "Data Prevista",
    "Data do Pagamento",
    "Status",
    "Responsável",
    "Observações",
    "Atualizado Em",
]

LINHAS = [
    ["R1", "Maria Souza", "0001", "1.500,00", "10/11/2026", "", "Pendente", "Ana", "", ""],
    ["R2", "João Lima", "0002", "800.00", "01/10/2026", "05/10/2026", "Concluído", "Bia", "", ""],
    ["R3", "Maria Souza", "0003", "250,50", "", "", "Em andamento", "Ana", "", ""],
    ["R4", "João Lima", "0004", "120,00", "01/10/2026", "", "Pendente", "Bia", "", ""],
]


class FakeSheets:
    """Planilha em memória compatível com a interface usada pelo módulo."""

    def __init__(self, valores: list[list[str]] | None = None) -> None:
        self.valores = [list(linha) for linha in (valores or [])]
        self.escritas: list[tuple[str, list[list[str]]]] = []
        self.anexos: list[list[list[str]]] = []

    def ler_valores(self, referencia: str = "A1:ZZ", *, aba: str | None = None):
        return [list(linha) for linha in self.valores]

    def escrever_valores(
        self, referencia: str, valores: list[list[str]], *, aba: str | None = None
    ):
        self.escritas.append((referencia, [list(v) for v in valores]))

        if referencia == "A1":
            self.valores = [list(valores[0])]
            return {}

        achado = re.search(r"A(\d+)", referencia)
        if achado:
            linha = int(achado.group(1))
            while len(self.valores) < linha:
                self.valores.append([])
            self.valores[linha - 1] = list(valores[0])
        return {}

    def anexar(
        self,
        valores: list[list[str]],
        *,
        referencia: str = "A1",
        aba: str | None = None,
    ):
        self.anexos.append([list(v) for v in valores])
        self.valores.extend([list(v) for v in valores])
        return ""


def settings_repasses(**overrides):
    base = {
        "repasses_sheet_id": "PLANILHA_REPASSES",
        "repasses_sheet_tab": "Repasses",
        "google_service_account_json": "{}",
        "repasses_cliente_token_secret": "segredo-de-teste",
    }
    base.update(overrides)
    return settings_teste(**base)


def planilha(valores=None, **overrides) -> RepassesPlanilha:
    return RepassesPlanilha(
        settings_repasses(**overrides),
        sheets=FakeSheets(valores if valores is not None else [CABECALHO, *LINHAS]),
    )


class TestLeitura:
    def test_lista_e_converte_tipos(self):
        repasses = planilha().listar()

        assert len(repasses) == 4

        r1 = repasses[0]
        assert r1.id == "R1"
        assert r1.cliente == "Maria Souza"
        assert float(r1.valor) == pytest.approx(1500)
        assert r1.data_prevista == date(2026, 11, 10)
        assert r1.status is StatusRepasse.PENDENTE

        r2 = repasses[1]
        assert float(r2.valor) == pytest.approx(800)
        assert r2.status is StatusRepasse.CONCLUIDO
        assert r2.data_pagamento == date(2026, 10, 5)

        r3 = repasses[2]
        assert r3.status is StatusRepasse.EM_ANDAMENTO
        assert r3.data_prevista is None

    def test_planilha_vazia(self):
        assert planilha([]).listar() == []

    def test_erro_claro_sem_coluna_de_cliente(self):
        with pytest.raises(RepassesError, match="cliente"):
            planilha([["ID", "Valor"], ["1", "10,00"]]).listar()

    def test_mapeamento_tolerante_a_nomes(self):
        mapa = mapear_colunas(["Código", "Sacado", "Vencimento", "Situação"])
        assert mapa.indice("id") == 0
        assert mapa.indice("cliente") == 1
        assert mapa.indice("data_prevista") == 2
        assert mapa.indice("status") == 3


class TestDominio:
    def test_pendentes_ordenados_por_vencimento(self):
        repasses = planilha().listar()
        pendentes = dominio.pendentes(repasses)

        assert [r.id for r in pendentes] == ["R4", "R1", "R3"]
        assert dominio.atrasados(repasses, hoje=date(2026, 11, 8))[0].id == "R4"

    def test_resumo(self):
        resumo = dominio.resumir(planilha().listar())

        assert resumo.total == 4
        assert resumo.pendentes == 2
        assert resumo.em_andamento == 1
        assert resumo.concluidos == 1
        assert resumo.atrasados == 1
        assert float(resumo.valor_total) == pytest.approx(2670.50)
        assert float(resumo.valor_aberto) == pytest.approx(1870.50)
        assert float(resumo.valor_pago) == pytest.approx(800)

    def test_resumo_por_cliente(self):
        repasses = planilha().listar()
        resumo = dominio.resumo_cliente(repasses, "maria souza")

        assert resumo.cliente == "Maria Souza"
        assert resumo.resumo.total == 2
        assert float(resumo.resumo.valor_aberto) == pytest.approx(1750.50)
        assert resumo.proxima_data_prevista == date(2026, 11, 10)

    def test_filtro_por_status_e_texto(self):
        repasses = planilha().listar()

        pendentes = dominio.filtrar(repasses, status="pendente")
        assert {r.id for r in pendentes} == {"R1", "R4"}

        encontrados = dominio.filtrar(repasses, texto="0002")
        assert [r.id for r in encontrados] == ["R2"]


class TestEscrita:
    def test_atualiza_campos_preservando_linha(self):
        repo = planilha()

        atualizado = repo.atualizar(
            "R1",
            {
                "status": "concluido",
                "valor": "1.600,00",
                "data_pagamento": "2026-11-09",
            },
        )

        assert atualizado.status is StatusRepasse.CONCLUIDO
        assert float(atualizado.valor) == pytest.approx(1600)
        assert atualizado.data_pagamento == date(2026, 11, 9)

        referencia, linhas = repo.sheets.escritas[-1]
        assert referencia.startswith("A2:")
        assert linhas[0][0] == "R1"
        assert linhas[0][3] == "1.600,00"
        assert linhas[0][5] == "09/11/2026"
        assert linhas[0][6] == "CONCLUIDO"

    def test_cria_repasse_na_planilha(self):
        repo = planilha()

        novo = repo.criar(
            {
                "cliente": "Novo Cliente",
                "processo": "9999",
                "valor": "99,90",
                "data_prevista": "2026-12-01",
                "status": "pendente",
            }
        )

        assert novo.cliente == "Novo Cliente"
        assert float(novo.valor) == pytest.approx(99.90)
        assert novo.data_prevista == date(2026, 12, 1)
        assert len(repo.sheets.anexos) == 1

        anexada = repo.sheets.anexos[0][0]
        assert anexada[1] == "Novo Cliente"
        assert anexada[3] == "99,90"
        assert anexada[4] == "01/12/2026"
        assert anexada[6] == "PENDENTE"

    def test_cria_cabecalho_quando_planilha_vazia(self):
        repo = planilha([])

        novo = repo.criar({"cliente": "Cliente Novo", "valor": "10,00"})

        assert novo.cliente == "Cliente Novo"
        assert repo.sheets.escritas[0][0] == "A1"
        assert repo.sheets.escritas[0][1][0][1] == "Cliente"

    def test_atualiza_inexistente(self):
        with pytest.raises(RepassesError, match="não encontrado"):
            planilha().atualizar("ZZZ", {"status": "PENDENTE"})


class TestTokens:
    def test_token_estavel_e_validavel(self):
        token = gerar_token_cliente("Maria Souza", "segredo")
        assert validar_token_cliente("maria souza", token, "segredo")
        assert not validar_token_cliente("Maria Souza", token, "outro")

    def test_link_individual(self):
        link = link_cliente("Maria Souza", "segredo")
        assert link.startswith("/repasses/cliente/Maria%20Souza?token=")

    def test_exige_segredo(self):
        with pytest.raises(TokenClienteError):
            gerar_token_cliente("Maria", "")


class TestApiRepasses:
    def _override(self, settings, repo):
        from app.core.deps import settings_dep
        from app.main import app
        from app.modules.repasses.deps import get_planilha

        antes = dict(app.dependency_overrides)
        app.dependency_overrides[settings_dep] = lambda: settings
        app.dependency_overrides[get_planilha] = lambda: repo
        return app, antes

    @staticmethod
    def _restaurar(app, antes):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(antes)

    def test_lista_com_token(self, client):
        settings = settings_repasses()
        repo = RepassesPlanilha(settings, sheets=FakeSheets([CABECALHO, *LINHAS]))
        app, antes = self._override(settings, repo)

        try:
            resposta = client.get(
                "/api/repasses",
                headers={"Authorization": "Bearer token-de-teste"},
            )
            assert resposta.status_code == 200
            assert resposta.json()["total"] == 4
        finally:
            self._restaurar(app, antes)

    def test_status_publico_do_cliente(self, client):
        settings = settings_repasses()
        repo = RepassesPlanilha(settings, sheets=FakeSheets([CABECALHO, *LINHAS]))
        app, antes = self._override(settings, repo)
        token = gerar_token_cliente(
            "Maria Souza", settings.repasses_cliente_token_secret
        )

        try:
            resposta = client.get(
                "/api/repasses/cliente/Maria Souza", params={"token": token}
            )
            assert resposta.status_code == 200
            dados = resposta.json()
            assert dados["cliente"] == "Maria Souza"
            assert len(dados["repasses"]) == 2
        finally:
            self._restaurar(app, antes)

    def test_status_publico_token_invalido(self, client):
        settings = settings_repasses()
        repo = RepassesPlanilha(settings, sheets=FakeSheets([CABECALHO, *LINHAS]))
        app, antes = self._override(settings, repo)

        try:
            resposta = client.get(
                "/api/repasses/cliente/Maria Souza", params={"token": "errado"}
            )
            assert resposta.status_code == 403
        finally:
            self._restaurar(app, antes)

    def test_painel_html_com_dados(self, client):
        settings = settings_repasses()
        repo = RepassesPlanilha(settings, sheets=FakeSheets([CABECALHO, *LINHAS]))
        app, antes = self._override(settings, repo)

        try:
            resposta = client.get("/repasses")
            assert resposta.status_code == 200
            assert "Tarefas pendentes" in resposta.text
            assert "Maria Souza" in resposta.text
            assert "João Lima" in resposta.text
        finally:
            self._restaurar(app, antes)

    def test_pagina_publica_do_cliente(self, client):
        settings = settings_repasses()
        repo = RepassesPlanilha(settings, sheets=FakeSheets([CABECALHO, *LINHAS]))
        app, antes = self._override(settings, repo)
        token = gerar_token_cliente(
            "Maria Souza", settings.repasses_cliente_token_secret
        )

        try:
            resposta = client.get(
                "/repasses/cliente/Maria Souza", params={"token": token}
            )
            assert resposta.status_code == 200
            assert "Seus repasses" in resposta.text
            assert "Maria Souza" in resposta.text
            assert "João Lima" not in resposta.text
        finally:
            self._restaurar(app, antes)
