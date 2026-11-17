# -*- coding: utf-8 -*-
"""
comunicacao_mensagens.py — backend da "Central de Mensagens" (Fase 1)
─────────────────────────────────────────────────────────────────────────
Gera mensagens para cliente / técnico / e-mail / texto de envio de relatório
seguindo docs/PADRAO_COMUNICACAO_RELATORIOS_GRID.md (repo PAINELDEFALHAS),
que é a fonte de verdade do padrão de comunicação da Grid Co.

- System prompt: texto da seção 11.5, SEM alteração (SYSTEM_PROMPT_11_5).
- Contexto do usuário: o bloco descrito logo abaixo da 11.5 (TIPO /
  DESTINATÁRIO(S) / UFV(s) / DATA/HORA DE ENVIO / RASCUNHO / AJUSTE), mais as
  decisões do Fred de 17/11/2026 que valem só para esta Central:
    • ele escreve a saudação → a IA começa direto no corpo;
    • e-mail: só assunto + texto (sem endereços);
    • não anunciar cordialidade ("de forma cordial" etc.);
    • Sal Energia fora de tudo.
- Saída: JSON {introducao, mensagens[], observacoes[]}.
- IA: Gemini, pela mesma função do app.py (_chamar_gemini_com_retry, com a
  chave reserva no 429). A chave fica só no .env da VM1 (GEMINI_API_KEY).
- Uma chamada por clique (Gerar / Ajustar / Dividir) — nada automático, para
  não consumir a cota gratuita.

Registro no app.py (2 linhas, perto do fim):
    from comunicacao_mensagens import registrar as _cm_registrar
    _cm_registrar(app, _chamar_gemini_com_retry, log)
"""
import json
import re

from flask import jsonify, request

# ── seção 11.5 do padrão — copiar e usar (não editar este texto) ─────────────
SYSTEM_PROMPT_11_5 = """Você é o redator de comunicação operacional da Grid Co. (O&M de usinas fotovoltaicas). Escreve em português do Brasil, em nome de Fred Alexandrino, Supervisor de O&M.

SAÍDA: responda SOMENTE em JSON no formato:
{"introducao": "<opcional, curta>", "mensagens": [{"titulo": "<opcional>", "assunto": "<só e-mail>", "texto": "<texto pronto para envio>"}], "observacoes": ["<inconsistências reais nos dados, se houver>"]}

REGRAS GERAIS
- O campo "texto" contém apenas o que será enviado: sem "Aqui está", "Segue", "Espero ter ajudado".
- Comunicados longos ao cliente: dividir em mensagens (diagnóstico | opções/encaminhamento).
- Nunca invente datas, nomes, causas ou culpados. Faltou dado essencial: use "a confirmar" e registre em "observacoes".
- Elimine bastidores internos, desabafos e ruídos interpessoais. Foque em: diagnóstico técnico, impacto/risco, ações da equipe, próximos passos.
- Usinas sempre como "UFV <Nome>", em *negrito* no WhatsApp.
- WhatsApp: *negrito*, _itálico_, bullets "•", listas numeradas para ações. Sem Markdown de títulos, tabelas ou links. Emojis só 🔴🟠🟡✅⚠️ com função.

CLIENTE
- Saudação: "Olá, <Nome>. Tudo bem?" / "Bom dia|Boa tarde, <Nome>. Tudo bem?" / "Prezados, bom dia." (coletivo).
- Estrutura: saudação → diagnóstico (equipamento + causa-raiz confirmada) → opções com riscos (Opção A/B) → alinhamento contratual se aplicável → próximos passos → encerramento ("Seguimos à disposição." | "Qualquer dúvida, sigo à disposição!").
- Tom político e cordial; em apontamentos do cliente, valide o fluxo, acolha e esclareça sem confronto.
- Nunca prometa data fechada que dependa de fabricante, concessionária ou homologação.
- Atividade fora de dias úteis 07h–17h: inclua a ressalva de Homem-Hora Extra/Noturno conforme contrato.
- Grid = monitoramento, acompanhamento de segurança, manobras de abertura/religamento, validação de laudos. Obras civis e serviços especializados = contratada do cliente.
- Preventiva: "como parte do cronograma de manutenção preventiva mensal sob responsabilidade da Grid".
- Anexos sempre explicados ("encaminhamos em anexo a OS <nº> com as fotos de ...").
- Falha grave: tom firme e estruturado, enfatizando risco e necessidade de posicionamento urgente.

TÉCNICO
- "Olá, <Nome>. Tudo bem?" ou "Bom dia, <Nome>." → fato com fonte ("Conforme levantamento do PCM...") → dados exatos em bullets "•" (mais crítico primeiro) → pedidos numerados ("Preciso que você...") → prazo explícito → "Aguardo retorno."
- Uma mensagem por técnico, agrupando todas as usinas dele. Um assunto por mensagem.
- Sem ameaça, sem tom punitivo, sem sarcasmo, sem presumir causa — pergunte o motivo.
- Sem assinatura no WhatsApp.

E-MAIL
- Inclua "assunto". Corpo formal estruturado. Assinatura: "Atenciosamente,\\n\\nFred Alexandrino\\nSupervisor de O&M\\nGrid Co."

TEXTOS FORMAIS/RELATÓRIOS
- Nunca cite WhatsApp, grupo, áudio, print, "conforme conversa" nem frases literais entre aspas. Atribua orientações à área ("De acordo com a engenharia, ...")."""

