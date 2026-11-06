/**
 * Config.gs
 * -----------------------------------------------------------------------------
 * Configuração central do projeto de automação de prazos jurídicos.
 *
 * IMPORTANTE: este é o ÚNICO arquivo com constantes inicializadas no escopo
 * global. O Apps Script concatena todos os arquivos .gs e avalia as constantes
 * de topo sem garantir a ordem entre arquivos — manter tudo aqui evita
 * "ReferenceError" intermitente.
 */

/**
 * Mapeamento semântico das colunas da aba DB_Prazos.
 * O valor é o índice 1-based da coluna na planilha.
 *
 * Bloco de ingestão (sem IA): 1..6
 * Bloco de enriquecimento (IA): 7..12  -> atualizado com um único setValues()
 */
const COL = Object.freeze({
  ID: 1,
  THREAD: 2,
  RECEBIDO: 3,
  REM_NOME: 4,
  REM_EMAIL: 5,
  ASSUNTO: 6,
  ADVOGADO: 7,
  TIPO: 8,
  DATA: 9,
  PROCESSO: 10,
  STATUS: 11,
  PROCESSADO: 12
});

/** Nome exibido de cada coluna (chave = índice, garantindo alinhamento com COL). */
const CABECALHOS = Object.freeze({
  [COL.ID]: 'Message ID',
  [COL.THREAD]: 'Thread ID',
  [COL.RECEBIDO]: 'Data Recebimento (E-mail)',
  [COL.REM_NOME]: 'Remetente (Nome)',
  [COL.REM_EMAIL]: 'Remetente (E-mail)',
  [COL.ASSUNTO]: 'Assunto',
  [COL.ADVOGADO]: 'Advogado',
  [COL.TIPO]: 'Tipo de Prazo',
  [COL.DATA]: 'Data do Prazo',
  [COL.PROCESSO]: 'Número do Processo',
  [COL.STATUS]: 'Status da Extração',
  [COL.PROCESSADO]: 'Processado Em'
});

const TOTAL_COLUNAS = Object.keys(CABECALHOS).length;

/** Retorna a linha de cabeçalho na ordem correta (A..L). */
function linhaCabecalho_() {
  return Object.keys(CABECALHOS)
    .map(Number)
    .sort(function (a, b) { return a - b; })
    .map(function (indice) { return CABECALHOS[indice]; });
}

/** Valores possíveis da coluna "Status da Extração". */
const STATUS = Object.freeze({
  PENDENTE_IA: 'PENDENTE_IA',
  OK: 'OK',
  REVISAR: 'REVISAR',
  SEM_PRAZO: 'SEM_PRAZO',
  ERRO_IA: 'ERRO_IA',
  ERRO_JSON: 'ERRO_JSON'
});

/** Status que permitem reprocessar a linha existente em vez de criar outra. */
const STATUS_REPROCESSAVEIS = Object.freeze([
  STATUS.PENDENTE_IA,
  STATUS.ERRO_IA,
  STATUS.ERRO_JSON
]);

/** Configuração geral. */
const CONFIG = Object.freeze({
  FUSO: 'America/Sao_Paulo',

  ABAS: Object.freeze({
    DB: 'DB_Prazos',
    DASHBOARD: 'Dashboard'
  }),

  /** Nomes das chaves de Script Properties (NUNCA coloque segredos no código). */
  PROPS: Object.freeze({
    DEEPSEEK_API_KEY: 'DEEPSEEK_API_KEY',
    SPREADSHEET_ID: 'SPREADSHEET_ID',
    ANCORA_HISTORICO: 'HISTORICO_ANCORA',
    OFFSET_HISTORICO: 'HISTORICO_OFFSET'
  }),

  DEEPSEEK: Object.freeze({
    ENDPOINT: 'https://api.deepseek.com/v1/chat/completions',
    MODEL: 'deepseek-chat',
    TEMPERATURE: 0,
    MAX_TOKENS: 2000,
    TIMEOUT_MS: 60000,
    MAX_TENTATIVAS: 3,
    BACKOFF_BASE_MS: 2000,
    TAMANHO_MAXIMO_CORPO: 12000
  }),

  EMAIL: Object.freeze({
    /** Rotina contínua: apenas não lidas da caixa de entrada. */
    QUERY_CONTINUO: 'label:inbox is:unread',
    /** Base para a busca histórica; recebe sufixo `after:YYYY/MM/DD`. */
    QUERY_BASE: 'label:inbox',
    /** Janela da carga inicial. */
    DIAS_HISTORICO: 60,
    /** Threads por página de busca. */
    TAMANHO_PAGINA_CONTINUO: 50,
    TAMANHO_PAGINA_HISTORICO: 50,
    /** Interrompe a execução antes do limite de ~6 min do Apps Script. */
    TEMPO_MAXIMO_EXECUCAO_MS: 4 * 60 * 1000
  })
});
