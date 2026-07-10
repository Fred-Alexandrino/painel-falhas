# -*- coding: utf-8 -*-
"""
relatorio_semanal.py
─────────────────────────────────────────────────────────────────────────
Geração automática do "Relatório Semanal" (.pptx) no modelo Grid Co.,
a partir das ocorrências (Painel de Falhas) E das OSs/atividades (Painel
de Atividades) já registradas nas planilhas.

Não usa nenhuma API de IA — o texto é montado por regras determinísticas
em cima dos campos de cada fonte (resumido, sem despejar o histórico
bruto de ações).
"""

import os
import re
import copy
import unicodedata
from io import BytesIO
from datetime import datetime

from pptx import Presentation
from pptx.oxml.ns import qn

# ── Configuração ───────────────────────────────────────────────────────────

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "modelo_relatorio_semanal.pptx")

# Índices das colunas da planilha principal — Painel de Falhas (0-based,
# iguais ao app.py / CAMPO_COL)
COL_ID, COL_CLIENTE, COL_USINA, COL_EQUIP = 0, 1, 2, 3
COL_FALHA, COL_CAUSA, COL_IMPACTADOS, COL_ACAO = 4, 5, 6, 7
COL_STATUS, COL_TICKET, COL_OS, COL_HISTORICO = 8, 9, 10, 11
COL_DATA_ABERTURA = 12
COL_DATA_FECHAMENTO = 20  # coluna U (21ª, 1-based) = índice 20 (0-based)

# Índices das colunas da aba Painel de Atividades (0-based, iguais ao
# ATIV_CAMPO_COL do app.py, que é 1-based -> aqui -1)
ATIV_COL_ID, ATIV_COL_CLIENTE, ATIV_COL_USINA, ATIV_COL_EQUIP = 0, 1, 2, 3
ATIV_COL_DESCRICAO, ATIV_COL_RESPONSAVEL, ATIV_COL_PRAZO, ATIV_COL_PRIORIDADE = 4, 5, 6, 7
ATIV_COL_STATUS, ATIV_COL_DATA_CRIACAO, ATIV_COL_DATA_CONCLUSAO, ATIV_COL_HISTORICO = 8, 9, 10, 11
ATIV_COL_EDITOR, ATIV_COL_NUMEROOS, ATIV_COL_STATUSOS = 12, 13, 14

STATUS_CONCLUIDO = ["concluído", "concluido", "resolvido", "fechado", "resolved", "closed"]

# Uma atividade/OS só é considerada concluída quando o statusOS chega em
# "Em Revisão" ou "Finalizada" — nunca pelo percentual de tarefas (ver
# regra registrada em memória: 100% das tarefas em "Em Processo" ainda é
# aguardando submissão, não conclusão).
STATUS_OS_CONCLUIDO = ["em revisão", "em revisao", "finalizada"]

CAT_COMUNICACOES = "COMUNICAÇÕES / SCADA / CCTV"
CAT_DESLIGAMENTOS = "DESLIGAMENTOS"

# Categorias "bonitas" para exibir no relatório (padrões regex, avaliados em ordem —
# o primeiro que bater define a categoria). Casa tanto nomes por extenso ("Inversor")
# quanto códigos curtos usados em campo ("Inv-18", "NVR", "Chave Seccionadora Skid 1").
# A ordem desta lista também define a ordem de exibição das categorias no relatório
# (exceto Comunicações e Desligamentos, que sempre recebem página própria — ver
# gerar_relatorio_pptx).
PADROES_CATEGORIA = [
    (r"\binv[\s\-]*r?\.?\d+|inversor", "INVERSORES"),
    (r"tracker|\btcu\b", "TRACKERS / TCU"),
    (r"fusive|fusíve", "FUSÍVEIS"),
    (r"transformador", "TRANSFORMADORES"),
    (r"switchgear|chave seccionadora|disjuntor|religador", "SWITCHGEAR"),
    (r"\bskid", "SKIDS"),
    (r"preventiv", "MANUTENÇÃO PREVENTIVA"),
    (r"\bstring\b|modulo|módulo|otimizador", "STRINGS / MÓDULOS"),
    (r"usina desligad|usina offline|desligamento|ufv desligad|usina parad", CAT_DESLIGAMENTOS),
    (r"comunica|scada|cctv|cftv|\bnvr\b|camera|câmera|speed dome|igate", CAT_COMUNICACOES),
    (r"sensor|piranometro|piranômetro|solarimetric|estacao solarimetrica|estação solarimétrica", "SENSORES"),
    (r"emergenc", "EMERGÊNCIAS"),
    (r"operac|opera[çc][aã]o", "OPERAÇÕES"),
    (r"termografia|termograf", "TERMOGRAFIA"),
    (r"restart|religamento", "RESTART / RELIGAMENTO"),
    (r"ronda", "RONDAS SEMANAIS"),
    (r"exaustor", "EXAUSTORES"),
    (r"vegeta", "CONTROLE DE VEGETAÇÃO"),
]

