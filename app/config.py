"""Configuração da aplicação (variáveis de ambiente)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração carregada de variáveis de ambiente / arquivo .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "Produtividade Jurídica"
    timezone: str = "America/Sao_Paulo"

    database_url: str = "sqlite:///./data/prazos.db"

    # --- Gmail ---------------------------------------------------------
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_query: str = "in:inbox"
    gmail_lookback_days: int = 60
    gmail_max_mensagens_por_execucao: int = 25
    gmail_marcar_como_lida: bool = True

    # --- DeepSeek ------------------------------------------------------
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout: float = 60.0
    deepseek_max_tentativas: int = 3
    deepseek_tamanho_maximo_corpo: int = 12_000

    # --- Google Sheets (espelhamento + dashboard em Apps Script) --------
    #: JSON da conta de serviço (cru ou base64). Compartilhe a planilha com
    #: o e-mail da conta de serviço como Editor.
    google_service_account_json: str = ""
    google_sheet_id: str = ""
    google_sheet_tab: str = "DB_Prazos"
    sheets_sync_ativo: bool = True

    # --- Segurança -----------------------------------------------------
    sync_token: str = ""
    app_password: str = ""

    # --- Limites -------------------------------------------------------
    max_segundos_por_execucao: int = 45

    @property
    def gmail_configurado(self) -> bool:
        return bool(
            self.gmail_client_id and self.gmail_client_secret and self.gmail_refresh_token
        )

    @property
    def deepseek_configurado(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def sheets_configurado(self) -> bool:
        return bool(self.google_service_account_json and self.google_sheet_id)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instância única de configuração."""
    return Settings()
