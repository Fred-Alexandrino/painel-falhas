# -*- coding: utf-8 -*-
"""
relatorio_handover_usina.py
─────────────────────────────────────────────────────────────────────────
Geração do "Relatório de Handover — Operação de Ativos" (EPC → O&M),
no modelo Grid Co., a partir dos dados preenchidos no dashboard + OSs
selecionadas do Painel de Atividades + o PDF exportado da própria
Fracttal (anexado manualmente pelo usuário, contendo o checklist de
subtarefas e fotos de cada Ordem de Trabalho de handover).

Este módulo gera DUAS partes em PDF (retrato e paisagem) via reportlab
e faz o merge final com o PDF da Fracttal no meio, reproduzindo a
estrutura do modelo de referência:

  Parte 1 (retrato): Capa · 1.Objetivo · 2.Informações Gerais ·
                      3.Etapas do Handover (3.1 a 3.3 + lista de
                      "Handover – <equipamento>") · título da 3.4
  [ PDF da Fracttal anexado pelo usuário — Ordens de Serviço - Handover ]
  Parte 2 (retrato):  4.Capacitação da Equipe · 5.Conclusão · 5.1 Anexos ·
                      6.Quadro de Revisões
  Parte 3 (paisagem): Punch List

Nenhum texto de análise/diagnóstico é inventado pela IA aqui — todos os
campos vêm do que o usuário preencheu no formulário do dashboard.
"""
from io import BytesIO

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, PageBreak)
from reportlab.pdfgen import canvas as pdfcanvas

from pypdf import PdfReader, PdfWriter

# ── Paleta (identidade visual Grid Co.) ─────────────────────────────────
NAVY = HexColor("#191528")
CINZA = HexColor("#504C63")
LIME = HexColor("#A9DB21")
VERDE = HexColor("#2E7D32")
BORDA = HexColor("#D9D9E0")
ZEBRA = HexColor("#F0F1F4")

TITULO_DOC = "Relatório de Handover – Operação de Ativos – Grid Co. – Rev.00"

_styles = getSampleStyleSheet()
ESTILO_CORPO = ParagraphStyle(
    "corpo", parent=_styles["Normal"], fontName="Helvetica", fontSize=10.5,
    leading=15, alignment=TA_JUSTIFY, spaceAfter=8,
)
ESTILO_TITULO_SECAO = ParagraphStyle(
    "tituloSecao", parent=_styles["Normal"], fontName="Helvetica-Bold",
    fontSize=12.5, leading=16, textColor=NAVY, spaceBefore=14, spaceAfter=8,
)
ESTILO_SUBTITULO = ParagraphStyle(
    "subtituloSecao", parent=_styles["Normal"], fontName="Helvetica-Bold",
    fontSize=11, leading=14, textColor=NAVY, spaceBefore=10, spaceAfter=6,
)
ESTILO_CAMPO = ParagraphStyle(
    "campo", parent=_styles["Normal"], fontName="Helvetica", fontSize=10.5,
    leading=15, spaceAfter=3,
)
ESTILO_BULLET = ParagraphStyle(
    "bullet", parent=ESTILO_CORPO, alignment=0, leftIndent=14, spaceAfter=4,
)


def _cabecalho_rodape(cx, doc):
    """Desenha cabeçalho (logo + título do documento + nº de página) em
    cada página de conteúdo — reproduz o padrão do modelo de referência."""
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


def _gerar_capa(dados):
    buf = BytesIO()
    cx = pdfcanvas.Canvas(buf, pagesize=A4)
    largura, altura = A4
    cx.setFillColor(NAVY)
    cx.rect(0, 0, largura, altura, fill=1, stroke=0)

    cx.setFont("Helvetica-Bold", 20)
    cx.setFillColor(white)
    cx.drawCentredString(largura / 2, altura - 4 * cm, "Grid Co.")

    subtitulo = f"UFV {dados.get('usina','').upper()} – {dados.get('cliente','').upper()}"
    cx.setFont("Helvetica-Bold", 14)
    cx.drawCentredString(largura / 2, altura / 2 + 1.4 * cm, subtitulo)

    cx.setFont("Helvetica-Bold", 18)
    cx.drawCentredString(largura / 2, altura / 2 - 0.6 * cm, "Relatório de Handover")
    cx.setFont("Helvetica-Bold", 14)
    cx.drawCentredString(largura / 2, altura / 2 - 1.3 * cm, "Operação de Ativos")

    cx.setFont("Helvetica-Bold", 12)
    cx.drawCentredString(largura / 2, altura / 2 - 2.6 * cm, "Grid Co.")

    cx.setFillColor(LIME)
    cx.setFont("Helvetica-Bold", 12)
    cx.drawCentredString(largura / 2, 3 * cm, "Viemos transformar")
    cx.drawCentredString(largura / 2, 3 * cm - 0.6 * cm, "o futuro da energia.")

    cx.showPage()
    cx.save()
    buf.seek(0)
    return buf


