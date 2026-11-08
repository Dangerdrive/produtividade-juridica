"""Configuração de uma ou mais contas Gmail.

A conta padrão continua sendo definida por ``GMAIL_CLIENT_ID``,
``GMAIL_CLIENT_SECRET`` e ``GMAIL_REFRESH_TOKEN``. Contas adicionais podem ser
declaradas em ``GMAIL_ACCOUNTS_JSON`` ou em um arquivo apontado por
``GMAIL_ACCOUNTS_FILE``:

    {
      "principal": {
        "client_id": "...",
        "client_secret": "...",
        "refresh_token": "...",
        "query": "in:inbox",
        "rotulo": "Conta principal"
      },
      "trabalho": {
        "refresh_token": "...",
        "query": "in:inbox"
      }
    }

Se ``client_id``/``client_secret`` forem omitidos em uma conta, são usados os
da conta padrão (útil quando todas as contas usam o mesmo cliente OAuth).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings


class ContasError(Exception):
    """Falha ao carregar/selecionar contas Gmail."""


@dataclass(slots=True, frozen=True)
class GmailConta:
    """Credenciais e preferências de uma conta Gmail."""

    nome: str
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    query: str = "in:inbox"
    rotulo: str = ""

    @property
    def configurada(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    @property
    def descricao(self) -> str:
        return self.rotulo or self.nome


def _texto(valor: object) -> str:
    return str(valor or "").strip()


def _carregar_json(settings: Settings) -> dict | list | None:
    bruto = (settings.gmail_accounts_json or "").strip()
    arquivo = (settings.gmail_accounts_file or "").strip()

    if not bruto and arquivo:
        caminho = Path(arquivo).expanduser()
        try:
            bruto = caminho.read_text(encoding="utf-8")
        except OSError as exc:
            raise ContasError(
                f"Não foi possível ler GMAIL_ACCOUNTS_FILE ({caminho}): {exc}"
            ) from exc

    if not bruto:
        return None

    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError as exc:
        raise ContasError(f"GMAIL_ACCOUNTS_JSON/FILE inválido: {exc}") from exc

    if not isinstance(dados, (dict, list)):
        raise ContasError(
            "GMAIL_ACCOUNTS_JSON/FILE deve ser um objeto (nome -> conta) "
            "ou uma lista de contas."
        )
    return dados


def _conta_de_dict(nome: str, dados: dict, padrao: GmailConta) -> GmailConta:
    return GmailConta(
        nome=nome,
        client_id=_texto(dados.get("client_id")) or padrao.client_id,
        client_secret=_texto(dados.get("client_secret")) or padrao.client_secret,
        refresh_token=_texto(dados.get("refresh_token")),
        query=_texto(dados.get("query")) or padrao.query,
        rotulo=_texto(dados.get("rotulo")),
    )


def carregar_contas(settings: Settings) -> dict[str, GmailConta]:
    """Carrega todas as contas declaradas, incluindo a conta padrão."""
    padrao = GmailConta(
        nome="padrao",
        client_id=_texto(settings.gmail_client_id),
        client_secret=_texto(settings.gmail_client_secret),
        refresh_token=_texto(settings.gmail_refresh_token),
        query=_texto(settings.gmail_query) or "in:inbox",
        rotulo="Conta padrão",
    )

    dados = _carregar_json(settings)
    contas: dict[str, GmailConta] = {}

    if dados is None:
        if padrao.configurada:
            contas[padrao.nome] = padrao
        return contas

    if isinstance(dados, list):
        for indice, item in enumerate(dados, start=1):
            if not isinstance(item, dict):
                continue
            nome = _texto(item.get("nome")) or f"conta-{indice}"
            contas[nome] = _conta_de_dict(nome, item, padrao)
    else:
        for chave, item in dados.items():
            nome = _texto(chave)
            if not nome:
                continue

            if isinstance(item, str):
                # Atalho: "trabalho": "refresh_token"
                contas[nome] = GmailConta(
                    nome=nome,
                    client_id=padrao.client_id,
                    client_secret=padrao.client_secret,
                    refresh_token=item.strip(),
                    query=padrao.query,
                )
            elif isinstance(item, dict):
                contas[nome] = _conta_de_dict(nome, item, padrao)
            else:
                raise ContasError(
                    f"Conta '{nome}' deve ser um objeto ou uma string com o refresh token."
                )

    # Compatibilidade: a conta única das variáveis GMAIL_* também aparece.
    if padrao.configurada and padrao.nome not in contas:
        contas[padrao.nome] = padrao

    return contas


def nome_conta_padrao(
    settings: Settings, contas: dict[str, GmailConta] | None = None
) -> str:
    """Nome da conta usada quando nenhuma é informada."""
    contas = carregar_contas(settings) if contas is None else contas
    escolhida = _texto(settings.gmail_conta_padrao)

    if escolhida and escolhida in contas:
        return escolhida
    if "padrao" in contas:
        return "padrao"
    return next(iter(contas), "")


def obter_conta(settings: Settings, nome: str | None = None) -> GmailConta:
    """Devolve a conta pedida (ou a padrão), validando as credenciais."""
    contas = carregar_contas(settings)

    if not contas:
        raise ContasError(
            "Nenhuma conta Gmail configurada. Preencha GMAIL_CLIENT_ID, "
            "GMAIL_CLIENT_SECRET e GMAIL_REFRESH_TOKEN ou use "
            "GMAIL_ACCOUNTS_FILE/GMAIL_ACCOUNTS_JSON."
        )

    escolhida = _texto(nome) or nome_conta_padrao(settings, contas)
    conta = contas.get(escolhida)

    if conta is None:
        disponiveis = ", ".join(sorted(contas)) or "(nenhuma)"
        raise ContasError(
            f"Conta Gmail '{escolhida}' não encontrada. Disponíveis: {disponiveis}."
        )

    if not conta.configurada:
        raise ContasError(
            f"Conta Gmail '{conta.nome}' incompleta: faltam client_id, "
            "client_secret ou refresh_token."
        )

    return conta
