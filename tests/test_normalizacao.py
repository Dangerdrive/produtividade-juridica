"""Testes da normalização e do parsing de JSON do modelo."""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.prazos.services.deepseek import RespostaInvalida, parse_json_tolerante
from app.modules.prazos.services.normalizacao import (
    normalizar_data,
    normalizar_prazo,
    normalizar_prazos,
    normalizar_processo,
    texto_limpo,
)


class TestTextoLimpo:
    @pytest.mark.parametrize("valor", [None, "", "   ", "null", "NULL", "n/a", "-", "—"])
    def test_lixo_vira_string_vazia(self, valor):
        assert texto_limpo(valor) == ""

    def test_preserva_conteudo(self):
        assert texto_limpo("  Dra. Ana  ") == "Dra. Ana"


class TestNormalizarData:
    @pytest.mark.parametrize(
        "entrada",
        ["2026-11-20", "2026/11/20", "20/11/2026", "20-11-2026"],
    )
    def test_formatos_aceitos(self, entrada):
        data, bruta = normalizar_data(entrada)
        assert data == date(2026, 11, 20)
        assert bruta == ""

    def test_data_impossivel_e_preservada(self):
        data, bruta = normalizar_data("31/02/2026")
        assert data is None
        assert bruta == "31/02/2026"

    def test_texto_livre_e_preservado(self):
        data, bruta = normalizar_data("prazo de 15 dias")
        assert data is None
        assert bruta == "prazo de 15 dias"

    def test_ano_fora_da_faixa(self):
        data, bruta = normalizar_data("1800-01-01")
        assert data is None
        assert bruta == "1800-01-01"

    def test_nulo(self):
        assert normalizar_data(None) == (None, "")


class TestNormalizarProcesso:
    def test_cnj_valido(self):
        valor, ok = normalizar_processo("0001234-56.2024.8.26.0100")
        assert ok is True
        assert valor == "0001234-56.2024.8.26.0100"

    def test_remove_espacos(self):
        valor, ok = normalizar_processo(" 0001234-56.2024.8.26.0100 ")
        assert ok is True
        assert " " not in valor

    def test_invalido_preservado(self):
        valor, ok = normalizar_processo("1234-56")
        assert ok is False
        assert valor == "1234-56"


class TestNormalizarPrazo:
    def test_completo(self):
        prazo = normalizar_prazo(
            {
                "advogado": "Dra. Maria",
                "tipo_prazo": "Recurso de Revista",
                "data": "2026-11-20",
                "numero_processo": "0001234-56.2024.8.26.0100",
            }
        )
        assert prazo is not None
        assert prazo.status == "OK"
        assert prazo.data_prazo == date(2026, 11, 20)

    def test_incompleto_vira_revisar(self):
        prazo = normalizar_prazo(
            {"advogado": None, "tipo_prazo": None, "data": None, "numero_processo": None}
        )
        assert prazo is not None
        assert prazo.status == "REVISAR"
        assert prazo.advogado == "Não identificado"
        assert prazo.tipo_prazo == "Não identificado"

    def test_item_invalido(self):
        assert normalizar_prazo("texto") is None


class TestNormalizarPrazos:
    def test_deduplica(self):
        item = {
            "advogado": "Dra. Maria",
            "tipo_prazo": "Manifestação",
            "data": "2026-11-20",
            "numero_processo": "0001234-56.2024.8.26.0100",
        }
        prazos = normalizar_prazos({"prazos": [item, dict(item)]})
        assert len(prazos) == 1

    @pytest.mark.parametrize("payload", [None, {}, {"prazos": "x"}, []])
    def test_payload_invalido(self, payload):
        with pytest.raises(ValueError):
            normalizar_prazos(payload)


class TestParseJsonTolerante:
    def test_cerca_markdown(self):
        assert parse_json_tolerante('```json\n{"prazos": []}\n```') == {"prazos": []}

    def test_texto_ao_redor(self):
        assert parse_json_tolerante('Claro! {"prazos": []} Abraço.') == {"prazos": []}

    def test_sem_json(self):
        with pytest.raises(RespostaInvalida):
            parse_json_tolerante("não consegui responder")

    def test_json_truncado(self):
        with pytest.raises(RespostaInvalida):
            parse_json_tolerante('{"prazos": [')