def _gerar_parte1(dados):
    """Capa + 1.Objetivo + 2.Informações Gerais + 3.Etapas (3.1–3.3 + lista)
    + título da 3.4 (o conteúdo real da 3.4 vem do PDF da Fracttal anexado)."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             topMargin=2.6 * cm, bottomMargin=1.8 * cm,
                             leftMargin=1.9 * cm, rightMargin=1.9 * cm)
    story = []

    cliente = dados.get("cliente", "")
    usina = dados.get("usina", "")
    data_inicio = dados.get("dataInicio", "")
    data_fim = dados.get("dataFim", "")
    equipe = dados.get("equipe", [])
    planejamento = dados.get("planejamento", "")
    documentacao_entregue = dados.get("documentacaoEntregue", "")
    equipamentos = dados.get("equipamentosHandover", [])  # lista de strings

    story.append(Paragraph("1. OBJETIVO", ESTILO_TITULO_SECAO))
    objetivo_txt = (
        f"Este relatório tem como objetivo documentar o processo de handover da usina "
        f"fotovoltaica {cliente.upper()} – {usina.upper()} da equipe de Construção/EPC para "
        f"a equipe de Operação e Manutenção (O&amp;M). O handover foi realizado com o objetivo "
        f"de garantir a transferência de responsabilidades de maneira eficiente, segura e "
        f"devidamente documentada, assegurando que a usina esteja em condições plenas de "
        f"operação."
    )
    story.append(Paragraph(objetivo_txt, ESTILO_CORPO))

    story.append(Paragraph("2. INFORMAÇÕES GERAIS", ESTILO_TITULO_SECAO))
    story.append(_tabela_campos([
        ("Nome da Usina:", usina),
        ("Cliente:", cliente),
        ("Data de Início do Handover:", data_inicio),
        ("Data de Conclusão do Handover:", data_fim),
    ]))
    story.append(Paragraph("<b>Partes Envolvidas:</b>", ESTILO_CAMPO))
    story.append(Paragraph("<b>Equipe de O&amp;M: Grid Co.:</b>", ESTILO_BULLET))
    for pessoa in equipe:
        if pessoa.strip():
            story.append(Paragraph(pessoa.strip(), ESTILO_BULLET))

    story.append(Paragraph("3. ETAPAS DO HANDOVER", ESTILO_TITULO_SECAO))
    story.append(Paragraph("3.1. Planejamento", ESTILO_SUBTITULO))
    story.append(Paragraph(planejamento or "—", ESTILO_CORPO))

    story.append(Paragraph("3.2. Documentação Entregue", ESTILO_SUBTITULO))
    story.append(Paragraph(documentacao_entregue or "—", ESTILO_CORPO))

    story.append(Paragraph("3.3. Inspeções e Testes", ESTILO_SUBTITULO))
    story.append(Paragraph(
        "Foram realizadas inspeções físicas, funcionais e termográficas em todos os "
        "sistemas e componentes principais da UFV, com o objetivo de verificar a "
        "integridade, o desempenho operacional e eventuais falhas que possam comprometer "
        "a segurança ou a eficiência da planta. Os resultados são apresentados nos tópicos "
        "a seguir.", ESTILO_CORPO))
    for eq in equipamentos:
        story.append(Paragraph(f"Handover – {eq}", ESTILO_BULLET))

    story.append(Paragraph("3.4. Ordens de Serviço - Handover", ESTILO_SUBTITULO))
    story.append(Paragraph(
        "As Ordens de Serviço de handover de cada sistema/equipamento — com o checklist "
        "de subtarefas e evidências fotográficas — estão anexadas nas páginas a seguir "
        "(exportação direta da Fracttal).", ESTILO_CORPO))

    doc.build(story, onFirstPage=lambda c, d: _cabecalho_rodape(c, d),
               onLaterPages=lambda c, d: _cabecalho_rodape(c, d))
    buf.seek(0)
    return buf


def _gerar_parte2(dados):
    """4.Capacitação · 5.Conclusão · 5.1 Anexos · 6.Quadro de Revisões."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             topMargin=2.6 * cm, bottomMargin=1.8 * cm,
                             leftMargin=1.9 * cm, rightMargin=1.9 * cm)
    story = []

    capacitacao = dados.get("capacitacao", [])
    conclusao = dados.get("conclusao", "")
    revisao = dados.get("revisao", {})

    story.append(Paragraph("4. CAPACITAÇÃO DA EQUIPE DE O&amp;M", ESTILO_TITULO_SECAO))
    story.append(Paragraph(
        "A equipe de O&amp;M participou de treinamentos abrangentes para compreender o "
        "funcionamento da usina, bem como as boas práticas de operação e manutenção. Os "
        "tópicos abordados incluíram:", ESTILO_CORPO))
    for topico in capacitacao:
        if topico.strip():
            story.append(Paragraph(topico.strip(), ESTILO_BULLET))

    story.append(Paragraph("5. CONCLUSÃO", ESTILO_TITULO_SECAO))
    story.append(Paragraph(conclusao or "—", ESTILO_CORPO))

    story.append(Paragraph("5.1. Anexos", ESTILO_SUBTITULO))
    story.append(Paragraph("Punch list da usina", ESTILO_BULLET))

    story.append(Paragraph("6. QUADRO DE REVISÕES", ESTILO_TITULO_SECAO))
    linhas = [["Revisão", "Edição", "Elaborador", "Verificador", "Aprovador", "Data"],
              [revisao.get("revisao", "00"), revisao.get("edicao", "Emissão inicial"),
               revisao.get("elaborador", "—"), revisao.get("verificador", "—"),
               revisao.get("aprovador", "—"), revisao.get("data", "—")]]
    tb = Table(linhas, colWidths=[2 * cm, 3.4 * cm, 3.2 * cm, 3.2 * cm, 3.2 * cm, 2.5 * cm])
    tb.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDA),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(tb)

    doc.build(story, onFirstPage=lambda c, d: _cabecalho_rodape(c, d),
               onLaterPages=lambda c, d: _cabecalho_rodape(c, d))
    buf.seek(0)
    return buf


