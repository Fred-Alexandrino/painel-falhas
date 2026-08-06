# -*- coding: utf-8 -*-
"""
relatorio_handover.py
─────────────────────────────────────────────────────────────────────────
Geração do "Relatório de Handover" (.pdf) — fechamento de uma OS avulsa
pro cliente — no padrão visual REAL da Grid Co. (fundo branco, wordmark
"Grid Co." em verde no topo-esquerdo, título do documento + nº de página
no topo-direito, títulos de seção numerados em negrito preto), o mesmo
padrão usado no Relatório de Handover EPC->O&M (usina inteira).

Todos os campos objetivos (cliente, usina, equipamento, datas, status,
histórico) vêm direto dos dados da OS — determinístico, nunca inventado.
O único trecho opcional gerado por IA é o resumo executivo (texto corrido
formal), passado já pronto via `resumo_ia`; se vier vazio, a seção é
simplesmente omitida.
"""
import re
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle

# ── Paleta (identidade visual real da Grid Co., extraída do modelo oficial) ──
NAVY = HexColor("#191528")
LIME = HexColor("#A9DB21")
VERDE = HexColor("#2E7D32")
BORDA = HexColor("#D9D9E0")

TITULO_DOC = "Relatório de Handover – Fechamento de Ordem de Serviço – Grid Co. – Rev.00"

STATUS_LABEL = {
    "em processo": "Em Execução",
    "em revisão": "Em Verificação",
    "em revisao": "Em Verificação",
    "finalizada": "Concluída",
    "cancelada": "Cancelada",
}

_styles = getSampleStyleSheet()
ESTILO_CORPO = ParagraphStyle(
    "corpo", parent=_styles["Normal"], fontName="Helvetica", fontSize=10.5,
    leading=15, alignment=TA_JUSTIFY, spaceAfter=8,
)
ESTILO_TITULO_SECAO = ParagraphStyle(
    "tituloSecao", parent=_styles["Normal"], fontName="Helvetica-Bold",
    fontSize=12.5, leading=16, textColor=NAVY, spaceBefore=14, spaceAfter=8,
)
ESTILO_CAMPO = ParagraphStyle(
    "campo", parent=_styles["Normal"], fontName="Helvetica", fontSize=10.5,
    leading=15, spaceAfter=3,
)
ESTILO_BULLET = ParagraphStyle(
    "bullet", parent=ESTILO_CORPO, leftIndent=14, spaceAfter=4,
)
ESTILO_BULLET_ITALIC = ParagraphStyle(
    "bulletItalic", parent=ESTILO_BULLET, fontName="Helvetica-Oblique",
)


def _cabecalho_rodape(cx, doc):
    """Mesmo cabeçalho do Relatório de Handover EPC->O&M: wordmark 'Grid Co.'
    em verde no topo-esquerdo, título do documento + nº de página no
    topo-direito, linha divisória."""
    cx.saveState()
    largura, altura = doc.pagesize
    y_topo = altura - 1.6 * cm

    cx.setFont("Helvetica-Bold", 13)
    cx.setFillColor(LIME)
    cx.drawString(1.9 * cm, y_topo, "Grid Co.")

    cx.setFont("Helvetica-Bold", 8.5)
    cx.setFillColor(black)
    cx.drawRightString(largura - 1.9 * cm, y_topo + 4, TITULO_DOC)

    cx.setFont("Helvetica-Bold", 9)
    cx.drawRightString(largura - 1.9 * cm, y_topo - 12, f"Página {doc.page}")

    cx.setStrokeColor(BORDA)
    cx.line(1.9 * cm, y_topo - 20, largura - 1.9 * cm, y_topo - 20)
    cx.restoreState()


def _tabela_campos(pares, larguras=(6 * cm, 10.5 * cm)):
    linhas = []
    for rotulo, valor in pares:
        linhas.append([Paragraph(f"<b>{rotulo}</b>", ESTILO_CAMPO),
                        Paragraph(valor or "—", ESTILO_CAMPO)])
    tb = Table(linhas, colWidths=list(larguras))
    tb.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    return tb


def _limpar_historico(historico_raw, max_linhas=15):
    """Quebra o histórico bruto (texto livre, uma entrada por linha) em
    linhas legíveis, sem alterar o conteúdo original de cada entrada."""
    if not historico_raw:
        return []
    linhas = [l.strip(" -•\t") for l in re.split(r"[\r\n]+", historico_raw) if l.strip()]
    return linhas[:max_linhas]