# Rótulo genérico pra tudo que não bate em nenhum padrão acima (formulários,
# spare parts, reclamações de concessionária, códigos Fracttal sem nenhuma
# palavra reconhecível etc.) — nunca usa o texto bruto do equipamento como
# categoria própria, pra não fragmentar o relatório em uma mini-seção por
# código.
CAT_OUTRAS = "OUTRAS OCORRÊNCIAS"

# Ordem de exibição das categorias "genéricas" (tudo que não é Comunicações
# nem Desligamentos, que são tratadas à parte). Categorias fora desta lista
# (rótulos livres vindos direto do campo Equipamento) vão para o final, em
# ordem alfabética.
ORDEM_CATEGORIAS_GERAIS = [rotulo for _, rotulo in PADROES_CATEGORIA
                           if rotulo not in (CAT_COMUNICACOES, CAT_DESLIGAMENTOS)] + [CAT_OUTRAS]

# Zeladoria: colunas fixas da tabela do relatório
ZELADORIA_COLUNAS = ["Usina", "Roçagem", "Poda Química", "Limpeza dos Módulos"]
# Mapeia cada coluna da tabela para o "nome" de grupo usado em coletar_zeladoria()
ZELADORIA_MAPA_COLUNAS = [("Roçagem", "Roçada"), ("Poda Química", "Poda Química"),
                          ("Limpeza dos Módulos", "Lavagem dos Módulos")]
ZELADORIA_MAX_USINAS_POR_PAGINA = 8  # evita estourar a tabela pra fora do slide


# ── Utilidades de texto/data ────────────────────────────────────────────────

def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = s.encode("ascii", "ignore").decode("ascii")
    return s.strip()


def _rotulo_categoria(equip_bruto):
    n = _norm(equip_bruto)
    for padrao, rotulo in PADROES_CATEGORIA:
        if re.search(padrao, n):
            return rotulo
    return CAT_OUTRAS


def _parse_data(txt):
    """Aceita 'dd/mm/aaaa[ hh:mm[:ss]]' -> datetime, ou None."""
    if not txt:
        return None
    txt = txt.strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(txt, fmt)
        except ValueError:
            continue
    return None


def _fmt_data_hora(dt):
    """datetime -> 'dd/mm/aaaa às HH:MM'."""
    return dt.strftime("%d/%m/%Y às %H:%M")


def _is_concluido(status):
    s = (status or "").lower()
    return any(x in s for x in STATUS_CONCLUIDO)


def _atividade_concluida(status_os):
    return (status_os or "").strip().lower() in STATUS_OS_CONCLUIDO


def _ticket_valido(ticket):
    """Só considera 'chamado real' se houver um número/identificador de verdade."""
    t = (ticket or "").strip().lower()
    return t not in ("", "n/a", "na", "-", "--", "nao", "não", "sem chamado", "sem ticket", "s/n")


def _ordenar_categorias(chaves):
    """Ordena rótulos de categoria pela ordem definida em ORDEM_CATEGORIAS_GERAIS;
    o que não estiver na lista vai para o final, em ordem alfabética."""
    def chave_ordenacao(rotulo):
        if rotulo in ORDEM_CATEGORIAS_GERAIS:
            return (0, ORDEM_CATEGORIAS_GERAIS.index(rotulo))
        return (1, rotulo)
    return sorted(chaves, key=chave_ordenacao)


# ── Coleta e agrupamento — Painel de Falhas (ocorrências) ──────────────────
#
# Toda ocorrência/atividade, seja qual for a origem, é normalizada para o
# mesmo formato de dict antes de entrar em "grupos":
#   {"usina", "equipamento", "falha", "status", "dt_abertura",
#    "dt_fechamento", "concluida", "origem"}
# Isso permite juntar Painel de Falhas + Painel de Atividades num único
# relatório sem duplicar as funções de geração de texto/slide.

