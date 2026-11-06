"""Testes do parsing de mensagens do Gmail."""

from __future__ import annotations

import base64
from datetime import datetime

from app.services.gmail import (
    GmailClient,
    decodificar_header,
    html_para_texto,
    limpar_texto,
    separar_remetente,
)


def _b64(texto: str) -> str:
    return base64.urlsafe_b64encode(texto.encode("utf-8")).decode("ascii").rstrip("=")


class TestSepararRemetente:
    def test_nome_e_email(self):
        assert separar_remetente('"Secretaria TJSP" <Protocolo@TJSP.jus.br>') == (
            "Secretaria TJSP",
            "protocolo@tjsp.jus.br",
        )

    def test_sem_nome(self):
        nome, email = separar_remetente("protocolo@tjsp.jus.br")
        assert email == "protocolo@tjsp.jus.br"
        assert nome == "protocolo"

    def test_vazio(self):
        assert separar_remetente(None) == ("", "")


class TestDecodificarHeader:
    def test_encoded_word(self):
        assert decodificar_header("=?UTF-8?B?SW50aW1hw6fDo28=?=") == "Intimação"

    def test_texto_simples(self):
        assert decodificar_header("Intimação") == "Intimação"

    def test_nulo(self):
        assert decodificar_header(None) == ""


class TestHtmlParaTexto:
    def test_remove_tags(self):
        assert html_para_texto("<p>Olá<br>mundo</p>") .strip() == "Olá\nmundo"

    def test_ignora_script_e_style(self):
        texto = html_para_texto("<style>.a{color:red}</style><p>Visível</p><script>x=1</script>")
        assert "Visível" in texto
        assert "color" not in texto
        assert "x=1" not in texto

    def test_entidades(self):
        assert "Intimação & prazos" in html_para_texto("<p>Intimação &amp; prazos</p>")


class TestLimparTexto:
    def test_colapsa_espacos(self):
        assert limpar_texto("a    b\t\tc") == "a b c"

    def test_limita_linhas_vazias(self):
        assert limpar_texto("a\n\n\n\nb") == "a\n\nb"


class TestConverterMensagem:
    def _payload_multipart(self) -> dict:
        return {
            "id": "18c0ffee",
            "threadId": "thread-1",
            "internalDate": "1762344000000",
            "snippet": "resumo",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "From", "value": '"Secretaria TJSP" <protocolo@tjsp.jus.br>'},
                    {"name": "Subject", "value": "=?UTF-8?B?SW50aW1hw6fDo28=?="},
                ],
                "parts": [
                    {
                        "mimeType": "multipart/alternative",
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": _b64("Prazo de 15 dias.")},
                            },
                            {
                                "mimeType": "text/html",
                                "body": {"data": _b64("<p>Prazo de 15 dias.</p>")},
                            },
                        ],
                    },
                    {
                        "mimeType": "application/pdf",
                        "filename": "anexo.pdf",
                        "body": {"attachmentId": "abc", "size": 10},
                    },
                ],
            },
        }

    def test_multipart_usa_text_plain(self):
        mensagem = GmailClient._converter(self._payload_multipart())

        assert mensagem.id == "18c0ffee"
        assert mensagem.thread_id == "thread-1"
        assert mensagem.remetente_nome == "Secretaria TJSP"
        assert mensagem.remetente_email == "protocolo@tjsp.jus.br"
        assert mensagem.assunto == "Intimação"
        assert mensagem.corpo == "Prazo de 15 dias."
        assert isinstance(mensagem.data_recebimento, datetime)

    def test_internal_date_em_utc(self):
        mensagem = GmailClient._converter(self._payload_multipart())
        # 1762344000 == 2025-11-05T12:00:00Z
        assert mensagem.data_recebimento.year == 2025
        assert mensagem.data_recebimento.hour == 12

    def test_somente_html(self):
        bruto = self._payload_multipart()
        bruto["payload"]["parts"][0]["parts"] = [
            {"mimeType": "text/html", "body": {"data": _b64("<div>Somente HTML</div>")}}
        ]
        mensagem = GmailClient._converter(bruto)
        assert mensagem.corpo == "Somente HTML"

    def test_fallback_para_snippet(self):
        bruto = {
            "id": "1",
            "threadId": "t",
            "internalDate": "1762344000000",
            "snippet": "corpo curto",
            "payload": {"mimeType": "text/plain", "headers": []},
        }
        mensagem = GmailClient._converter(bruto)
        assert mensagem.corpo == "corpo curto"
        assert mensagem.assunto == ""
