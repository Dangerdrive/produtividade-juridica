"""Cliente da API da DeepSeek (formato compatível com a OpenAI)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.modules.prazos.models import StatusExtracao
from app.modules.prazos.services.normalizacao import PrazoExtraido, normalizar_prazos

PROMPT_SISTEMA = """\
Você é um assistente jurídico especializado em analisar e-mails de protocolo,
intimação e publicação de tribunais brasileiros (PJe, e-SAJ, TST, TRT, etc.).

TAREFA: identificar TODOS os prazos processuais mencionados no e-mail.

FORMATO DE SAÍDA — responda SOMENTE com um objeto json válido, sem markdown,
sem comentários, sem texto antes ou depois. Estrutura exata:
{
  "prazos": [
    {
      "advogado": "Nome do advogado responsável",
      "tipo_prazo": "Recurso de Revista / Agravo de Instrumento / Contrarrazões / ...",
      "data": "YYYY-MM-DD",
      "numero_processo": "NNNNNNN-NN.NNNN.N.NN.NNNN"
    }
  ]
}

REGRAS:
1. Se não houver nenhum prazo processual, devolva exatamente {"prazos": []}.
2. "data" é a data-limite (vencimento) do prazo, no formato YYYY-MM-DD.
   Use a data de recebimento do e-mail como referência para converter prazos
   relativos ("prazo de 15 dias", "em 5 dias úteis", "até o dia 12").
   Se não for possível determinar com segurança, use null.
3. "numero_processo" deve seguir o padrão CNJ NNNNNNN-NN.NNNN.N.NN.NNNN.
   Se não existir no texto, use null. NUNCA invente números de processo.
4. "advogado" é o advogado destinatário/responsável indicado no e-mail.
   Se não identificado, use null.
5. "tipo_prazo" é o ato processual (ex.: "Recurso de Revista",
   "Agravo de Instrumento", "Contrarrazões", "Impugnação", "Manifestação").
   Se não identificado, use "Não identificado".
6. Um mesmo e-mail pode conter vários prazos (vários processos ou vários
   advogados): devolva um item por prazo.
7. Não invente dados. Datas e números devem constar do texto do e-mail.
"""

_RE_CERCA_MD = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class DeepSeekError(Exception):
    """Falha genérica de comunicação com a API."""

    status = StatusExtracao.ERRO_IA.value


class RespostaInvalida(DeepSeekError):
    """O modelo respondeu algo que não é o JSON esperado."""

    status = StatusExtracao.ERRO_JSON.value


@dataclass(slots=True)
class ResultadoExtracao:
    prazos: list[PrazoExtraido]


def montar_prompt_usuario(
    *,
    assunto: str,
    remetente_nome: str,
    remetente_email: str,
    data_recebimento: str,
    corpo: str,
    limite_corpo: int = 12_000,
    processos_conhecidos: list[str] | None = None,
) -> str:
    """Monta a mensagem ``user`` enviada ao modelo."""
    if len(corpo) > limite_corpo:
        corpo = corpo[:limite_corpo] + "\n[... texto truncado ...]"

    linhas = [
        f"Data de recebimento do e-mail: {data_recebimento}",
        f"Remetente: {remetente_nome} <{remetente_email}>",
        f"Assunto: {assunto}",
    ]

    if processos_conhecidos:
        linhas.append(
            "Números de processo detectados automaticamente no assunto "
            "(referência — não invente outros): " + ", ".join(processos_conhecidos)
        )

    linhas.extend(
        [
            "",
            "--- INÍCIO DO CORPO DO E-MAIL ---",
            corpo,
            "--- FIM DO CORPO DO E-MAIL ---",
        ]
    )

    return "\n".join(linhas)


def parse_json_tolerante(conteudo: str) -> dict:
    """Faz o parse do JSON do modelo tolerando cercas de markdown e ruído."""
    texto = _RE_CERCA_MD.sub("", str(conteudo or "")).strip()

    inicio = texto.find("{")
    fim = texto.rfind("}")
    if inicio == -1 or fim == -1 or fim < inicio:
        raise RespostaInvalida("Conteúdo sem objeto JSON.")

    try:
        return json.loads(texto[inicio : fim + 1])
    except json.JSONDecodeError as exc:
        raise RespostaInvalida(f"JSON inválido: {exc}") from exc


class DeepSeekClient:
    """Cliente HTTP mínimo para ``/chat/completions``."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep=time.sleep,
    ) -> None:
        self.settings = settings
        self._client = client or httpx.Client(timeout=settings.deepseek_timeout)
        self._sleep = sleep

    # ------------------------------------------------------------------
    def extrair_prazos(
        self,
        *,
        assunto: str,
        remetente_nome: str,
        remetente_email: str,
        data_recebimento: str,
        corpo: str,
        processos_conhecidos: list[str] | None = None,
    ) -> list[PrazoExtraido]:
        """Envia o e-mail ao modelo e devolve os prazos normalizados."""
        if not self.settings.deepseek_api_key:
            raise DeepSeekError("DEEPSEEK_API_KEY não configurada.")

        prompt = montar_prompt_usuario(
            assunto=assunto,
            remetente_nome=remetente_nome,
            remetente_email=remetente_email,
            data_recebimento=data_recebimento,
            corpo=corpo,
            limite_corpo=self.settings.deepseek_tamanho_maximo_corpo,
            processos_conhecidos=processos_conhecidos,
        )

        conteudo = self._chamar(prompt)
        return normalizar_prazos(parse_json_tolerante(conteudo))

    # ------------------------------------------------------------------
    def _chamar(self, prompt_usuario: str) -> str:
        payload = {
            "model": self.settings.deepseek_model,
            "messages": [
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": prompt_usuario},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 2000,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.settings.deepseek_base_url.rstrip('/')}/v1/chat/completions"

        ultimo_erro: Exception | None = None

        for tentativa in range(1, self.settings.deepseek_max_tentativas + 1):
            try:
                resposta = self._client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                ultimo_erro = DeepSeekError(f"Falha de rede: {exc}")
            else:
                if resposta.status_code == 200:
                    return self._extrair_conteudo(resposta)

                fragmento = resposta.text[:300]
                ultimo_erro = DeepSeekError(
                    f"HTTP {resposta.status_code}: {fragmento}"
                )
                # 4xx (exceto 429) são definitivos: não vale repetir.
                if resposta.status_code != 429 and resposta.status_code < 500:
                    raise ultimo_erro

            if tentativa < self.settings.deepseek_max_tentativas:
                self._sleep(min(2 ** (tentativa - 1) * 2, 8))

        raise ultimo_erro or DeepSeekError("Falha desconhecida na chamada à DeepSeek.")

    @staticmethod
    def _extrair_conteudo(resposta: httpx.Response) -> str:
        try:
            dados = resposta.json()
        except ValueError as exc:
            raise DeepSeekError("Resposta HTTP não é JSON.") from exc

        escolhas = dados.get("choices") or []
        conteudo = (escolhas[0] or {}).get("message", {}).get("content") if escolhas else None

        if not isinstance(conteudo, str):
            raise DeepSeekError("Estrutura de resposta inesperada da DeepSeek.")

        return conteudo
