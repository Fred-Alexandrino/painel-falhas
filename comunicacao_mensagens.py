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
import base64
import io
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote

import requests
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
- E-mail: só "assunto" e "texto"; não inclua endereços de e-mail. O texto termina na linha "Atenciosamente," — NÃO escreva nome, cargo nem empresa depois (o Outlook já coloca a assinatura). Use *asteriscos* só no nome das usinas (viram negrito no Outlook); itens/condições em linhas com "- " (sem "•" nem emojis).
- Pessoa verbal: mantenha a do rascunho. Se o Fred escreve "solicito", escreva na 1ª pessoa do singular ("solicito", "fico no aguardo"); só use o plural ("solicitamos") se o rascunho usar.
- Leia os prints anexados e traga os dados objetivos que importam para a decisão (valor total, quantidades, prazos, condições de pagamento), mesmo que o rascunho não os repita. Ex.: proposta anexada com total → linha "- Valor: R$ X".
- Termos comerciais: modalidade de frete é CIF ou FOB ("SIF"/"Sif" no rascunho = CIF). Escreva só a modalidade ("- Frete: CIF"), sem explicar entre parênteses o que ela implica.
- Não acrescente explicações, parênteses ou consequências que o rascunho e os prints não dizem.
- Pedido de aprovação/autorização: feche ligando a espera ao próximo passo (ex.: "Fico no aguardo para seguir com o retorno ao cliente."), não com um "Seguimos à disposição" genérico.
- Ao citar uma pessoa, use exatamente o cargo da lista CARGOS abaixo (ex.: supervisor de campo não é "técnico"). Se a pessoa não estiver na lista, não atribua cargo.
- Formatação igual à dos exemplos: usinas sempre em *negrito* no WhatsApp ("*UFV Nome*"), bullets "•", pedidos numerados.
- Tipo técnico: a mensagem é postada no grupo de WhatsApp da equipe da usina e é DIRIGIDA AO SUPERVISOR DE CAMPO (o DESTINATÁRIO); o técnico responsável e a equipe também leem. Escreva para o supervisor de campo, citando o técnico pelo nome quando o pedido for para ele.
- Técnico: termine exatamente com "Aguardo retorno."
- WhatsApp (cliente, técnico e texto de envio de relatório): SEM assinatura. "Atenciosamente" e assinatura só no e-mail.
- Use só as usinas, nomes e dados informados acima."""

# Exemplos de referência (seções 3.8, 4.6 e 8.7 do padrão — "use como few-shot
# no prompt"), na versão sem saudação e sem a Sal Energia, iguais aos usados
# nos testes do frontend (tests/comunicacao/exemplos.js no PAINELDEFALHAS).
EXEMPLOS = {
    "tecnico": ['Conforme levantamento do PCM, as preventivas abaixo estão com prazo vencido no painel de atividades:\n\n• 🔴 *UFV Colorado I* — preventiva mensal de inversores, vencida em 10/11\n• 🟠 *UFV Sarandi* — inspeção termográfica, vencida em 14/11\n• 🟡 *UFV Mandaguaçu* — limpeza de sala elétrica, vence em 19/11\n\nPreciso que você me informe:\n1. O motivo do atraso em Colorado I e Sarandi;\n2. A data prevista de execução de cada uma.\n\nRetorno ainda hoje, por favor.\nAguardo retorno.'],
    "cliente": ['Para mantermos o alinhamento sobre a manutenção corretiva da drenagem na *UFV Sete Lagoas*, no cruzamento da vala com os cabos CC, detalho abaixo a atuação de cada parte:\n\n*Responsabilidades da Empresa Contratada:*\n• Execução da escavação e das obras da vala de drenagem.\n• Movimentação segura dos cabos CC.\n• Execução do envelopamento de concreto dos cabos, conforme previsto em projeto.\n\n*Atuação da Grid:*\n1. Acompanhar a atividade no local para orientar a equipe e garantir a segurança das instalações.\n2. Desligar o inversor correspondente antes do início das intervenções nos cabos, assegurando que o trecho esteja totalmente sem energia para o trabalho da contratada. _(Lembrando que, caso os serviços ocorram em período noturno ou fora do horário comercial regular, precisaremos alinhar previamente os custos de homem-hora extra da nossa equipe, conforme previsto contratualmente.)_\n3. Religar o inversor e validar a operação assim que a contratada concluir os trabalhos no trecho.\n\nDiante disso, aguardo a confirmação da data exata da intervenção para que possamos programar e mobilizar nossa equipe técnica em campo.\n\nQualquer dúvida, sigo à disposição!', 'Como parte do cronograma de manutenção preventiva mensal sob responsabilidade da Grid, solicitamos a liberação de acesso ao rack onde o nobreak está instalado na *UFV Araputanga*.\n\nA intervenção tem como finalidade a inspeção técnica do equipamento, abrangendo a medição das tensões de entrada e saída, além da avaliação dos níveis de tensão do banco de baterias, assegurando o pleno funcionamento e a confiabilidade do sistema auxiliar.\n\nFicamos no aguardo da sua autorização para alinharmos o acesso junto à equipe em campo.\nSeguimos à disposição!', 'Em resposta ao seu questionamento sobre o Inversor 2.1 da *UFV Nova Londrina II*, apresentamos abaixo o diagnóstico técnico da falha identificada e o plano de ação proposto pela equipe da Grid:\n\n*1. Diagnóstico Técnico:*\n• Identificamos que a causa da baixa geração e da sinalização de falha no inversor decorre do fusível CC queimado na String 9 (Mesa 6, String 2).\n• No momento, não há dispositivo fusível sobressalente disponível em estoque na usina para substituição imediata.\n• Observação operacional: constatamos que já existem outros inversores na usina operando sem esse fusível de proteção específico — a exemplo do Inversor 3.1 —, condição pré-existente e não realizada pela Grid.\n\n[MENSAGEM 2]\n\n*2. Plano de Ação para Normalização:*\nPara restabelecer a operação plena do Inversor 2.1 com todas as 9 strings conectadas, a alternativa técnica imediata é realizar a ligação provisória sem o fusível CC.\n• Riscos técnicos envolvidos: sem o fusível CC, o circuito dependerá exclusivamente das proteções internas do inversor. Em eventual surto nessa string, o inversor desarmará para garantir sua autoproteção.\n\n*3. Próximos Passos (Alinhamento):*\nSolicitamos a sua validação para definirmos o direcionamento operacional:\n• *Opção A:* autorização para a Grid realizar a ligação provisória da String 9 sem o fusível CC, restabelecendo a capacidade total do inversor de imediato; ou\n• *Opção B:* previsão de envio/disponibilização da nova peça para programarmos a substituição do componente.\n\nFicamos no aguardo de sua decisão para mobilizar a equipe técnica.\nQualquer dúvida, sigo à disposição!'],
    "email": ['ASSUNTO: UFV Tupi Paulista I — Condição crítica da via de acesso\n\nEncaminhamos em anexo o Relatório de Ocorrência da UFV Tupi Paulista I, com a cronologia da vistoria realizada em 16/11/2026, os riscos identificados no trecho de aclive e as evidências fotográficas do local.\n\nA via apresenta processo erosivo ativo, com sulcos profundos nas trilhas de roda e ausência de drenagem, o que representa risco à equipe de O&M e à continuidade do atendimento à usina.\n\nSolicitamos o posicionamento da 2C Energia quanto à recuperação do acesso, com previsão de execução.\n\nSeguimos à disposição.\n\nAtenciosamente,', 'ASSUNTO: Solicitação de compra — CFTV — UFV Nova Xavantina I e II\n\nConforme solicitado pelo cliente Renogrid, solicito a aprovação para a compra de 6 switchs, para a normalização do sistema de CFTV das *UFV Nova Xavantina I* e *UFV Nova Xavantina II*, conforme proposta em anexo.\n\nO custo total será incluído no BM da Renogrid com envio previsto para o dia 25/11/2026. As condições comerciais para esta aquisição são:\n\n- Valor: R$ 3.474,00\n- Pagamento: 03x no boleto (30/60/90 dias).\n- Prazo de entrega: postagem em até 3 dias úteis.\n- Frete: CIF\n\nFico no aguardo para seguir com o retorno ao cliente.\n\nAtenciosamente,'],
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
_MAX_PRINTS = 4
_MAX_PRINT_B64 = 6 * 1024 * 1024  # ~4,5 MB por imagem (o front já reduz antes de enviar)
_MIMES_PRINT = ("image/png", "image/jpeg", "image/webp")
# 18/11/2026: PDF anexado (relatório, OS) — o Gemini lê PDF direto (texto e imagens das páginas)
_MAX_PDFS = 2
_MAX_PDF_B64 = 14 * 1024 * 1024   # até ~10 MB o PDF vai inteiro para a IA (o pedido ao Gemini tem teto de ~20 MB)
_MAX_PDF_B64_TEXTO = 56 * 1024 * 1024   # até ~40 MB: o servidor extrai o texto (OS da Fracttal com centenas de fotos)
_MAX_DOCS = 2
_LIM_DOC = 40000


def prints_validos(body):
    """Prints anexados (WhatsApp, telas etc.) que a IA deve ler. Devolve a lista
    de partes inline_data do Gemini. Levanta ValueError se vier algo inválido."""
    itens = body.get("prints") or []
    if not isinstance(itens, list):
        raise ValueError("prints em formato inválido")
    pdfs = [it for it in itens if isinstance(it, dict) and str(it.get("mimeType") or "").lower() == "application/pdf"]
    if len(itens) - len(pdfs) > _MAX_PRINTS:
        raise ValueError(f"no máximo {_MAX_PRINTS} prints por mensagem")
    if len(pdfs) > _MAX_PDFS and body.get("tipo") == "envio_relatorio":
        raise ValueError(f"no máximo {_MAX_PDFS} PDFs por mensagem")
    partes = []
    for it in itens:
        if not isinstance(it, dict):
            raise ValueError("print em formato inválido")
        b64 = re.sub(r"^data:[^,]+,", "", str(it.get("base64") or "")).strip()
        mime = str(it.get("mimeType") or "image/jpeg").lower()
        if not b64:
            continue
        if mime == "application/pdf":
            if body.get("tipo") != "envio_relatorio":
                raise ValueError("PDF só no envio de relatório; nos outros tipos o print precisa ser imagem (PNG, JPG ou WEBP)")
            if len(b64) > _MAX_PDF_B64_TEXTO:
                raise ValueError("PDF grande demais (máx. ~40 MB)")
            if len(b64) > _MAX_PDF_B64:
                continue   # grande: vai como texto extraído (pdfs_como_texto)
        elif mime not in _MIMES_PRINT:
            raise ValueError("print precisa ser imagem (PNG, JPG ou WEBP)")
        elif len(b64) > _MAX_PRINT_B64:
            raise ValueError("print grande demais")
        partes.append({"inline_data": {"mime_type": mime, "data": b64}})
    return partes


def pdfs_como_texto(body):
    """PDF grande demais para ir inteiro: extrai o texto das páginas (pdfplumber) e manda como documento."""
    docs = []
    for it in body.get("prints") or []:
        if not (isinstance(it, dict) and str(it.get("mimeType") or "").lower() == "application/pdf"):
            continue
        b64 = re.sub(r"^data:[^,]+,", "", str(it.get("base64") or "")).strip()
        if len(b64) <= _MAX_PDF_B64 or len(b64) > _MAX_PDF_B64_TEXTO:
            continue
        try:
            import pdfplumber
            partes = []
            with pdfplumber.open(io.BytesIO(base64.b64decode(b64))) as pdf:
                for pg in pdf.pages[:80]:
                    partes.append(pg.extract_text() or "")
                    if sum(len(x) for x in partes) > _LIM_DOC:
                        break
            texto = "\n".join(partes).strip()
        except Exception as e:
            raise ValueError(f"não consegui ler o PDF grande ({str(e)[:80]})")
        if not texto:
            raise ValueError("o PDF grande não tem texto legível (é só imagem) — envie uma versão menor que 10 MB")
        docs.append({"nome": _txt(it.get("nome"), 120) or "PDF anexado", "texto": texto[:_LIM_DOC]})
    return docs


def documentos_validos(body):
    """Documentos em texto anexados (ex.: a OS lida da Fracttal) + PDFs grandes convertidos em texto. [{nome, texto}]"""
    if "_docs_cache" in body:
        return body["_docs_cache"]
    docs = pdfs_como_texto(body)
    for d in (body.get("documentos") or [])[:_MAX_DOCS]:
        if isinstance(d, dict) and str(d.get("texto") or "").strip():
            docs.append({"nome": _txt(d.get("nome"), 120) or "documento", "texto": str(d["texto"]).strip()[:_LIM_DOC]})
    body["_docs_cache"] = docs
    return docs


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
    n_prints = len(body.get("prints") or [])
    eh_pdf = lambda it: isinstance(it, dict) and str(it.get("mimeType") or "").lower() == "application/pdf"
    n_imgs = sum(1 for it in (body.get("prints") or []) if not eh_pdf(it))
    n_pdfs = sum(1 for it in (body.get("prints") or []) if eh_pdf(it)
                 and len(re.sub(r"^data:[^,]+,", "", str(it.get("base64") or ""))) <= _MAX_PDF_B64)
    docs = documentos_validos(body)
    if not rascunho and not msgs_atuais and not n_prints and not docs:
        raise ValueError("escreva o rascunho ou anexe um print antes de gerar")
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
    cliente = _txt(body.get("cliente"), 60)
    if not ufvs and not (tipo != "tecnico" and cliente):
        raise ValueError("selecione o cliente" if tipo != "tecnico" else "selecione ao menos uma UFV do mapeamento")

    linhas = [
        f"TIPO: {tipo}",
        f"DESTINATÁRIO(S): {', '.join(destinatarios) if destinatarios else 'não informado (o texto não depende do nome)'}",
        *([f"CLIENTE: {cliente}"] if cliente else []),
        "UFV(s): " + ("; ".join(ufvs) if ufvs else f"não selecionadas (cliente {cliente}); use só as usinas citadas no rascunho/prints, como \"UFV <Nome>\""),
        f"DATA/HORA DE ENVIO: {_txt(body.get('dataHoraEnvio'), 20) or 'a confirmar'}",
        "RASCUNHO/RELATO:",
        rascunho or ("(ver os anexos)" if (n_prints or docs) else "(ver versão atual abaixo)"),
    ]
    if n_imgs:
        linhas.append(f"PRINTS ANEXADOS: {n_imgs} imagem(ns) logo abaixo deste texto. Leia o conteúdo e use só fatos que estejam "
                      "visíveis neles ou no rascunho. Não mencione que a informação veio de print, WhatsApp, grupo ou conversa.")
    if n_pdfs:
        linhas.append(f"PDF ANEXADO: {n_pdfs} arquivo(s) PDF logo abaixo deste texto — é o relatório/documento que será enviado "
                      "ao cliente. Leia o PDF inteiro e escreva o texto de envio a partir dele: o que o documento traz (atividade, "
                      "usina, data, resultado, pendências/recomendações) e o que se pede ao cliente, se houver. Use só fatos do PDF e do rascunho.")
    for d in docs:
        linhas.append(f"DOCUMENTO ANEXADO — {d['nome']} (é o documento que vai junto ao texto; use os fatos dele). "
                      "Checklist: item \"Falhou\" cuja descrição diz que só se faz sob solicitação do cliente, \"se aplicável\" ou "
                      "que a observação justifica como não aplicável NÃO é pendência — não liste; pendência é o que o técnico "
                      "encontrou com defeito/faltando. Não liste itens aprovados um a um: resuma o que foi feito.")
        linhas.append(d["texto"])
        linhas.append("--- fim do documento ---")
    if ajuste:
        linhas.append(f"AJUSTE PEDIDO (se houver): {ajuste}  → devolver o texto COMPLETO corrigido")
        linhas.append("VERSÃO ATUAL (a ser corrigida, em JSON):")
        linhas.append(json.dumps({"mensagens": [{k: m.get(k) for k in ("titulo", "assunto", "texto") if m.get(k)}
                                                for m in msgs_atuais]}, ensure_ascii=False))
    pessoas = [p for p in (body.get("pessoas") or []) if isinstance(p, dict) and _txt(p.get("nome"), 80)][:120]
    if pessoas:
        linhas.append("CARGOS: " + "; ".join(f"{_txt(p.get('nome'), 80)} — {_txt(p.get('cargo'), 80)}" for p in pessoas))
    linhas.append("")
    linhas.append("EXEMPLOS DE REFERÊNCIA (tom, estrutura e formatação a seguir; não copie os fatos):")
    for k, exemplo in enumerate(EXEMPLOS.get(tipo, []), 1):
        linhas.append(f"--- exemplo {k} ---")
        linhas.append(exemplo)
    linhas.append("--- fim dos exemplos ---")
    linhas.append("")
    linhas.append(REGRAS_CENTRAL)
    return "\n".join(linhas)


def cortar_assinatura_email(texto):
    """O e-mail termina em "Atenciosamente,": a assinatura (nome, cargo, Grid Co.)
    é a imagem do Outlook. O 11.5 manda escrever a assinatura; a decisão de
    18/11/2026 do Fred prevalece, então o bloco em texto é removido aqui."""
    m = re.search(r"(?im)^\s*atenciosamente\s*,?\s*$", texto)
    if m:
        return texto[:m.start()].rstrip() + "\n\nAtenciosamente,"
    return re.sub(r"\n\s*Fred Alexandrino\s*\n\s*Supervisor de O&M\s*(\n\s*Grid Co\.?)?\s*$", "", texto).rstrip()


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
        if tipo == "email":
            texto = cortar_assinatura_email(texto)
        else:
            # WhatsApp não leva assinatura (REGRAS_CENTRAL); a IA às vezes fecha com "Atenciosamente, Fred..."
            texto = re.sub(r"\n\s*Atenciosamente,?\s*(\n\s*Fred Alexandrino.*)?\s*$", "", texto, flags=re.S | re.I).rstrip()
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


def payload_gemini(contexto, prints=None):
    return {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT_11_5}]},
        "contents": [{"role": "user", "parts": [{"text": contexto}] + list(prints or [])}],
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
        Body: {tipo, cliente?, destinatarios[], ufvs[{ufv,cidadeUf,cliente,tecnico,funcao}] (opcional
        fora do tipo técnico), prints?[{base64,mimeType}] (até 4),
        dataHoraEnvio:"dd/mm/aaaa HH:MM", rascunho, ajuste?, dividir?,
        versaoAtual?{mensagens[]}}. Resposta: {ok, introducao, mensagens[], observacoes[]}."""
        if request.method == "OPTIONS":
            return ("", 204)
        body = request.get_json(force=True, silent=True) or {}
        try:
            prints = prints_validos(body)
            contexto = montar_contexto(body)
            docs = documentos_validos(body)
            if docs:
                log.info(f"[comunicacao/gerar] documentos anexados: {[d['nome'] for d in docs]}")
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        tipo = body.get("tipo")
        try:
            tem_pdf = any((p.get("inline_data") or {}).get("mime_type") == "application/pdf" for p in prints)
            # 18/11/2026: o Gemini às vezes devolve 503 ("high demand") — tenta mais 2 vezes antes de desistir
            for espera in (4, 10, None):
                try:
                    resp = chamar_gemini(payload_gemini(contexto, prints), timeout=110 if tem_pdf else (60 if prints else 45))
                    break
                except Exception as e_g:
                    if espera is None or not re.search(r"\b(503|500|UNAVAILABLE|overloaded|high demand)\b", str(e_g), re.I):
                        raise
                    log.warning(f"[comunicacao/gerar] Gemini indisponível, nova tentativa em {espera}s")
                    time.sleep(espera)
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
            msg = re.sub(r"key=[^&\s\"']+", "key=***", str(e))   # nunca devolver a chave da API para a tela
            if "429" in msg:
                msg = "cota da IA esgotada no momento — tente de novo em alguns minutos"
            elif re.search(r"\b(503|UNAVAILABLE|high demand)\b", msg, re.I):
                msg = "a IA do Google está sobrecarregada agora — tente de novo em 1 ou 2 minutos"
            return jsonify({"ok": False, "error": msg[:300]}), 502
        log.info(f"[comunicacao/gerar] {tipo}: {len(saida['mensagens'])} mensagem(ns)"
                 + (" (ajuste)" if body.get("ajuste") or body.get("dividir") else ""))
        return jsonify({"ok": True, **saida}), 200

    @app.route("/comunicacao/os-anexo", methods=["POST", "OPTIONS"])
    def comunicacao_os_anexo():
        """18/11/2026: lê a OS da Fracttal (o mesmo conteúdo do PDF de impressão da OS: dados, ativos,
        checklist com Aprovou/Alerta/Falhou, observações, fotos) para a Central gerar o texto de envio.
        Body: {link: "https://printwo.fracttal.com/?<id público>", numeroOS?}.
        Resposta: {ok, nome, texto, fotos[{base64, mimeType}]}."""
        if request.method == "OPTIONS":
            return ("", 204)
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify({"ok": True, **ler_os_fracttal(body.get("link"), body.get("numeroOS"), int(body.get("maxFotos") or 3))}), 200
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            log.error(f"[comunicacao/os-anexo] {body.get('numeroOS')}: {e}")
            return jsonify({"ok": False, "error": f"não consegui ler a OS na Fracttal ({str(e)[:150]})"}), 502

    return comunicacao_gerar