# Decisões do Fred (17/11/2026) para esta Central — vão no contexto do usuário,
# não alteram o texto da 11.5.
REGRAS_CENTRAL = """REGRAS DESTA CENTRAL (têm prioridade sobre as acima):
- NÃO escreva saudação: o Fred escreve a saudação. Comece direto no corpo da mensagem (ignore a etapa "saudação" das estruturas acima).
- Mantenha o encerramento padrão no fim da ÚLTIMA mensagem.
- Não anuncie a cordialidade ("de forma cordial", "gentilmente", "cordialmente solicitamos"): a cordialidade vem do tom e das palavras.
- E-mail: só "assunto" e "texto" (com a assinatura); não inclua endereços de e-mail.
- Formatação igual à dos exemplos: usinas sempre em *negrito* no WhatsApp ("*UFV Nome*"), bullets "•", pedidos numerados.
- Técnico: termine exatamente com "Aguardo retorno."
- WhatsApp (cliente, técnico e texto de envio de relatório): SEM assinatura. "Atenciosamente" e assinatura só no e-mail.
- Use só as usinas, nomes e dados informados acima."""

# Exemplos de referência (seções 3.8, 4.6 e 8.7 do padrão — "use como few-shot
# no prompt"), na versão sem saudação e sem a Sal Energia, iguais aos usados
# nos testes do frontend (tests/comunicacao/exemplos.js no PAINELDEFALHAS).
EXEMPLOS = {
    "tecnico": ['Conforme levantamento do PCM, as preventivas abaixo estão com prazo vencido no painel de atividades:\n\n• 🔴 *UFV Colorado I* — preventiva mensal de inversores, vencida em 10/11\n• 🟠 *UFV Sarandi* — inspeção termográfica, vencida em 14/11\n• 🟡 *UFV Mandaguaçu* — limpeza de sala elétrica, vence em 19/11\n\nPreciso que você me informe:\n1. O motivo do atraso em Colorado I e Sarandi;\n2. A data prevista de execução de cada uma.\n\nRetorno ainda hoje, por favor.\nAguardo retorno.'],
    "cliente": ['Para mantermos o alinhamento sobre a manutenção corretiva da drenagem na *UFV Sete Lagoas*, no cruzamento da vala com os cabos CC, detalho abaixo a atuação de cada parte:\n\n*Responsabilidades da Empresa Contratada:*\n• Execução da escavação e das obras da vala de drenagem.\n• Movimentação segura dos cabos CC.\n• Execução do envelopamento de concreto dos cabos, conforme previsto em projeto.\n\n*Atuação da Grid:*\n1. Acompanhar a atividade no local para orientar a equipe e garantir a segurança das instalações.\n2. Desligar o inversor correspondente antes do início das intervenções nos cabos, assegurando que o trecho esteja totalmente sem energia para o trabalho da contratada. _(Lembrando que, caso os serviços ocorram em período noturno ou fora do horário comercial regular, precisaremos alinhar previamente os custos de homem-hora extra da nossa equipe, conforme previsto contratualmente.)_\n3. Religar o inversor e validar a operação assim que a contratada concluir os trabalhos no trecho.\n\nDiante disso, aguardo a confirmação da data exata da intervenção para que possamos programar e mobilizar nossa equipe técnica em campo.\n\nQualquer dúvida, sigo à disposição!', 'Como parte do cronograma de manutenção preventiva mensal sob responsabilidade da Grid, solicitamos a liberação de acesso ao rack onde o nobreak está instalado na *UFV Araputanga*.\n\nA intervenção tem como finalidade a inspeção técnica do equipamento, abrangendo a medição das tensões de entrada e saída, além da avaliação dos níveis de tensão do banco de baterias, assegurando o pleno funcionamento e a confiabilidade do sistema auxiliar.\n\nFicamos no aguardo da sua autorização para alinharmos o acesso junto à equipe em campo.\nSeguimos à disposição!', 'Em resposta ao seu questionamento sobre o Inversor 2.1 da *UFV Nova Londrina II*, apresentamos abaixo o diagnóstico técnico da falha identificada e o plano de ação proposto pela equipe da Grid:\n\n*1. Diagnóstico Técnico:*\n• Identificamos que a causa da baixa geração e da sinalização de falha no inversor decorre do fusível CC queimado na String 9 (Mesa 6, String 2).\n• No momento, não há dispositivo fusível sobressalente disponível em estoque na usina para substituição imediata.\n• Observação operacional: constatamos que já existem outros inversores na usina operando sem esse fusível de proteção específico — a exemplo do Inversor 3.1 —, condição pré-existente e não realizada pela Grid.\n\n[MENSAGEM 2]\n\n*2. Plano de Ação para Normalização:*\nPara restabelecer a operação plena do Inversor 2.1 com todas as 9 strings conectadas, a alternativa técnica imediata é realizar a ligação provisória sem o fusível CC.\n• Riscos técnicos envolvidos: sem o fusível CC, o circuito dependerá exclusivamente das proteções internas do inversor. Em eventual surto nessa string, o inversor desarmará para garantir sua autoproteção.\n\n*3. Próximos Passos (Alinhamento):*\nSolicitamos a sua validação para definirmos o direcionamento operacional:\n• *Opção A:* autorização para a Grid realizar a ligação provisória da String 9 sem o fusível CC, restabelecendo a capacidade total do inversor de imediato; ou\n• *Opção B:* previsão de envio/disponibilização da nova peça para programarmos a substituição do componente.\n\nFicamos no aguardo de sua decisão para mobilizar a equipe técnica.\nQualquer dúvida, sigo à disposição!'],
    "email": ['ASSUNTO: UFV Tupi Paulista I — Condição crítica da via de acesso\n\nEncaminhamos em anexo o Relatório de Ocorrência da UFV Tupi Paulista I, com a cronologia da vistoria realizada em 16/11/2026, os riscos identificados no trecho de aclive e as evidências fotográficas do local.\n\nA via apresenta processo erosivo ativo, com sulcos profundos nas trilhas de roda e ausência de drenagem, o que representa risco à equipe de O&M e à continuidade do atendimento à usina.\n\nSolicitamos o posicionamento da 2C Energia quanto à recuperação do acesso, com previsão de execução.\n\nSeguimos à disposição.\n\nAtenciosamente,\n\nFred Alexandrino\nSupervisor de O&M\nGrid Co.'],
    "envio_relatorio": ['Encaminhamos em anexo o Relatório de Ocorrência referente à condição da via de acesso à *UFV Tupi Paulista I*, com a cronologia da vistoria realizada em 16/11, os riscos identificados, as evidências fotográficas e as recomendações para recuperação do trecho.\n\nSolicitamos, por gentileza, o posicionamento da 2C Energia quanto à recuperação do acesso, com previsão de execução. A Grid permanece à disposição para acompanhar a vistoria no local com a empresa contratada.\n\nSeguimos à disposição.'],
}

