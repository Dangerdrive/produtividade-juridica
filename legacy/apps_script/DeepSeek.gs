/**
 * DeepSeek.gs
 * -----------------------------------------------------------------------------
 * Cliente da API da DeepSeek (compatível com o formato OpenAI) + montagem do
 * prompt + parsing e normalização da resposta.
 *
 * Requisitos:
 *   - Script Property `DEEPSEEK_API_KEY` configurada.
 *   - response_format: { type: "json_object" } (a palavra "json" deve constar
 *     do prompt; está garantida em PROMPT_SISTEMA).
 */

const PROMPT_SISTEMA = [
  'Você é um assistente jurídico especializado em analisar e-mails de protocolo,',
  'intimação e publicação de tribunais brasileiros (PJe, e-SAJ, TST, TRT, etc.).',
  '',
  'TAREFA: identificar TODOS os prazos processuais mencionados no e-mail.',
  '',
  'FORMATO DE SAÍDA — responda SOMENTE com um objeto json válido, sem markdown,',
  'sem comentários, sem texto antes ou depois. Estrutura exata:',
  '{',
  '  "prazos": [',
  '    {',
  '      "advogado": "Nome do advogado responsável",',
  '      "tipo_prazo": "Recurso de Revista / Agravo de Instrumento / Contrarrazões / Manifestação / ...",',
  '      "data": "YYYY-MM-DD",',
  '      "numero_processo": "NNNNNNN-NN.NNNN.N.NN.NNNN"',
  '    }',
  '  ]',
  '}',
  '',
  'REGRAS:',
  '1. Se não houver nenhum prazo processual, devolva exatamente {"prazos": []}.',
  '2. "data" é a data-limite (vencimento) do prazo, no formato YYYY-MM-DD.',
  '   Use a data de recebimento do e-mail como referência para converter prazos',
  '   relativos ("prazo de 15 dias", "em 5 dias úteis", "até o dia 12").',
  '   Se não for possível determinar com segurança, use null.',
  '3. "numero_processo" deve seguir o padrão CNJ NNNNNNN-NN.NNNN.N.NN.NNNN.',
  '   Se não existir no texto, use null. NUNCA invente números de processo.',
  '4. "advogado" é o advogado destinatário/responsável indicado no e-mail.',
  '   Se não identificado, use null.',
  '5. "tipo_prazo" é o ato processual (ex.: "Recurso de Revista",',
  '   "Agravo de Instrumento", "Contrarrazões", "Impugnação", "Manifestação").',
  '   Se não identificado, use "Não identificado".',
  '6. Um mesmo e-mail pode conter vários prazos (vários processos ou vários',
  '   advogados): devolva um item por prazo.',
  '7. Não invente dados. Datas e números devem constar do texto do e-mail.'
].join('\n');

/**
 * Monta a mensagem do usuário enviada ao modelo.
 * @param {Object} base Dados determinísticos do e-mail.
 * @return {string}
 */
function montarPromptUsuario_(base) {
  const linhas = [
    'Data de recebimento do e-mail: ' +
      Utilities.formatDate(base.dataRecebimento, CONFIG.FUSO, 'dd/MM/yyyy HH:mm'),
    'Remetente: ' + base.remetenteNome + ' <' + base.remetenteEmail + '>',
    'Assunto: ' + base.assunto,
    '',
    '--- INÍCIO DO CORPO DO E-MAIL ---',
    truncarTexto_(base.corpo, CONFIG.DEEPSEEK.TAMANHO_MAXIMO_CORPO),
    '--- FIM DO CORPO DO E-MAIL ---'
  ];

  return linhas.join('\n');
}

/**
 * Trunca texto preservando o início (onde costuma estar o teor do ato).
 * @param {string} texto
 * @param {number} limite
 * @return {string}
 */
function truncarTexto_(texto, limite) {
  const valor = String(texto || '');
  return valor.length <= limite ? valor : valor.slice(0, limite) + '\n[... texto truncado ...]';
}

/**
 * Cria um Error com um código de status associado (usado na máquina de estados).
 * @param {string} mensagem
 * @param {string} codigo
 * @return {Error}
 */
function erroComCodigo_(mensagem, codigo) {
  const erro = new Error(mensagem);
  erro.codigo = codigo;
  return erro;
}

/**
 * Executa a chamada HTTP à DeepSeek com retry exponencial.
 * @param {string} promptUsuario
 * @return {string} Conteúdo textual devolvido pelo modelo.
 */