def coletar_ocorrencias_semana(todos_valores, cliente, data_inicio, data_fim):
    """
    todos_valores: retorno de ws.get_all_values() do Painel de Falhas (linha 0 = cabeçalho)
    cliente: nome do cliente (comparação por 'contains', case-insensitive)
    data_inicio / data_fim: datetime (00:00 do dia inicial até 23:59 do dia final)

    Retorna dict: {"categoria": {"concluidas": [dict,...], "abertas": [dict,...]}, ...}
    """
    cliente_norm = _norm(cliente)
    grupos = {}

    for row in todos_valores[1:]:
        if len(row) <= COL_DATA_FECHAMENTO:
            row = row + [""] * (COL_DATA_FECHAMENTO + 1 - len(row))

        if cliente_norm not in _norm(row[COL_CLIENTE]):
            continue

        dt_abertura = _parse_data(row[COL_DATA_ABERTURA])
        dt_fecham = _parse_data(row[COL_DATA_FECHAMENTO])
        concluida = _is_concluido(row[COL_STATUS])

        fechou_na_semana = dt_fecham and data_inicio <= dt_fecham <= data_fim
        abriu_na_semana = dt_abertura and data_inicio <= dt_abertura <= data_fim
        em_aberto_backlog = not concluida

        if not (fechou_na_semana or abriu_na_semana or em_aberto_backlog):
            continue

        categoria = _rotulo_categoria(row[COL_EQUIP])
        d = {
            "usina": row[COL_USINA].strip() or "Usina não informada",
            "equipamento": row[COL_EQUIP].strip() or "Equipamento não informado",
            "falha": row[COL_FALHA].strip() or "Falha não especificada",
            "status": row[COL_STATUS].strip() or "Em aberto",
            "dt_abertura": dt_abertura,
            "dt_fechamento": dt_fecham,
            "concluida": concluida,
            "origem": "falha",
        }
        grupos.setdefault(categoria, {"concluidas": [], "abertas": []})

        if fechou_na_semana:
            grupos[categoria]["concluidas"].append(d)
        elif em_aberto_backlog:
            grupos[categoria]["abertas"].append(d)

    return {k: v for k, v in grupos.items() if v["concluidas"] or v["abertas"]}


# ── Coleta e agrupamento — Painel de Atividades (OSs) ───────────────────────

def coletar_atividades_semana(todos_valores, cliente, data_inicio, data_fim):
    """
    todos_valores: retorno de ws.get_all_values() do Painel de Atividades
    (linha 0 = cabeçalho). Mesma lógica de janela/backlog de
    coletar_ocorrencias_semana, mas a data de abertura é a Data de Criação
    da OS/atividade e a de fechamento é a Data de Conclusão; e "concluída"
    é definido pelo statusOS (Em Revisão / Finalizada) — nunca por
    percentual de tarefas.

    Retorna dict no mesmo formato de coletar_ocorrencias_semana, pronto
    para ser mesclado com mesclar_grupos().
    """
    cliente_norm = _norm(cliente)
    grupos = {}
    min_cols = ATIV_COL_STATUSOS + 1

    for row in todos_valores[1:]:
        if len(row) <= min_cols:
            row = row + [""] * (min_cols + 1 - len(row))

        if cliente_norm not in _norm(row[ATIV_COL_CLIENTE]):
            continue

        dt_abertura = _parse_data(row[ATIV_COL_DATA_CRIACAO])
        dt_fecham = _parse_data(row[ATIV_COL_DATA_CONCLUSAO])
        concluida = _atividade_concluida(row[ATIV_COL_STATUSOS])

        fechou_na_semana = dt_fecham and data_inicio <= dt_fecham <= data_fim
        abriu_na_semana = dt_abertura and data_inicio <= dt_abertura <= data_fim
        em_aberto_backlog = not concluida

        if not (fechou_na_semana or abriu_na_semana or em_aberto_backlog):
            continue

        categoria = _rotulo_categoria(row[ATIV_COL_EQUIP])
        status_display = row[ATIV_COL_STATUSOS].strip() or row[ATIV_COL_STATUS].strip() or "Em aberto"
        d = {
            "usina": row[ATIV_COL_USINA].strip() or "Usina não informada",
            "equipamento": row[ATIV_COL_EQUIP].strip() or "Equipamento não informado",
            "falha": row[ATIV_COL_DESCRICAO].strip() or "Sem descrição",
            "status": status_display,
            "dt_abertura": dt_abertura,
            "dt_fechamento": dt_fecham,
            "concluida": concluida,
            "origem": "atividade",
        }
        grupos.setdefault(categoria, {"concluidas": [], "abertas": []})

        if fechou_na_semana:
            grupos[categoria]["concluidas"].append(d)
        elif em_aberto_backlog:
            grupos[categoria]["abertas"].append(d)

    return {k: v for k, v in grupos.items() if v["concluidas"] or v["abertas"]}


def mesclar_grupos(*grupos_varios):
    """Mescla dois ou mais dicts no formato de coletar_ocorrencias_semana
    (ex.: Painel de Falhas + Painel de Atividades) em um único dict de
    categorias, somando as listas de concluídas/abertas de cada fonte."""
    resultado = {}
    for grupos in grupos_varios:
        for cat, dados in (grupos or {}).items():
            alvo = resultado.setdefault(cat, {"concluidas": [], "abertas": []})
            alvo["concluidas"].extend(dados.get("concluidas", []))
            alvo["abertas"].extend(dados.get("abertas", []))
    return resultado


