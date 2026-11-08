"""Leitura e escrita da planilha específica de repasses.

A planilha é a fonte da verdade do módulo. O acesso HTTP fica no módulo Google;
aqui ficam o mapeamento de colunas, a conversão de tipos e as operações de
negócio (listar, criar e atualizar repasses).
"""

from __future__ import annotations

from uuid import uuid4

from app.core.config import Settings
from app.modules.google.sheets import SheetsClient, SheetsError
from app.modules.repasses.colunas import (
    CABECALHOS_PADRAO,
    CAMPOS_ATUALIZAVEIS,
    MapaColunas,
    carregar_overrides,
    mapear_colunas,
)
from app.modules.repasses.modelos import (
    Repasse,
    StatusRepasse,
    normalizar_status,
)
from app.modules.repasses.utils import (
    agora_local,
    formatar_data,
    formatar_valor,
    parse_data,
    parse_valor,
)


class RepassesError(Exception):
    """Falha de configuração ou de acesso à planilha de repasses."""


def _formatar_campo(campo: str, valor: object) -> str:
    if valor is None:
        return ""

    if campo == "valor":
        numero = parse_valor(valor)
        return formatar_valor(numero) if numero is not None else str(valor).strip()

    if campo in ("data_prevista", "data_pagamento"):
        data = parse_data(valor)
        return formatar_data(data) if data else str(valor).strip()

    if campo == "status":
        return normalizar_status(valor).value

    return str(valor).strip()