function chamarDeepSeek_(promptUsuario) {
  const apiKey = PropertiesService.getScriptProperties()
    .getProperty(CONFIG.PROPS.DEEPSEEK_API_KEY);

  if (!apiKey) {
    throw erroComCodigo_(
      'Propriedade de script ' + CONFIG.PROPS.DEEPSEEK_API_KEY + ' não configurada. ' +
      'Veja o README (Configurar a chave da API).',
      STATUS.ERRO_IA
    );
  }

  const payload = {
    model: CONFIG.DEEPSEEK.MODEL,
    messages: [
      { role: 'system', content: PROMPT_SISTEMA },
      { role: 'user', content: promptUsuario }
    ],
    response_format: { type: 'json_object' },
    temperature: CONFIG.DEEPSEEK.TEMPERATURE,
    max_tokens: CONFIG.DEEPSEEK.MAX_TOKENS,
    stream: false
  };

  const opcoes = {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + apiKey },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  let ultimoErro = null;

  for (let tentativa = 1; tentativa <= CONFIG.DEEPSEEK.MAX_TENTATIVAS; tentativa++) {
    let resposta = null;

    try {
      resposta = UrlFetchApp.fetch(CONFIG.DEEPSEEK.ENDPOINT, opcoes);
    } catch (e) {
      ultimoErro = erroComCodigo_('Falha de rede: ' + e.message, STATUS.ERRO_IA);
    }

    if (resposta) {
      const codigo = resposta.getResponseCode();
      const texto = resposta.getContentText();

      if (codigo === 200) {
        return extrairConteudoResposta_(texto);
      }

      ultimoErro = erroComCodigo_('HTTP ' + codigo + ': ' + texto.slice(0, 300), STATUS.ERRO_IA);

      // Erros definitivos (auth, payload, modelo inexistente) não devem ser repetidos.
      if (codigo !== 429 && codigo < 500) {
        throw ultimoErro;
      }
    }

    if (tentativa < CONFIG.DEEPSEEK.MAX_TENTATIVAS) {
      Utilities.sleep(CONFIG.DEEPSEEK.BACKOFF_BASE_MS * Math.pow(2, tentativa - 1));
    }
  }

  throw ultimoErro || erroComCodigo_('Falha desconhecida na chamada à DeepSeek.', STATUS.ERRO_IA);
}

/**
 * Extrai o texto do campo choices[0].message.content.
 * @param {string} textoResposta
 * @return {string}
 */
function extrairConteudoResposta_(textoResposta) {
  let json;

  try {
    json = JSON.parse(textoResposta);
  } catch (e) {
    throw erroComCodigo_('Resposta HTTP não é JSON: ' + textoResposta.slice(0, 300), STATUS.ERRO_IA);
  }

  const escolha = json.choices && json.choices[0];

  if (!escolha || !escolha.message || typeof escolha.message.content !== 'string') {
    throw erroComCodigo_(
      'Estrutura de resposta inesperada: ' + textoResposta.slice(0, 300),
      STATUS.ERRO_IA
    );
  }

  return escolha.message.content;
}

/**
 * Faz o parse do JSON devolvido pelo modelo, tolerando cercas de markdown
 * e texto residual ao redor do objeto.
 * @param {string} conteudo
 * @return {Object}
 */
function parseJsonTolerante_(conteudo) {
  let texto = String(conteudo || '').trim();

  texto = texto.replace(/^```[a-zA-Z]*\s*/, '').replace(/```\s*$/, '').trim();

  const inicio = texto.indexOf('{');
  const fim = texto.lastIndexOf('}');

  if (inicio === -1 || fim === -1 || fim < inicio) {
    throw erroComCodigo_('Conteúdo sem objeto JSON: ' + texto.slice(0, 300), STATUS.ERRO_JSON);
  }

  try {
    return JSON.parse(texto.slice(inicio, fim + 1));
  } catch (e) {
    throw erroComCodigo_('JSON inválido: ' + e.message, STATUS.ERRO_JSON);
  }
}

/**
 * Normaliza um texto vindo do modelo, tratando "lixo" comum.
 * @param {*} valor
 * @return {string}
 */
function textoLimpo_(valor) {
  if (valor === null || valor === undefined) {
    return '';
  }

  const texto = String(valor).trim();

  if (!texto || /^(null|undefined|n\/a|na|-|—)$/i.test(texto)) {
    return '';
  }

  return texto;
}

