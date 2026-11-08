"""Registro dos módulos da plataforma.

Cada módulo é um pacote em ``app/modules/<chave>`` com seus próprios modelos,
serviços, rotas e templates. Este registro é usado pela página inicial e pela
navegação compartilhada.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class ModuloInfo:
    """Metadados de um módulo exibidos na interface."""

    chave: str
    nome: str
    descricao: str
    rota: str
    configurado: bool
    icone: str = ""


def listar_modulos(settings: Settings) -> list[ModuloInfo]:
    """Lista os módulos disponíveis com o estado de configuração atual."""
    return [
        ModuloInfo(
            chave="prazos",
            nome="Prazos",
            descricao=(
                "Leitura de e-mails de protocolo, extração de prazos com IA e "
                "dashboard de produtividade."
            ),
            rota="/prazos",
            configurado=(
                settings.gmail_configurado and settings.deepseek_configurado
            ),
            icone="⚖️",
        ),
        ModuloInfo(
            chave="repasses",
            nome="Repasses",
            descricao=(
                "Controle de repasses em planilha própria, tarefas pendentes e "
                "acompanhamento de status por cliente."
            ),
            rota="/repasses",
            configurado=settings.repasses_configurado,
            icone="💸",
        ),
        ModuloInfo(
            chave="google",
            nome="Integração Google",
            descricao=(
                "Camada única de acesso ao Gmail, Google Sheets e Google Docs, "
                "reutilizada pelos demais módulos."
            ),
            rota="/google",
            configurado=(
                settings.google_configurado or settings.gmail_configurado
            ),
            icone="🔗",
        ),
    ]
