"""Mapeamento entre os campos do domínio e os cabeçalhos da planilha.

A planilha de repasses pode ter colunas em ordens diferentes e com nomes
ligeiramente distintos. O mapeamento é feito por nome (tolerante a acentos e
maiúsculas) e pode ser sobreposto com ``REPASSES_COLUNAS_JSON``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.modules.google.sheets import letra_coluna
from app.modules.repasses.utils import normalizar_chave

#: Cabeçalho criado quando a planilha está vazia.
CABECALHOS_PADRAO: tuple[str, ...] = (
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
)

#: Nomes aceitos para cada campo (o primeiro é o usado no cabeçalho padrão).
COLUNAS_PADRAO: dict[str, tuple[str, ...]] = {
    "id": ("ID", "Id", "ID do Repasse", "Código", "Codigo"),
    "cliente": ("Cliente", "Nome do Cliente", "Cliente (Nome)", "Sacado"),
    "processo": (
        "Processo",
        "Número do Processo",
        "Numero do Processo",
        "Nº do Processo",
        "No do Processo",
    ),
    "valor": ("Valor", "Valor do Repasse", "Valor (R$)", "Montante"),
    "data_prevista": (
        "Data Prevista",
        "Data de Vencimento",
        "Vencimento",
        "Prazo",
        "Data Limite",
    ),
    "data_pagamento": (
        "Data do Pagamento",
        "Data Pagamento",
        "Pago Em",
        "Data de Pagamento",
    ),
    "status": ("Status", "Situação", "Situacao", "Estado"),
    "responsavel": ("Responsável", "Responsavel", "Responsável pelo Repasse"),
    "observacoes": ("Observações", "Observacoes", "Observação", "Obs"),
    "atualizado_em": ("Atualizado Em", "Atualização", "Atualizacao", "Última Atualização"),
}

CAMPOS_ATUALIZAVEIS: frozenset[str] = frozenset(
    {
        "cliente",
        "processo",
        "valor",
        "data_prevista",
        "data_pagamento",
        "status",
        "responsavel",
        "observacoes",
    }
)


@dataclass(slots=True)
class MapaColunas:
    """Índices (0-based) de cada campo no cabeçalho atual."""

    indices: dict[str, int]
    cabecalho: list[str]

    @property
    def ultima_coluna(self) -> str:
        return letra_coluna(max(len(self.cabecalho), 1))

    def tem(self, campo: str) -> bool:
        return campo in self.indices

    def indice(self, campo: str) -> int | None:
        return self.indices.get(campo)

    def valor(self, linha: list[str], campo: str) -> str:
        indice = self.indices.get(campo)
        if indice is None or indice >= len(linha):
            return ""
        return str(linha[indice] or "").strip()

    def definir(self, linha: list[str], campo: str, valor: str) -> None:
        indice = self.indices.get(campo)
        if indice is None:
            raise KeyError(campo)
        while len(linha) <= indice:
            linha.append("")
        linha[indice] = valor


def mapear_colunas(
    cabecalho: list[str], *, overrides: dict[str, str] | None = None
) -> MapaColunas:
    """Resolve os índices dos campos a partir do cabeçalho da planilha."""
    normalizados = {
        normalizar_chave(nome): indice
        for indice, nome in enumerate(cabecalho)
        if normalizar_chave(nome)
    }

    indices: dict[str, int] = {}
    for campo, aliases in COLUNAS_PADRAO.items():
        desejado = (overrides or {}).get(campo)
        candidatos = (desejado,) if desejado else aliases

        for candidato in candidatos:
            chave = normalizar_chave(candidato)
            if chave in normalizados:
                indices[campo] = normalizados[chave]
                break

    return MapaColunas(
        indices=indices,
        cabecalho=[str(nome).strip() for nome in cabecalho],
    )


def carregar_overrides(configuracao_json: str) -> dict[str, str]:
    """Lê ``REPASSES_COLUNAS_JSON`` (campo -> cabeçalho exato)."""
    bruto = (configuracao_json or "").strip()
    if not bruto:
        return {}

    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError:
        return {}

    if not isinstance(dados, dict):
        return {}

    return {
        str(campo): str(cabecalho)
        for campo, cabecalho in dados.items()
        if str(campo) in COLUNAS_PADRAO and str(cabecalho).strip()
    }