/**
 * Converte a data devolvida pelo modelo em Date (meia-noite local).
 * Aceita YYYY-MM-DD, DD/MM/YYYY, YYYY/MM/DD e DD-MM-YYYY.
 * @param {*} valor
 * @return {{valor: (Date|string), valido: boolean}} Se não interpretável,
 *         `valor` mantém o texto cru para triagem manual.
 */
function normalizarData_(valor) {
  const texto = textoLimpo_(valor);

  if (!texto) {
    return { valor: '', valido: false };
  }

  let ano;
  let mes;
  let dia;
  const iso = texto.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/);
  const br = texto.match(/^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$/);

  if (iso) {
    ano = Number(iso[1]);
    mes = Number(iso[2]);
    dia = Number(iso[3]);
  } else if (br) {
    dia = Number(br[1]);
    mes = Number(br[2]);
    ano = Number(br[3]);
  } else {
    return { valor: texto, valido: false };
  }

  if (ano < 1990 || ano > 2100 || mes < 1 || mes > 12 || dia < 1 || dia > 31) {
    return { valor: texto, valido: false };
  }

  const data = new Date(ano, mes - 1, dia);

  // Rejeita datas "transbordadas" (ex.: 31/02/2026 -> 03/03/2026).
  if (data.getFullYear() !== ano || data.getMonth() !== mes - 1 || data.getDate() !== dia) {
    return { valor: texto, valido: false };
  }

  return { valor: data, valido: true };
}

/**
 * Normaliza o número do processo no padrão CNJ.
 * @param {*} valor
 * @return {{valor: string, valido: boolean}}
 */
function normalizarProcesso_(valor) {
  const texto = textoLimpo_(valor);

  if (!texto) {
    return { valor: '', valido: false };
  }

  const normalizado = texto.replace(/\s+/g, '');
  const padraoCnj = /^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$/;

  return { valor: normalizado, valido: padraoCnj.test(normalizado) };
}

/**
 * Converte um item bruto do modelo em prazo normalizado.
 * @param {*} bruto
 * @return {Object|null}
 */
function normalizarPrazo_(bruto) {
  if (!bruto || typeof bruto !== 'object') {
    return null;
  }

  const advogado = textoLimpo_(bruto.advogado);
  const tipo = textoLimpo_(bruto.tipo_prazo) || 'Não identificado';
  const data = normalizarData_(bruto.data);
  const processo = normalizarProcesso_(bruto.numero_processo);

  const completo = Boolean(advogado && data.valido && processo.valido);

  return {
    advogado: advogado,
    tipo_prazo: tipo,
    data: data.valor,
    dataValida: data.valido,
    numero_processo: processo.valor,
    processoValido: processo.valido,
    status: completo ? STATUS.OK : STATUS.REVISAR
  };
}

/**
 * Valida/normaliza a lista de prazos devolvida pelo modelo e remove duplicados.
 * @param {Object} objetoResposta
 * @return {Object[]}
 */
function normalizarPrazos_(objetoResposta) {
  if (!objetoResposta || typeof objetoResposta !== 'object' ||
      !Array.isArray(objetoResposta.prazos)) {
    throw erroComCodigo_(
      'Resposta sem o array "prazos".',
      STATUS.ERRO_JSON
    );
  }

  const vistos = {};
  const prazos = [];

  for (let i = 0; i < objetoResposta.prazos.length; i++) {
    const prazo = normalizarPrazo_(objetoResposta.prazos[i]);

    if (!prazo) {
      continue;
    }

    const dataChave = prazo.data instanceof Date
      ? Utilities.formatDate(prazo.data, CONFIG.FUSO, 'yyyy-MM-dd')
      : String(prazo.data || '');

    const chave = [
      prazo.advogado.toLowerCase(),
      prazo.tipo_prazo.toLowerCase(),
      dataChave,
      prazo.numero_processo
    ].join('|');

    if (vistos[chave]) {
      continue;
    }

    vistos[chave] = true;
    prazos.push(prazo);
  }

  return prazos;
}

/**
 * Fluxo completo de extração: monta prompt -> chama API -> valida -> normaliza.
 * @param {Object} base Dados determinísticos do e-mail.
 * @return {Object[]} Lista normalizada de prazos (pode ser vazia).
 */
function extrairPrazosDoEmail_(base) {
  const conteudo = chamarDeepSeek_(montarPromptUsuario_(base));
  return normalizarPrazos_(parseJsonTolerante_(conteudo));
}