def _gerar_punch_list(dados):
    """Tabela de Punch List em página paisagem, mesmo padrão do modelo
    de referência (cabeçalho verde, zebra, colunas: Cliente, Usina,
    Cluster, Ativo, Criticidade, Status, Anormalidade, Recomendações,
    Responsável)."""
    buf = BytesIO()
    pagesize = landscape(A4)
    doc = SimpleDocTemplate(buf, pagesize=pagesize,
                             topMargin=1.6 * cm, bottomMargin=1.6 * cm,
                             leftMargin=1.4 * cm, rightMargin=1.4 * cm)
    story = []

    cabecalho = ["CLIENTE", "USINA", "CLUSTER", "ATIVO", "CRITICIDADE",
                 "STATUS", "ANORMALIDADE", "RECOMENDAÇÕES", "RESPONSÁVEL"]
    estilo_cel = ParagraphStyle("cel", fontName="Helvetica", fontSize=8, leading=10)
    estilo_cel_head = ParagraphStyle("celHead", fontName="Helvetica-Bold", fontSize=8.5,
                                      leading=10, textColor=white, alignment=TA_CENTER)

    linhas = [[Paragraph(h, estilo_cel_head) for h in cabecalho]]
    for item in dados.get("punchList", []):
        linhas.append([
            Paragraph(item.get("cliente", "") or "—", estilo_cel),
            Paragraph(item.get("usina", "") or "—", estilo_cel),
            Paragraph(item.get("cluster", "") or "—", estilo_cel),
            Paragraph(item.get("ativo", "") or "—", estilo_cel),
            Paragraph(item.get("criticidade", "") or "—", estilo_cel),
            Paragraph(item.get("status", "") or "—", estilo_cel),
            Paragraph(item.get("anormalidade", "") or "—", estilo_cel),
            Paragraph(item.get("recomendacoes", "") or "—", estilo_cel),
            Paragraph(item.get("responsavel", "") or "—", estilo_cel),
        ])

    larguras = [2.2 * cm, 2.6 * cm, 2.2 * cm, 2.4 * cm, 2.4 * cm, 2 * cm,
                5 * cm, 5 * cm, 3 * cm]
    tb = Table(linhas, colWidths=larguras, repeatRows=1)
    estilo_tabela = [
        ("BACKGROUND", (0, 0), (-1, 0), VERDE),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(linhas)):
        if i % 2 == 0:
            estilo_tabela.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    tb.setStyle(TableStyle(estilo_tabela))
    story.append(tb)

    doc.build(story)
    buf.seek(0)
    return buf


def montar_relatorio_handover_usina(dados, fracttal_pdf_bytes=None):
    """
    Monta o Relatório de Handover completo (PDF), fazendo o merge:
    Capa+Parte1 -> [PDF da Fracttal, se enviado] -> Parte2 -> Punch List.

    `dados`: dict com os campos do formulário (ver _gerar_parte1/2/punch).
    `fracttal_pdf_bytes`: bytes do PDF exportado da Fracttal (seção 3.4),
    opcional — se None, essa seção fica só com o título (sem o anexo).

    Retorna um BytesIO posicionado no início, pronto para send_file.
    """
    writer = PdfWriter()

    for buf in (_gerar_capa(dados), _gerar_parte1(dados)):
        reader = PdfReader(buf)
        for page in reader.pages:
            writer.add_page(page)

    if fracttal_pdf_bytes:
        reader_fracttal = PdfReader(BytesIO(fracttal_pdf_bytes))
        for page in reader_fracttal.pages:
            writer.add_page(page)

    for buf in (_gerar_parte2(dados), _gerar_punch_list(dados)):
        reader = PdfReader(buf)
        for page in reader.pages:
            writer.add_page(page)

    saida = BytesIO()
    writer.write(saida)
    saida.seek(0)
    return saida