TIPOS = {
    "cliente": "cliente",
    "tecnico": "tecnico",
    "email": "email",
    "envio_relatorio": "envio_relatorio",
}

INSTRUCAO_DIVIDIR = ("Divida em 2 mensagens: (1) diagnóstico/contexto; (2) opções, encaminhamento e próximos "
                     "passos, com o encerramento só na segunda.")

_LIM_RASCUNHO = 12000
_LIM_AJUSTE = 3000


def _txt(v, lim=500):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:lim]


def montar_contexto(body):
    """Monta o prompt do usuário (bloco descrito abaixo da seção 11.5).
    Levanta ValueError com mensagem amigável se faltar algo essencial."""
    tipo = (body.get("tipo") or "").strip()
    if tipo not in TIPOS:
        raise ValueError("tipo inválido (use cliente, tecnico, email ou envio_relatorio)")
    rascunho = str(body.get("rascunho") or "").strip()
    ajuste = str(body.get("ajuste") or "").strip()
    dividir = bool(body.get("dividir"))
    atual = body.get("versaoAtual") or {}
    msgs_atuais = [m for m in (atual.get("mensagens") or []) if isinstance(m, dict) and str(m.get("texto") or "").strip()]
    if dividir:
        ajuste = (ajuste + "\n" if ajuste else "") + INSTRUCAO_DIVIDIR
    if not rascunho and not msgs_atuais:
        raise ValueError("cole o rascunho/relato antes de gerar")
    if (ajuste or dividir) and not msgs_atuais:
        raise ValueError("não há versão anterior para ajustar")
    if len(rascunho) > _LIM_RASCUNHO:
        raise ValueError(f"rascunho muito longo (máx. {_LIM_RASCUNHO} caracteres)")
    if len(ajuste) > _LIM_AJUSTE:
        raise ValueError(f"pedido de ajuste muito longo (máx. {_LIM_AJUSTE} caracteres)")

    destinatarios = [_txt(d, 80) for d in (body.get("destinatarios") or []) if _txt(d, 80)]
    ufvs = []
    for u in (body.get("ufvs") or [])[:40]:
        if not isinstance(u, dict):
            continue
        nome = _txt(u.get("ufv"), 80)
        if not nome:
            continue
        partes = [nome]
        if _txt(u.get("cidadeUf")):
            partes.append(_txt(u.get("cidadeUf"), 80))
        if _txt(u.get("cliente")):
            partes.append("cliente " + _txt(u.get("cliente"), 60))
        if _txt(u.get("tecnico")):
            tec = _txt(u.get("tecnico"), 80)
            if _txt(u.get("funcao")):
                tec += f" ({_txt(u.get('funcao'), 40)})"
            partes.append("técnico responsável " + tec)
        ufvs.append(", ".join(partes))
    if not ufvs:
        raise ValueError("selecione ao menos uma UFV do mapeamento")

    linhas = [
        f"TIPO: {tipo}",
        f"DESTINATÁRIO(S): {', '.join(destinatarios) if destinatarios else 'a confirmar'}",
        "UFV(s): " + "; ".join(ufvs),
        f"DATA/HORA DE ENVIO: {_txt(body.get('dataHoraEnvio'), 20) or 'a confirmar'}",
        "RASCUNHO/RELATO:",
        rascunho or "(ver versão atual abaixo)",
    ]
    if ajuste:
        linhas.append(f"AJUSTE PEDIDO (se houver): {ajuste}  → devolver o texto COMPLETO corrigido")
        linhas.append("VERSÃO ATUAL (a ser corrigida, em JSON):")
        linhas.append(json.dumps({"mensagens": [{k: m.get(k) for k in ("titulo", "assunto", "texto") if m.get(k)}
                                                for m in msgs_atuais]}, ensure_ascii=False))
    linhas.append("")
    linhas.append("EXEMPLOS DE REFERÊNCIA (tom, estrutura e formatação a seguir; não copie os fatos):")
    for k, exemplo in enumerate(EXEMPLOS.get(tipo, []), 1):
        linhas.append(f"--- exemplo {k} ---")
        linhas.append(exemplo)
    linhas.append("--- fim dos exemplos ---")
    linhas.append("")
    linhas.append(REGRAS_CENTRAL)
    return "\n".join(linhas)