def listar_usinas_cliente(todos_valores, cliente):
    """Lista todas as usinas do cliente (histórico completo, não só a semana)."""
    cliente_norm = _norm(cliente)
    usinas = []
    vistos = set()
    for row in todos_valores[1:]:
        if len(row) <= COL_USINA:
            continue
        if cliente_norm not in _norm(row[COL_CLIENTE]):
            continue
        usina = row[COL_USINA].strip()
        chave = _norm(usina)
        if usina and chave not in vistos:
            vistos.add(chave)
            usinas.append(usina)
    return sorted(usinas)


def coletar_chamados_abertos(todos_valores, cliente):
    """Ocorrências do cliente (Painel de Falhas) com número de chamado/ticket
    fabricante válido e ainda não concluídas."""
    cliente_norm = _norm(cliente)
    chamados = []
    for row in todos_valores[1:]:
        if len(row) <= COL_DATA_FECHAMENTO:
            row = row + [""] * (COL_DATA_FECHAMENTO + 1 - len(row))
        if cliente_norm not in _norm(row[COL_CLIENTE]):
            continue
        ticket = row[COL_TICKET].strip()
        if not _ticket_valido(ticket) or _is_concluido(row[COL_STATUS]):
            continue
        chamados.append(row)
    return chamados


# ── Geração de conteúdo (offline, sem IA) — parágrafos com negrito por usina ─

def _linha_ocorrencia(d):
    """Linha resumida: Equipamento – Falha. Status: X. (sem despejar causa/ação/histórico)"""
    equip = d["equipamento"]
    falha = d["falha"]
    if len(falha) > 180:
        falha = falha[:177].rstrip() + "..."
    status = d["status"]
    return f"{equip} – {falha}. Status: {status}."


def gerar_paragrafos_categoria(categoria, dados):
    """
    Agrupa as ocorrências/atividades de uma categoria por usina (nome da
    usina em negrito, seguido de um bullet resumido por ocorrência).
    Retorna lista de dicts: {"texto": str, "bold": bool}
    """
    todas = list(dados["concluidas"]) + list(dados["abertas"])
    por_usina = {}
    for d in todas:
        por_usina.setdefault(d["usina"], []).append(d)

    paragrafos = []
    for usina in sorted(por_usina.keys()):
        paragrafos.append({"texto": usina, "bold": True})
        for d in por_usina[usina]:
            paragrafos.append({"texto": "• " + _linha_ocorrencia(d), "bold": False})
    return paragrafos


# ── Desligamentos: sempre com data/hora real (aberto via WhatsApp já traz a
# hora do desligamento; ao normalizar, a hora de conclusão) ─────────────────

def _linha_desligamento_runs(d):
    """
    Runs (mistura de negrito/normal na mesma linha) para uma ocorrência de
    desligamento, incluindo data/hora real de abertura e, se concluída, de
    conclusão. Só cai no aviso em negrito se a data realmente estiver
    ausente na origem (caso excepcional — normalmente já vem preenchida
    pela automação do WhatsApp, ou pela Data de Criação/Conclusão da OS).
    """
    equip = d["equipamento"]
    falha = d["falha"]
    status = d["status"]
    concluida = d["concluida"]
    dt_abertura = d["dt_abertura"]
    dt_fecham = d["dt_fechamento"]

    runs = [{"texto": f"• {equip} – {falha}. Status: {status}. ", "bold": False}]

    if dt_abertura:
        runs.append({"texto": f"Desligada em {_fmt_data_hora(dt_abertura)}.", "bold": False})
    else:
        runs.append({"texto": "ACRESCENTAR A DATA E HORA DO DESLIGAMENTO", "bold": True})

    if concluida:
        if dt_fecham:
            runs.append({"texto": f" Religada/concluída em {_fmt_data_hora(dt_fecham)}.", "bold": False})
        else:
            runs.append({"texto": " ACRESCENTAR A DATA E HORA DA CONCLUSÃO", "bold": True})

    return runs


def gerar_paragrafos_desligamentos(dados):
    """Igual a gerar_paragrafos_categoria, mas com data/hora real por ocorrência."""
    todas = list(dados["concluidas"]) + list(dados["abertas"])
    por_usina = {}
    for d in todas:
        por_usina.setdefault(d["usina"], []).append(d)

    paragrafos = []
    for usina in sorted(por_usina.keys()):
        paragrafos.append({"runs": [{"texto": usina, "bold": True}]})
        for d in por_usina[usina]:
            paragrafos.append({"runs": _linha_desligamento_runs(d)})
    return paragrafos


