"""Testes da extração determinística (regex do assunto e do corpo)."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.extracao import (
    analisar,
    analisar_email,
    extrair_datas,
    extrair_numeros_processo,
    extrair_prazo_em_dias,
    extrair_processo_do_assunto,
    sugerir_tipo_prazo,
)

PROCESSO = "0100138-89.2023.5.01.0074"


class TestNumeroProcesso:
    def test_formato_canonico(self):
        assert extrair_numeros_processo(f"Intimação {PROCESSO}") == [PROCESSO]

    def test_formato_sem_pontuacao(self):
        assert extrair_numeros_processo("01001388920235010074") == [PROCESSO]

    def test_com_espacos(self):
        assert extrair_numeros_processo("0100138 89 2023 5 01 0074") == [PROCESSO]

    def test_variacao_de_separadores(self):
        assert extrair_numeros_processo("0100138-89.2023.501.0074") == [PROCESSO]

    def test_dois_processos(self):
        texto = (
            "Processos 0002222-33.2024.5.02.0001 e 0003333-44.2024.5.02.0002"
        )
        assert extrair_numeros_processo(texto) == [
            "0002222-33.2024.5.02.0001",
            "0003333-44.2024.5.02.0002",
        ]

    def test_nao_duplica(self):
        texto = f"{PROCESSO} e novamente {PROCESSO}"
        assert extrair_numeros_processo(texto) == [PROCESSO]

    @pytest.mark.parametrize(
        "texto",
        ["", None, "sem processo aqui", "12345-67", "protocolo 2024/00123"],
    )
    def test_sem_processo(self, texto):
        assert extrair_numeros_processo(texto) == []

    def test_numero_longo_demais_nao_casa(self):
        assert extrair_numeros_processo("010013889202350100741") == []

    def test_assunto_real(self):
        assunto = (
            "Intimação para manifestação - Processo: 0100138-89.2023.5.01.0074 - "
            "Autor: Fulano de Tal"
        )
        assert extrair_processo_do_assunto(assunto) == PROCESSO

    def test_assunto_sem_processo(self):
        assert extrair_processo_do_assunto("Informativo semanal") == ""


class TestDatas:
    def test_formato_br(self):
        datas = extrair_datas("Prazo até 20/11/2026")
        assert datas == [(date(2026, 11, 20), "20/11/2026")]

    def test_ano_com_dois_digitos(self):
        datas = extrair_datas("até 20/11/26")
        assert datas[0][0] == date(2026, 11, 20)

    def test_extenso(self):
        datas = extrair_datas("de 5 de novembro de 2026")
        assert datas[0][0] == date(2026, 11, 5)

    def test_data_impossivel_ignorada(self):
        assert extrair_datas("31/02/2026") == []

    def test_sem_data(self):
        assert extrair_datas("nenhuma data") == []


class TestTipos:
    @pytest.mark.parametrize(
        ("texto", "esperado"),
        [
            ("prazo para contrarrazões", "Contrarrazões"),
            ("apresentar razões finais", "Razões Finais"),
            ("intimação para manifestação", "Manifestação"),
            ("recurso de revista", "Recurso de Revista"),
            ("embargos de declaração", "Embargos de Declaração"),
        ],
    )
    def test_sugere_tipo(self, texto, esperado):
        assert esperado in sugerir_tipo_prazo(texto)

    def test_sem_sugestao(self):
        assert sugerir_tipo_prazo("bom dia") == []


class TestPrazoDias:
    def test_prazo_de_15_dias(self):
        assert extrair_prazo_em_dias("no prazo de 15 dias") == 15

    def test_dias_uteis(self):
        assert extrair_prazo_em_dias("prazo de 5 dias úteis") == 5

    def test_sem_prazo(self):
        assert extrair_prazo_em_dias("sem prazo definido") is None


class TestAnalise:
    def test_analisar_email_prioriza_assunto(self):
        analise = analisar_email(
            f"Intimação {PROCESSO}",
            "Prazo de 15 dias para contrarrazões até 20/11/2026.",
        )

        assert analise.processo_principal == PROCESSO
        assert date(2026, 11, 20) in analise.datas
        assert "Contrarrazões" in analise.tipos_sugeridos
        assert analise.prazo_dias == 15

    def test_processo_do_corpo_quando_ausente_no_assunto(self):
        analise = analisar_email("Intimação", f"Processo {PROCESSO}")
        assert analise.processos == [PROCESSO]

    def test_json(self):
        dados = analisar("x").as_dict()
        assert set(dados) == {
            "processos",
            "datas",
            "datas_brutas",
            "tipos_sugeridos",
            "prazo_dias",
        }