def interpretar_resposta(texto_bruto, tipo):
    """Lê o JSON da IA e normaliza para {introducao, mensagens[], observacoes[]}."""
    t = str(texto_bruto or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        dados = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise ValueError("a IA não devolveu JSON")
        dados = json.loads(m.group(0))
    if not isinstance(dados, dict):
        raise ValueError("a IA devolveu um formato inesperado")
    mensagens = []
    for m in dados.get("mensagens") or []:
        if isinstance(m, str):
            m = {"texto": m}
        if not isinstance(m, dict):
            continue
        texto = str(m.get("texto") or "").strip()
        if not texto:
            continue
        item = {"texto": texto}
        if str(m.get("titulo") or "").strip():
            item["titulo"] = str(m["titulo"]).strip()
        if tipo == "email" and str(m.get("assunto") or "").strip():
            item["assunto"] = str(m["assunto"]).strip()
        mensagens.append(item)
    if not mensagens:
        raise ValueError("a IA não devolveu nenhuma mensagem")
    obs = dados.get("observacoes") or []
    if isinstance(obs, str):
        obs = [obs]
    return {
        "introducao": str(dados.get("introducao") or "").strip(),
        "mensagens": mensagens,
        "observacoes": [str(o).strip() for o in obs if str(o).strip()],
    }


def payload_gemini(contexto):
    return {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT_11_5}]},
        "contents": [{"role": "user", "parts": [{"text": contexto}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 3000,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }


def registrar(app, chamar_gemini, log):
    """Registra POST /comunicacao/gerar no app Flask."""

    @app.route("/comunicacao/gerar", methods=["POST", "OPTIONS"])
    def comunicacao_gerar():
        """Central de Mensagens: gera (ou ajusta/divide) as mensagens.
        Body: {tipo, destinatarios[], ufvs[{ufv,cidadeUf,cliente,tecnico,funcao}],
        dataHoraEnvio:"dd/mm/aaaa HH:MM", rascunho, ajuste?, dividir?,
        versaoAtual?{mensagens[]}}. Resposta: {ok, introducao, mensagens[], observacoes[]}."""
        if request.method == "OPTIONS":
            return ("", 204)
        body = request.get_json(force=True, silent=True) or {}
        try:
            contexto = montar_contexto(body)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        tipo = body.get("tipo")
        try:
            resp = chamar_gemini(payload_gemini(contexto), timeout=45)
            data = resp.json()
            cand = (data.get("candidates") or [{}])[0]
            partes = ((cand.get("content") or {}).get("parts") or [])
            bruto = "".join(p.get("text", "") for p in partes if isinstance(p, dict))
            if not bruto.strip():
                motivo = cand.get("finishReason") or (data.get("promptFeedback") or {}).get("blockReason") or "resposta vazia"
                raise ValueError(f"a IA não respondeu ({motivo})")
            saida = interpretar_resposta(bruto, tipo)
        except Exception as e:
            log.error(f"[comunicacao/gerar] {tipo}: {e}")
            msg = str(e)
            if "429" in msg:
                msg = "cota da IA esgotada no momento — tente de novo em alguns minutos"
            return jsonify({"ok": False, "error": msg[:300]}), 502
        log.info(f"[comunicacao/gerar] {tipo}: {len(saida['mensagens'])} mensagem(ns)"
                 + (" (ajuste)" if body.get("ajuste") or body.get("dividir") else ""))
        return jsonify({"ok": True, **saida}), 200

    return comunicacao_gerar