# ── Chamados em aberto: uma linha por chamado, usina em negrito inline ─────

def gerar_paragrafos_chamados(chamados):
    """Uma linha por chamado válido: '- Usina – Case #ticket -> resumo -> status.'
    (chamados vêm sempre do Painel de Falhas — Painel de Atividades não tem
    número de chamado/ticket de fabricante, só número de OS interno)."""
    if not chamados:
        return [{"texto": "Nenhum chamado em aberto nesta semana.", "bold": False}]

    por_usina = {}
    for row in chamados:
        usina = row[COL_USINA].strip() or "Usina não informada"
        por_usina.setdefault(usina, []).append(row)

    paragrafos = []
    for usina in sorted(por_usina.keys()):
        for row in por_usina[usina]:
            ticket = row[COL_TICKET].strip()
            resumo = row[COL_FALHA].strip() or row[COL_CAUSA].strip() or "Sem descrição detalhada"
            status = row[COL_STATUS].strip() or "Em análise"
            runs = [
                {"texto": "- ", "bold": False},
                {"texto": usina, "bold": True},
                {"texto": f" – Case #{ticket} -> {resumo} -> {status}.", "bold": False},
            ]
            paragrafos.append({"runs": runs})
    return paragrafos


# Layout real da aba "Zeladoria" (gid 987654321): linha 1 = título dos grupos,
# linha 2 = subcabeçalhos, dados a partir da linha 3.
# Colunas (0-based): A Cliente, B Usina, depois 4 grupos de 4 colunas cada
# (Última Data, Próxima Data, Quantidade, Status): Roçada, Poda Química, Lavagem dos Módulos, Controle de Pragas.
ZEL_COL_CLIENTE, ZEL_COL_USINA = 0, 1
ZEL_GRUPOS = [
    ("Roçada",               2),
    ("Poda Química",         6),
    ("Lavagem dos Módulos", 10),
    ("Controle de Pragas",  14),
]


def coletar_zeladoria(zeladoria_valores, cliente):
    """
    zeladoria_valores: ws.get_all_values() da aba Zeladoria (linhas 1-2 = cabeçalho).
    Retorna lista de dicts: [{"usina": ..., "grupos": [{"nome":, "status":, "ultima_data":}, ...]}, ...]
    """
    cliente_norm = _norm(cliente)
    resultado = []
    for row in zeladoria_valores[2:]:
        if len(row) <= ZEL_GRUPOS[-1][1] + 3:
            row = row + [""] * (ZEL_GRUPOS[-1][1] + 4 - len(row))
        if not row[ZEL_COL_USINA].strip():
            continue
        if cliente_norm not in _norm(row[ZEL_COL_CLIENTE]):
            continue

        grupos_usina = []
        for nome, col_ini in ZEL_GRUPOS:
            ultima_data = row[col_ini].strip()
            status = row[col_ini + 3].strip()
            grupos_usina.append({"nome": nome, "status": status, "ultima_data": ultima_data})

        resultado.append({"usina": row[ZEL_COL_USINA].strip(), "grupos": grupos_usina})
    return resultado


def _status_zeladoria_para_tabela(item, nome_grupo):
    """Texto de uma célula da tabela de Zeladoria (só o status, conforme
    pedido — sem despejar a última data dentro da célula para não poluir)."""
    grupo = next((g for g in item["grupos"] if g["nome"] == nome_grupo), None)
    if not grupo:
        return "Sem informação"
    return grupo["status"] or "Sem informação"


def paginar_zeladoria(zeladoria_usinas, max_por_pagina=ZELADORIA_MAX_USINAS_POR_PAGINA):
    """Divide as usinas da Zeladoria em páginas de tabela, pra nunca estourar
    o slide (era o problema antigo: texto cortado com muitas usinas)."""
    if not zeladoria_usinas:
        return [[]]
    return [zeladoria_usinas[i:i + max_por_pagina] for i in range(0, len(zeladoria_usinas), max_por_pagina)]


# ── Clonagem de slides (mantém 100% do estilo do modelo Grid Co.) ──────────

def _duplicate_slide(prs, index):
    """Duplica um slide existente do modelo (mesmo layout, cores, fontes, formas)."""
    source = prs.slides[index]
    dest = prs.slides.add_slide(source.slide_layout)

    for shp in list(dest.shapes):
        shp._element.getparent().remove(shp._element)

    id_map = {}
    for rId, rel in source.part.rels.items():
        if "notesSlide" in rel.reltype or "slideLayout" in rel.reltype:
            continue
        new_rid = dest.part.rels._add_relationship(rel.reltype, rel._target, rel.is_external)
        id_map[rId] = new_rid

    for shp in source.shapes:
        newel = copy.deepcopy(shp._element)
        for el in newel.iter():
            for attr in ("embed", "link", "id"):
                full = qn(f"r:{attr}")
                val = el.get(full)
                if val and val in id_map:
                    el.set(full, id_map[val])
        dest.shapes._spTree.append(newel)
    return dest


