/**
 * Extração Automática de Prazos Jurídicos via IA (Google Gemini 3.8 Flash)
 * Arquitetura Fase 1 - MVP em Apps Script
 */

// ============================================================
// 1. ROTINA DE EXECUÇÃO CONTÍNUA (Novos e-mails)
// ============================================================
function processarEmailsProtocolo() {
  const props = PropertiesService.getScriptProperties();
  const apiKey = props.getProperty('OPENAI_API_KEY');
  const query = props.getProperty('EMAIL_QUERY') || 'label:inbox is:unread';

  if (!apiKey) {
    console.error("ERRO: Chave da API (GEMINI / OPENAI) não encontrada nas Propriedades do Script.");
    return;
  }

  const sheet = getOrCreateDbSheet();
  executarBuscaEProcessamento(query, apiKey, sheet, true);
}

// ============================================================
// 2. ROTINA HISTÓRICA (Últimos 2 Meses) - Executar apenas 1 vez
// ============================================================
function processarHistorico2Meses() {
  const props = PropertiesService.getScriptProperties();
  const apiKey = props.getProperty('OPENAI_API_KEY');

  if (!apiKey) {
    console.error("ERRO: Chave da API (GEMINI / OPENAI) não encontrada nas Propriedades do Script.");
    return;
  }

  const dataLimite = new Date();
  dataLimite.setDate(dataLimite.getDate() - 60);
  const ano = dataLimite.getFullYear();
  const mes = String(dataLimite.getMonth() + 1).padStart(2, '0');
  const dia = String(dataLimite.getDate()).padStart(2, '0');

  const dataFormatada = `${ano}/${mes}/${dia}`;
  const queryHistorica = `label:inbox after:${dataFormatada}`;
  console.log(`Iniciando varredura histórica com a query: "${queryHistorica}"`);

  const sheet = getOrCreateDbSheet();
  executarBuscaEProcessamento(queryHistorica, apiKey, sheet, false);
}

// ============================================================
// 3. MOTOR DE PROCESSAMENTO E LEITURA DE E-MAILS
// ============================================================
function executarBuscaEProcessamento(query, apiKey, sheet, apenasNaoLidos) {
  const threads = GmailApp.search(query);
  if (threads.length === 0) {
    console.log("Nenhum e-mail encontrado para a query especificada.");
    return;
  }

  const idsExistentes = getMessageIdsExistentes(sheet);

  for (let i = 0; i < threads.length; i++) {
    const messages = threads[i].getMessages();

    for (let j = 0; j < messages.length; j++) {
      const msg = messages[j];
      const messageId = msg.getId();

      if (apenasNaoLidos && !msg.isUnread()) continue;

      if (idsExistentes.has(messageId)) {
        console.log(`Mensagem ${messageId} já processada anteriormente. Pulando...`);
        continue;
      }

      const emailDate = msg.getDate();
      const plainTextBody = msg.getPlainBody();

      try {
        console.log(`Processando e-mail ID: ${messageId} | Data: ${emailDate}`);

        const prazosExtraidos = extrairDadosComIA(plainTextBody, apiKey);

        if (prazosExtraidos && prazosExtraidos.prazos && prazosExtraidos.prazos.length > 0) {
          prazosExtraidos.prazos.forEach(prazo => {
            sheet.appendRow([
              messageId,
              emailDate,
              prazo.advogado,
              prazo.tipo_prazo,
              prazo.data,
              prazo.numero_processo
            ]);
          });
          console.log(`-> ${prazosExtraidos.prazos.length} prazo(s) extraído(s) com sucesso.`);
        } else {
          console.log("-> Nenhum prazo identificado no corpo deste e-mail.");
        }

        if (apenasNaoLidos) {
          msg.markRead();
        }

        idsExistentes.add(messageId);

      } catch (error) {
        console.error(`Falha ao processar mensagem ${messageId}: ${error.message}`);
      }
    }
  }
}

// ============================================================
// 4. CHAMADA À API DO GOOGLE GEMINI (Modelo gemini-3.8-flash)
// ============================================================
function extrairDadosComIA(textoEmail, apiKey) {
  // Endpoint atualizado para gemini-3.8-flash
  const url = `https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key=${apiKey}`;

  const payload = {
    contents: [
      {
        role: "user",
        parts: [
          {
            text: "Você é um assistente jurídico de extração de dados. Leia o e-mail e extraia todos os prazos e processos mencionados. Se o e-mail não mantiver relação com prazos jurídicos, retorne a lista de prazos vazia.\n\nConteúdo do e-mail:\n" + textoEmail
          }
        ]
      }
    ],
    generationConfig: {
      responseMimeType: "application/json",
      responseSchema: {
        type: "OBJECT",
        properties: {
          prazos: {
            type: "ARRAY",
            items: {
              type: "OBJECT",
              properties: {
                advogado: { type: "STRING" },
                tipo_prazo: { type: "STRING" },
                data: { type: "STRING", description: "Formato YYYY-MM-DD" },
                numero_processo: { type: "STRING" }
              },
              required: ["advogado", "tipo_prazo", "data", "numero_processo"]
            }
          }
        },
        required: ["prazos"]
      }
    }
  };

  const options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  const response = UrlFetchApp.fetch(url, options);
  const jsonResponse = JSON.parse(response.getContentText());

  if (response.getResponseCode() !== 200) {
    const errorMsg = jsonResponse.error ? jsonResponse.error.message : response.getContentText();
    throw new Error(`Erro na API do Gemini: ${errorMsg}`);
  }

  const textOutput = jsonResponse.candidates[0].content.parts[0].text;
  return JSON.parse(textOutput);
}

// ============================================================
// 5. AUXILIARES DE PLANILHA E HISTÓRICO DE IDs
// ============================================================
function getMessageIdsExistentes(sheet) {
  const lastRow = sheet.getLastRow();
  const setIds = new Set();

  if (lastRow > 1) {
    const data = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
    for (let i = 0; i < data.length; i++) {
      if (data[i][0]) {
        setIds.add(String(data[i][0]));
      }
    }
  }
  return setIds;
}

function getOrCreateDbSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  let dbSheet = ss.getSheetByName("DB_Prazos");
  if (!dbSheet) {
    dbSheet = ss.insertSheet("DB_Prazos");
    dbSheet.appendRow([
      "Message ID",
      "Data Recebimento (E-mail)",
      "Advogado",
      "Tipo de Prazo",
      "Data do Prazo",
      "Número do Processo"
    ]);
    dbSheet.getRange("A1:F1").setFontWeight("bold").setBackground("#4A607A").setFontColor("#FFFFFF");
    dbSheet.setFrozenRows(1);
  }

  let dashSheet = ss.getSheetByName("Dashboard");
  if (!dashSheet) {
    dashSheet = ss.insertSheet("Dashboard");
    dashSheet.getRange("A1").setValue("Controle Semanal/Mensal de Produtividade").setFontSize(14).setFontWeight("bold");

    const sourceRange = dbSheet.getRange("A1:F1000");
    const pivotTable = dashSheet.getRange("A3").createPivotTable(sourceRange);

    pivotTable.addRowGroup(3);    // Advogado
    pivotTable.addColumnGroup(4); // Tipo de Prazo
    pivotTable.addPivotValue(6, SpreadsheetApp.PivotTableSummarizeFunction.COUNTA); // Processos

    const criteria = SpreadsheetApp.newFilterCriteria().build();
    pivotTable.addFilter(5, criteria);
  }

  return dbSheet;
}
