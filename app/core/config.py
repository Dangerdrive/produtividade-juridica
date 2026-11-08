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
    #: Conta padrão (compatível com a configuração de uma única conta).
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_query: str = "in:inbox"
    gmail_lookback_days: int = 60
    gmail_max_mensagens_por_execucao: int = 25
    gmail_marcar_como_lida: bool = True

    #: Contas adicionais: JSON (objeto/lista) ou caminho de arquivo JSON.
    gmail_accounts_json: str = ""
    gmail_accounts_file: str = ""
    #: Nome da conta usada quando nenhuma é informada.
    gmail_conta_padrao: str = ""

    # --- DeepSeek ------------------------------------------------------
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout: float = 60.0
    deepseek_max_tentativas: int = 3
    deepseek_tamanho_maximo_corpo: int = 12_000

    # --- Google (compartilhado entre os módulos) ------------------------
    #: JSON da conta de serviço (cru ou base64). Compartilhe a planilha com
    #: o e-mail da conta de serviço como Editor.
    google_service_account_json: str = ""
    #: Caminho para o arquivo JSON da conta de serviço (alternativa ao conteúdo).
    google_service_account_file: str = ""

    # --- Módulo de prazos (espelho + dashboard em Apps Script) ---------
    google_sheet_id: str = ""
    google_sheet_tab: str = "DB_Prazos"
    sheets_sync_ativo: bool = True

    # --- Módulo de repasses --------------------------------------------
    #: Planilha específica do módulo de repasses.
    repasses_sheet_id: str = ""
    repasses_sheet_tab: str = "Repasses"
    #: Segredo usado para gerar links individuais de acompanhamento por cliente.
    repasses_cliente_token_secret: str = ""
    #: Token exigido nas rotas de escrita da API de repasses.
    #: Se vazio, usa SYNC_TOKEN.
    repasses_api_token: str = ""
    #: Mapeamento opcional das colunas da planilha (JSON: campo -> cabeçalho).
    repasses_colunas_json: str = ""

    # --- Segurança -----------------------------------------------------
    sync_token: str = ""
    app_password: str = ""

    # --- Limites -------------------------------------------------------
    max_segundos_por_execucao: int = 45

    @property
    def gmail_configurado(self) -> bool:
        """Há ao menos uma conta Gmail declarada (padrão ou adicional)."""
        if self.gmail_client_id and self.gmail_client_secret and self.gmail_refresh_token:
            return True
        return bool(self.gmail_accounts_json or self.gmail_accounts_file)

    @property
    def deepseek_configurado(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def google_configurado(self) -> bool:
        """Conta de serviço disponível para Sheets/Docs."""
        return bool(
            self.google_service_account_json or self.google_service_account_file
        )

    @property
    def sheets_configurado(self) -> bool:
        """Planilha do módulo de prazos configurada."""
        return bool(self.google_configurado and self.google_sheet_id)

    @property
    def repasses_configurado(self) -> bool:
        """Planilha do módulo de repasses configurada."""
        return bool(self.google_configurado and self.repasses_sheet_id)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instância única de configuração."""
    return Settings()