def _extrair_formato_base(shape):
    """Extrai fonte/tamanho/itálico/cor do 1º run existente na shape (pra reaplicar depois)."""
    tf = shape.text_frame
    primeiro_par = tf.paragraphs[0]
    if not primeiro_par.runs:
        return None
    font = primeiro_par.runs[0].font
    cor_rgb = None
    try:
        if font.color and font.color.type is not None:
            cor_rgb = font.color.rgb  # levanta AttributeError se for cor de tema (schemeClr)
    except AttributeError:
        cor_rgb = None
    return (font.name, font.size, font.italic, cor_rgb)


def _set_text_preservando_estilo(shape, novo_texto):
    """Substitui o texto de um text box mantendo a formatação (incl. negrito) do 1º run."""
    tf = shape.text_frame
    primeiro_par = tf.paragraphs[0]
    bold_original = primeiro_par.runs[0].font.bold if primeiro_par.runs else None
    fmt = _extrair_formato_base(shape)

    tf.clear()
    linhas = novo_texto.split("\n")
    for i, linha in enumerate(linhas):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run()
        run.text = linha
        if fmt:
            if fmt[0]:
                run.font.name = fmt[0]
            if fmt[1]:
                run.font.size = fmt[1]
            if fmt[2] is not None:
                run.font.italic = fmt[2]
            if fmt[3]:
                run.font.color.rgb = fmt[3]
        if bold_original is not None:
            run.font.bold = bold_original


def _set_paragrafos_com_estilo(shape, paragrafos):
    """
    Substitui o conteúdo de um text box por uma lista de parágrafos, permitindo
    alternar negrito por parágrafo (usado para destacar nome de usina/categoria)
    OU, quando o item tiver a chave "runs", misturar negrito/normal dentro da
    MESMA linha (usado em Desligamentos e Chamados em Aberto).

    paragrafos: lista de dicts, em um dos dois formatos:
      - {"texto": str, "bold": bool}                      -> parágrafo simples
      - {"runs": [{"texto": str, "bold": bool}, ...]}      -> parágrafo com runs mistos
    """
    fmt = _extrair_formato_base(shape)
    tf = shape.text_frame
    tf.clear()

    if not paragrafos:
        paragrafos = [{"texto": "", "bold": False}]

    for i, p in enumerate(paragrafos):
        par = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        runs_def = p.get("runs") or [{"texto": p.get("texto", ""), "bold": p.get("bold", False)}]
        for r in runs_def:
            run = par.add_run()
            run.text = r.get("texto", "")
            if fmt:
                if fmt[0]:
                    run.font.name = fmt[0]
                if fmt[1]:
                    run.font.size = fmt[1]
                if fmt[2] is not None:
                    run.font.italic = fmt[2]
                if fmt[3]:
                    run.font.color.rgb = fmt[3]
            run.font.bold = bool(r.get("bold", False))


def _formatar_celula_tabela(celula, fmt, negrito):
    """Aplica a fonte/tamanho extraídos do template (fmt) numa célula de tabela."""
    celula.text_frame.word_wrap = True
    for p in celula.text_frame.paragraphs:
        p.alignment = p.alignment  # no-op, mantém padrão do template de tabela
        if not p.runs:
            p.add_run()
        for r in p.runs:
            if fmt:
                if fmt[0]:
                    r.font.name = fmt[0]
                if fmt[1]:
                    r.font.size = fmt[1]
            r.font.bold = negrito


def _remover_fotos(slide):
    """Remove os placeholders de foto (o gerador não tem fotos de campo)."""
    for shp in list(slide.shapes):
        if shp.shape_type == 13 and (shp.name or "").lower().startswith("imagem"):
            shp._element.getparent().remove(shp._element)


def _find_shape(slide, contem_texto):
    for shp in slide.shapes:
        if shp.has_text_frame and contem_texto.lower() in shp.text_frame.text.lower():
            return shp
    return None


# ── Montagem do PPTX final ──────────────────────────────────────────────────

def _tamanho_paragrafos(paragrafos):
    total = 0
    for p in paragrafos:
        if "runs" in p:
            total += sum(len(r.get("texto", "")) for r in p["runs"])
        else:
            total += len(p.get("texto", ""))
    return total