def gerar_handover_docx(atividade, resumo_ia=""):
    """
    Monta o Relatório de Handover (.pdf) no padrão visual real da Grid Co.
    a partir de uma atividade (dict, chaves = ATIV_HEADERS_JSON do app.py).

    (Nome da função mantido como `gerar_handover_docx` por compatibilidade
    com a rota existente em app.py — o retorno agora é um PDF, não DOCX.)

    `resumo_ia`: texto do resumo executivo já gerado (opcional). Se vazio,
    a seção "Resumo Executivo" é omitida — o relatório nunca falha por
    causa da IA.

    Retorna um BytesIO posicionado no início, pronto para send_file.
    """
    cliente = (atividade.get("cliente") or "").strip()
    usina = (atividade.get("usina") or "").strip()
    numero_os = (atividade.get("numeroOS") or "").strip()
    equipamento = (atividade.get("equipamento") or "").strip()
    descricao = (atividade.get("descricao") or "").strip()
    responsavel = (atividade.get("responsavel") or "").strip()
    status_raw = (atividade.get("statusOS") or atividade.get("status") or "").strip()
    status_label = STATUS_LABEL.get(status_raw.lower(), status_raw or "—")
    data_criacao = (atividade.get("dataCriacao") or "").strip()
    data_conclusao = (atividade.get("dataConclusao") or "").strip()
    observacoes = (atividade.get("observacoesOS") or "").strip()
    historico_raw = atividade.get("historico") or ""

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             topMargin=2.6 * cm, bottomMargin=1.8 * cm,
                             leftMargin=1.9 * cm, rightMargin=1.9 * cm)
    story = []

    subtitulo = " — ".join([p for p in [cliente, usina] if p])
    if subtitulo:
        story.append(Paragraph(subtitulo, ParagraphStyle(
            "subCapa", parent=_styles["Normal"], fontName="Helvetica",
            fontSize=10, textColor=HexColor("#504C63"), spaceAfter=2)))
    story.append(Paragraph("Relatório de Handover", ParagraphStyle(
        "tituloCapa", parent=_styles["Normal"], fontName="Helvetica-Bold",
        fontSize=18, textColor=NAVY, spaceAfter=16)))

    story.append(Paragraph("1. Dados Gerais", ESTILO_TITULO_SECAO))
    story.append(_tabela_campos([
        ("Cliente:", cliente),
        ("Usina:", usina),
        ("Equipamento:", equipamento),
        ("Nº da OS:", numero_os),
        ("Responsável (Grid Co.):", responsavel),
        ("Status:", status_label),
        ("Data de abertura:", data_criacao),
        ("Data de conclusão:", data_conclusao),
    ]))

    story.append(Paragraph("2. Descrição do Serviço", ESTILO_TITULO_SECAO))
    story.append(Paragraph(descricao or "Sem descrição registrada.", ESTILO_CORPO))

    proxima_secao = 3
    if resumo_ia:
        story.append(Paragraph(f"{proxima_secao}. Resumo Executivo", ESTILO_TITULO_SECAO))
        for paragrafo in resumo_ia.split("\n\n"):
            if paragrafo.strip():
                story.append(Paragraph(paragrafo.strip(), ESTILO_CORPO))
        proxima_secao += 1

    story.append(Paragraph(f"{proxima_secao}. Histórico de Execução", ESTILO_TITULO_SECAO))
    linhas_hist = _limpar_historico(historico_raw)
    if linhas_hist:
        for linha in linhas_hist:
            story.append(Paragraph("• " + linha, ESTILO_BULLET))
    else:
        story.append(Paragraph("Sem histórico detalhado registrado.", ESTILO_BULLET_ITALIC))
    proxima_secao += 1

    if observacoes:
        story.append(Paragraph(f"{proxima_secao}. Observações", ESTILO_TITULO_SECAO))
        story.append(Paragraph(observacoes, ESTILO_CORPO))

    doc.build(story, onFirstPage=lambda c, d: _cabecalho_rodape(c, d),
               onLaterPages=lambda c, d: _cabecalho_rodape(c, d))
    buf.seek(0)
    return buf