# ── OS da Fracttal (link público de impressão printwo.fracttal.com) ──────────
_FT_SHARE = "https://one.fracttal.com/rpc/print/shareWo?idPublic="
_CHECK = {"1": "Aprovou", "2": "Alerta", "3": "Falhou"}
_TZ = timezone(timedelta(hours=-3))


def _dt(s, so_data=False):
    if not s:
        return ""
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(_TZ)
        return d.strftime("%d/%m/%Y" if so_data else "%d/%m/%Y %H:%M")
    except Exception:
        return str(s)[:16]


def _dur(seg):
    try:
        seg = int(seg or 0)
    except Exception:
        return ""
    return f"{seg // 3600}h{(seg % 3600) // 60:02d}" if seg else ""


def _limpa_ativo(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def montar_texto_os(d, numero=None):
    """Transforma o JSON da OS (shareWo) no texto que o PDF de impressão mostra."""
    tasks = d.get("tasks") or []
    t0 = tasks[0] if tasks else {}
    folio = d.get("wo_folio") or numero or ""
    L = [f"ORDEM DE TRABALHO Nº {folio} — {(d.get('company') or {}).get('description') or 'Grid Co.'}"]
    usina = t0.get("items_log_groups_1_description") or ""
    if usina:
        L.append(f"Usina (classificação 1): {usina}" + (f" · cluster {t0.get('items_log_groups_2_description')}" if t0.get("items_log_groups_2_description") else ""))
    L.append(f"Criada em {_dt(d.get('creation_date'))} por {d.get('created_by') or '—'} · responsável: {d.get('personnel_description') or d.get('user_assigned') or '—'}"
             + (f" · validada por {d['validated_by_description'].strip()}" if (d.get("validated_by_description") or "").strip() else "")
             + f" · {d.get('completed_percentage', 0)}% concluída"
             + (f" · finalizada em {_dt(d.get('final_date'))}" if d.get("final_date") else ""))
    if (d.get("note") or "").strip():
        L.append(f"Notas da OS: {d['note'].strip()}")
    falhas, alertas = [], []
    for t in tasks:
        ativo = _limpa_ativo(t.get("items_log_description") or t.get("items_description"))
        L.append("")
        L.append(f"■ Ativo: {ativo}")
        if t.get("items_log_parent_description"):
            L.append(f"  Local: {_limpa_ativo(t['items_log_parent_description']).strip('/ ')}")
        L.append(f"  Tarefa: {(t.get('tasks_description') or '').strip()} · {t.get('tasks_types_main_description') or ''}"
                 + (f" / {t['tasks_types_description']}" if t.get("tasks_types_description") else "")
                 + (f" · {t['tasks_types_2_description']}" if t.get("tasks_types_2_description") else ""))
        L.append(f"  Agendada: {_dt(t.get('date_maintenance'), True)} · início {_dt(t.get('initial_date')) or '—'} · fim {_dt(t.get('final_date')) or '—'}"
                 + (f" · tempo de trabalho {_dur(t.get('real_duration'))}" if t.get("real_duration") else "")
                 + (" · concluída" if t.get("done") else " · não concluída"))
        if (t.get("task_note") or "").strip():
            L.append(f"  Nota da tarefa: {t['task_note'].strip()}")
        if t.get("types_description") or t.get("causes_description"):
            L.append(f"  Falha: {t.get('types_description') or ''} · causa: {t.get('causes_description') or ''} · severidade: {t.get('severiry_description') or ''}")
        for f in sorted(t.get("task_form_items") or [], key=lambda x: x.get("order_number") or 0):
            desc = (f.get("description") or "").strip()
            v = f.get("value")
            tipo = f.get("id_task_form_item_type")
            if v in (None, ""):
                continue
            if tipo == 4:
                res = _CHECK.get(str(v), str(v))
                if res == "Falhou":
                    falhas.append(f"{ativo}: {desc}")
                elif res == "Alerta":
                    alertas.append(f"{ativo}: {desc}")
            elif tipo == 2:
                res = "Sim" if str(v).lower() in ("true", "1") else "Não"
            else:
                res = str(v).strip()
            un = (f.get("unit_description") or "").strip()
            L.append(f"  - {desc}: {res}{(' ' + un) if un and tipo not in (2, 4) else ''}")
        obs = [str(i.get("value") or "").strip() for i in (t.get("task_images") or [])
               if str(i.get("type")) == "3" and str(i.get("value") or "").strip()]
        for o in obs:
            L.append(f"  - Observação: {o}")
        n_fotos = sum(1 for i in (t.get("task_images") or []) if str(i.get("type")) == "1" and "/.ot/" in (i.get("path_image") or ""))
        if n_fotos:
            L.append(f"  - Fotos anexadas no item: {n_fotos}")
    L.append("")
    L.append("RESUMO DO CHECKLIST: " + (f"{len(falhas)} item(ns) Falhou" if falhas else "nenhum item Falhou")
             + (f", {len(alertas)} em Alerta" if alertas else ""))
    for x in falhas[:30]:
        L.append(f"  Falhou — {x}")
    for x in alertas[:30]:
        L.append(f"  Alerta — {x}")
    return "\n".join(L)


def _fotos_os(d, maximo):
    """Escolhe até `maximo` fotos: primeiro as dos itens com Falhou/Alerta, depois uma por ativo."""
    if maximo <= 0:
        return []
    prioridade, demais = [], []
    for t in d.get("tasks") or []:
        ruins = {f.get("id_work_orders_tasks_form_items") for f in (t.get("task_form_items") or [])
                 if f.get("id_task_form_item_type") == 4 and str(f.get("value")) in ("2", "3")}
        fotos = [i for i in (t.get("task_images") or []) if str(i.get("type")) == "1" and "/.ot/" in (i.get("path_image") or "")]
        for k, i in enumerate(fotos):
            if str(i.get("id_work_orders_tasks_form_items")) in {str(x) for x in ruins}:
                prioridade.append(i)
            elif k == 0:
                demais.append(i)
    saida = []
    try:
        from PIL import Image
    except Exception:
        Image = None
    for i in prioridade + demais:
        if len(saida) >= maximo:
            break
        try:
            r = requests.get(i["path_image"], timeout=20)
            r.raise_for_status()
            dados = r.content
            if Image is not None:
                im = Image.open(io.BytesIO(dados)).convert("RGB")
                im.thumbnail((1280, 1280))
                b = io.BytesIO()
                im.save(b, "JPEG", quality=80)
                dados = b.getvalue()
            elif len(dados) > 3 * 1024 * 1024:
                continue
            saida.append({"base64": base64.b64encode(dados).decode(), "mimeType": "image/jpeg",
                          "legenda": f"{_limpa_ativo(i.get('form_item_description'))[:80]}"})
        except Exception:
            continue
    return saida


def ler_os_fracttal(link, numero=None, max_fotos=3):
    m = re.search(r"printwo\.fracttal\.com/\?([^\s&#]+)", str(link or ""))
    if not m:
        raise ValueError("esta OS não tem o link de impressão da Fracttal (printwo) — abra a OS e baixe o PDF para anexar")
    id_publico = unquote(m.group(1))
    r = requests.get(_FT_SHARE + quote(id_publico, safe=""), timeout=40)
    r.raise_for_status()
    j = r.json()
    d = j.get("data") if isinstance(j, dict) else None
    if not j.get("success") or not isinstance(d, dict):
        raise ValueError("a Fracttal não devolveu a OS")
    texto = montar_texto_os(d, numero)
    return {"nome": f"OS {d.get('wo_folio') or numero or ''} (Fracttal)", "texto": texto[:_LIM_DOC],
            "fotos": _fotos_os(d, max(0, min(int(max_fotos or 0), _MAX_PRINTS)))}