def agrupar_em_paginas(grupos, orcamento_chars=1400, ordem=None):
    """
    Agrupa categorias em páginas por orçamento de caracteres. Retorna lista de
    páginas, cada página = lista de tuplas (categoria, paragrafos).
    `ordem`, se fornecida, define a sequência das categorias (categorias fora
    dela vão para o final, em ordem alfabética).
    """
    chaves = _ordenar_categorias(grupos.keys()) if ordem is None else ordem
    blocos = [(cat, gerar_paragrafos_categoria(cat, grupos[cat])) for cat in chaves if cat in grupos]
    paginas, atual, atual_len = [], [], 0
    for cat, paragrafos in blocos:
        tam = _tamanho_paragrafos(paragrafos) + len(cat) + 10
        if atual and (atual_len + tam > orcamento_chars):
            paginas.append(atual)
            atual, atual_len = [], 0
        atual.append((cat, paragrafos))
        atual_len += tam
    if atual:
        paginas.append(atual)
    return paginas


def agrupar_desligamentos_em_paginas(dados, orcamento_chars=1400):
    """Pagina os parágrafos (com runs) de Desligamentos por orçamento de caracteres."""
    paragrafos = gerar_paragrafos_desligamentos(dados)
    paginas, atual, atual_len = [], [], 0
    for p in paragrafos:
        tam = _tamanho_paragrafos([p])
        if atual and (atual_len + tam > orcamento_chars):
            paginas.append(atual)
            atual, atual_len = [], 0
        atual.append(p)
        atual_len += tam
    if atual:
        paginas.append(atual)
    return paginas or [[]]


def _renderizar_pagina_categoria(prs, titulo, corpo_paragrafos):
    """Cria um slide de conteúdo (clonado do modelo) com título + corpo em texto."""
    novo = _duplicate_slide(prs, 2)
    _remover_fotos(novo)
    shp_categoria = _find_shape(novo, "DESLIGAMENTOS")
    shp_corpo = _find_shape(novo, "Foram registradas")
    if shp_categoria:
        _set_text_preservando_estilo(shp_categoria, titulo)
    if shp_corpo:
        _set_paragrafos_com_estilo(shp_corpo, corpo_paragrafos)
    return novo


def _renderizar_pagina_zeladoria_tabela(prs, titulo, pagina_usinas):
    """
    Cria um slide de Zeladoria com uma TABELA real (Usina | Roçagem | Poda
    Química | Limpeza dos Módulos), no lugar da antiga lista de bullets —
    o texto corrido não cabia numa única página quando havia muitas usinas.
    """
    novo = _duplicate_slide(prs, 2)
    _remover_fotos(novo)
    shp_categoria = _find_shape(novo, "DESLIGAMENTOS")
    shp_corpo = _find_shape(novo, "Foram registradas")
    if shp_categoria:
        _set_text_preservando_estilo(shp_categoria, titulo)

    if shp_corpo:
        fmt = _extrair_formato_base(shp_corpo)
        left, top, width, height = shp_corpo.left, shp_corpo.top, shp_corpo.width, shp_corpo.height
        shp_corpo._element.getparent().remove(shp_corpo._element)
    else:
        fmt = None
        left = top = width = height = None

    if left is None:
        # fallback conservador, caso o placeholder de corpo não seja encontrado
        from pptx.util import Emu
        left, top, width, height = Emu(700000), Emu(1500000), Emu(11500000), Emu(5500000)

    linhas = len(pagina_usinas) + 1  # +1 cabeçalho
    colunas = len(ZELADORIA_COLUNAS)
    graphic_frame = novo.shapes.add_table(linhas, colunas, left, top, width, height)
    tabela = graphic_frame.table

    for j, titulo_col in enumerate(ZELADORIA_COLUNAS):
        cel = tabela.cell(0, j)
        cel.text = titulo_col
        _formatar_celula_tabela(cel, fmt, negrito=True)

    for i, item in enumerate(pagina_usinas, start=1):
        cel_usina = tabela.cell(i, 0)
        cel_usina.text = item["usina"]
        _formatar_celula_tabela(cel_usina, fmt, negrito=True)
        for j, (_, nome_grupo) in enumerate(ZELADORIA_MAPA_COLUNAS, start=1):
            cel = tabela.cell(i, j)
            cel.text = _status_zeladoria_para_tabela(item, nome_grupo)
            _formatar_celula_tabela(cel, fmt, negrito=False)

    return novo