class RepassesPlanilha:
    """Repositório de repasses apoiado na planilha do Google Sheets."""

    def __init__(
        self, settings: Settings, *, sheets: SheetsClient | None = None
    ) -> None:
        self.settings = settings
        self.sheets = sheets or SheetsClient(
            settings,
            sheet_id=settings.repasses_sheet_id,
            aba=settings.repasses_sheet_tab,
        )
        self._overrides = carregar_overrides(settings.repasses_colunas_json)

    # ------------------------------------------------------------------
    @property
    def configurado(self) -> bool:
        return self.settings.repasses_configurado

    def _exigir_configuracao(self) -> None:
        if not self.configurado:
            raise RepassesError(
                "Planilha de repasses não configurada "
                "(GOOGLE_SERVICE_ACCOUNT_JSON / REPASSES_SHEET_ID)."
            )

    def _ler(self) -> tuple[list[str], list[tuple[int, list[str]]]]:
        self._exigir_configuracao()

        try:
            valores = self.sheets.ler_valores("A1:ZZ")
        except SheetsError as exc:
            raise RepassesError(str(exc)) from exc

        if not valores:
            return [], []

        cabecalho = [str(celula).strip() for celula in valores[0]]
        while cabecalho and not cabecalho[-1]:
            cabecalho.pop()

        linhas: list[tuple[int, list[str]]] = []
        for numero, linha in enumerate(valores[1:], start=2):
            if not any(str(celula).strip() for celula in linha):
                continue
            linhas.append((numero, [str(celula) for celula in linha]))

        return cabecalho, linhas

    def _mapa(self, cabecalho: list[str], *, exigir_id: bool = False) -> MapaColunas:
        mapa = mapear_colunas(cabecalho, overrides=self._overrides)

        if not mapa.tem("cliente"):
            raise RepassesError(
                "A planilha não possui uma coluna de cliente reconhecida. "
                "Cabeçalho encontrado: "
                + (", ".join(cabecalho) or "(vazio)")
            )

        if exigir_id and not mapa.tem("id"):
            raise RepassesError(
                "A planilha de repasses precisa de uma coluna de ID "
                "(aceita: ID, ID do Repasse, Código)."
            )

        return mapa

    # ------------------------------------------------------------------
    def listar(self) -> list[Repasse]:
        """Todas as linhas válidas da planilha."""
        cabecalho, linhas = self._ler()
        if not cabecalho:
            return []

        mapa = self._mapa(cabecalho)
        return [
            self._para_repasse(mapa, linha, numero) for numero, linha in linhas
        ]

    def obter(self, repasse_id: str) -> Repasse | None:
        alvo = str(repasse_id or "").strip().casefold()
        for repasse in self.listar():
            if repasse.id.casefold() == alvo:
                return repasse
        return None

    def _para_repasse(
        self, mapa: MapaColunas, linha: list[str], numero_linha: int
    ) -> Repasse:
        bruto = {
            mapa.cabecalho[indice]: str(linha[indice])
            for indice in range(min(len(linha), len(mapa.cabecalho)))
        }

        return Repasse(
            id=mapa.valor(linha, "id") or f"linha-{numero_linha}",
            cliente=mapa.valor(linha, "cliente") or "Não informado",
            processo=mapa.valor(linha, "processo"),
            valor=parse_valor(mapa.valor(linha, "valor")),
            data_prevista=parse_data(mapa.valor(linha, "data_prevista")),
            data_pagamento=parse_data(mapa.valor(linha, "data_pagamento")),
            status=normalizar_status(mapa.valor(linha, "status")),
            responsavel=mapa.valor(linha, "responsavel"),
            observacoes=mapa.valor(linha, "observacoes"),
            atualizado_em=None,
            linha=numero_linha,
            bruto=bruto,
        )

    # ------------------------------------------------------------------
    def garantir_planilha(self) -> tuple[MapaColunas, list[tuple[int, list[str]]]]:
        """Garante um cabeçalho utilizável, criando-o se a planilha estiver vazia."""
        cabecalho, linhas = self._ler()

        if not cabecalho:
            cabecalho = list(CABECALHOS_PADRAO)
            try:
                self.sheets.escrever_valores("A1", [cabecalho])
            except SheetsError as exc:
                raise RepassesError(str(exc)) from exc

        return self._mapa(cabecalho, exigir_id=True), linhas

    def criar(self, dados: dict) -> Repasse:
        """Acrescenta um repasse à planilha."""
        mapa, linhas = self.garantir_planilha()

        cliente = str(dados.get("cliente") or "").strip()
        if not cliente:
            raise RepassesError("O campo 'cliente' é obrigatório.")

        novo_id = str(dados.get("id") or "").strip() or uuid4().hex[:10].upper()

        existentes = {
            self._para_repasse(mapa, linha, numero).id.casefold()
            for numero, linha in linhas
        }
        if novo_id.casefold() in existentes:
            raise RepassesError(f"Já existe um repasse com o ID {novo_id}.")

        linha = [""] * len(mapa.cabecalho)
        mapa.definir(linha, "id", novo_id)
        mapa.definir(linha, "cliente", cliente)

        for campo in CAMPOS_ATUALIZAVEIS - {"cliente"}:
            if campo in dados and dados[campo] is not None and mapa.tem(campo):
                mapa.definir(linha, campo, _formatar_campo(campo, dados[campo]))

        if mapa.tem("status"):
            mapa.definir(
                linha,
                "status",
                normalizar_status(dados.get("status", StatusRepasse.PENDENTE)).value,
            )

        if mapa.tem("atualizado_em"):
            mapa.definir(
                linha,
                "atualizado_em",
                agora_local(self.settings.timezone).strftime("%d/%m/%Y %H:%M"),
            )

        try:
            self.sheets.anexar(
                [linha], referencia=f"A1:{mapa.ultima_coluna}"
            )
        except SheetsError as exc:
            raise RepassesError(str(exc)) from exc

        return self.obter(novo_id) or Repasse(
            id=novo_id,
            cliente=cliente,
            status=normalizar_status(dados.get("status")),
        )

    def atualizar(self, repasse_id: str, campos: dict) -> Repasse:
        """Atualiza células de um repasse existente, preservando as demais colunas."""
        mapa, linhas = self.garantir_planilha()

        alvo_numero: int | None = None
        alvo_linha: list[str] | None = None

        for numero, linha in linhas:
            repasse = self._para_repasse(mapa, linha, numero)
            if repasse.id.casefold() == str(repasse_id).strip().casefold():
                alvo_numero, alvo_linha = numero, linha
                break

        if alvo_numero is None or alvo_linha is None:
            raise RepassesError(f"Repasse {repasse_id} não encontrado.")

        for campo, valor in campos.items():
            if campo not in CAMPOS_ATUALIZAVEIS:
                raise RepassesError(f"Campo não atualizável: {campo}")
            if not mapa.tem(campo):
                raise RepassesError(
                    f"A planilha não possui a coluna do campo '{campo}'."
                )
            mapa.definir(alvo_linha, campo, _formatar_campo(campo, valor))

        if mapa.tem("atualizado_em"):
            mapa.definir(
                alvo_linha,
                "atualizado_em",
                agora_local(self.settings.timezone).strftime("%d/%m/%Y %H:%M"),
            )

        try:
            self.sheets.escrever_valores(
                f"A{alvo_numero}:{mapa.ultima_coluna}{alvo_numero}", [alvo_linha]
            )
        except SheetsError as exc:
            raise RepassesError(str(exc)) from exc

        return self._para_repasse(mapa, alvo_linha, alvo_numero)