def gerar_relatorio_pptx(cliente, semana_num, data_label, grupos, chamados=None, zeladoria_usinas=None):
    """
    grupos: dict categoria -> {"concluidas": [dict,...], "abertas": [dict,...]}
            (normalmente o resultado de mesclar_grupos(coletar_ocorrencias_semana(...),
             coletar_atividades_semana(...)) — ocorrências do Painel de Falhas E
             OSs do Painel de Atividades, já combinadas por categoria).
    semana_num: número da semana do ano (baseado na data final do período)
    data_label: data final formatada (dd/mm/aaaa) — mantido por compatibilidade
                de assinatura, não é mais exibido na capa (padrão Grid Co.).
    chamados: lista de linhas (Painel de Falhas) com chamado/ticket válido em aberto (opcional)
    zeladoria_usinas: retorno de coletar_zeladoria() (opcional)
    Retorna BytesIO() pronto para download.
    """
    prs = Presentation(TEMPLATE_PATH)

    grupos = dict(grupos)  # não alterar o dict do chamador
    dados_comunicacoes = grupos.pop(CAT_COMUNICACOES, None)
    dados_desligamentos = grupos.pop(CAT_DESLIGAMENTOS, None)
    outras_categorias = grupos  # Inversores, Strings/Módulos, etc. — ocorrências E OSs

    # --- Slide 1: Capa — só cliente e semana (sem data) ---------------------
    capa = _duplicate_slide(prs, 0)
    shp_titulo = _find_shape(capa, "O&M")
    if shp_titulo:
        _set_text_preservando_estilo(shp_titulo, f"O&M – {cliente}\nSemana {semana_num}")

    # --- Slide 2: Ata da reunião — tópicos padronizados e fixos, nesta ordem
    topicos = []
    if dados_comunicacoes:
        topicos.append(CAT_COMUNICACOES)
    if outras_categorias:
        topicos.append("OCORRÊNCIAS SEMANAIS")
    if dados_desligamentos:
        topicos.append(CAT_DESLIGAMENTOS)
    if chamados:
        topicos.append("CHAMADOS EM ABERTO")
    if zeladoria_usinas:
        topicos.append("ZELADORIA")
    ata = _duplicate_slide(prs, 1)
    shp_lista = _find_shape(ata, "Desligamentos")
    if shp_lista:
        _set_text_preservando_estilo(shp_lista, "\n".join(topicos))

    # --- Página(s): Comunicações / SCADA / CCTV — sempre primeiro, própria(s)
    #     página(s), separada de Inversores/Strings/demais equipamentos -------
    if dados_comunicacoes:
        for pagina in agrupar_em_paginas({CAT_COMUNICACOES: dados_comunicacoes}):
            cat, paragrafos = pagina[0]
            _renderizar_pagina_categoria(prs, cat, paragrafos)

    # --- Página(s): "Ocorrências da Semana" — Inversores, Strings/Módulos e
    #     demais equipamentos, já com Painel de Falhas + Painel de Atividades
    #     combinados por categoria (empacotadas por orçamento de caracteres) --
    for pagina in agrupar_em_paginas(outras_categorias):
        if len(pagina) == 1:
            categoria, paragrafos = pagina[0]
            _renderizar_pagina_categoria(prs, categoria, paragrafos)
        else:
            combinados = []
            for cat, paragrafos in pagina:
                combinados.append({"texto": cat, "bold": True})
                combinados.extend(paragrafos)
            _renderizar_pagina_categoria(prs, "OCORRÊNCIAS DA SEMANA", combinados)

    # --- Página(s): Desligamentos — com data/hora real de abertura/conclusão
    if dados_desligamentos:
        paginas_deslig = agrupar_desligamentos_em_paginas(dados_desligamentos)
        for i, pagina in enumerate(paginas_deslig):
            titulo = CAT_DESLIGAMENTOS if i == 0 else f"{CAT_DESLIGAMENTOS} (cont.)"
            _renderizar_pagina_categoria(prs, titulo, pagina)

    # --- Página: Chamados em aberto (só com ticket/case válido) ------------
    if chamados:
        _renderizar_pagina_categoria(prs, "CHAMADOS EM ABERTO", gerar_paragrafos_chamados(chamados))

    # --- Página(s): Zeladoria — tabela real (Usina | Roçagem | Poda Química |
    #     Limpeza dos Módulos), paginada para nunca estourar o slide ---------
    if zeladoria_usinas:
        for i, pagina in enumerate(paginar_zeladoria(zeladoria_usinas)):
            titulo = "ZELADORIA" if i == 0 else "ZELADORIA (cont.)"
            _renderizar_pagina_zeladoria_tabela(prs, titulo, pagina)

    # --- Reordena o deck: capa nova -> ata nova -> conteúdo -> contato -----
    xml_slides = prs.slides._sldIdLst
    todos_els = list(xml_slides)
    contato_el = todos_els[6]
    capa_el = todos_els[7]
    ata_el = todos_els[8]
    conteudo_els = todos_els[9:]

    for e in todos_els:
        xml_slides.remove(e)
    for e in [capa_el, ata_el] + conteudo_els + [contato_el]:
        xml_slides.append(e)

    buf = BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf
