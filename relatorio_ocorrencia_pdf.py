# -*- coding: utf-8 -*-
"""
relatorio_ocorrencia_pdf.py — Relatório de Ocorrência (PDF) no padrão visual Grid Co.
─────────────────────────────────────────────────────────────────────────────────────
18/11/2026: modelo definido pelo Fred (Relatorio_Ocorrencia_Acesso_UFV_Tupi_Paulista_I.pdf,
mesma identidade da skill grid-relatorio-ocorrencia). Capa azul-marinho com a usina em
verde, barras de seção azuis com acento verde, caixa de enquadramento verde-clara,
cronologia com linha verde (itens críticos em vermelho), tabelas com cabeçalho azul,
evidências uma foto por página e fecho com assinatura.

Entrada: `dados` (dict) — ver ESQUEMA abaixo — e `fotos` (lista de bytes de imagem, na
ordem dos índices usados em dados["evidencias"][i]["foto"]).

Numeração das seções é automática (seções vazias saem do documento).
Paginação: barra de seção sempre junto do primeiro bloco; itens de cronologia e linhas
de tabela não quebram; conclusão + local/data + assinatura formam um bloco único.

ESQUEMA (todos os textos em português, sem markdown):
{
  "rotulo": "RELATÓRIO DE OCORRÊNCIA",          # ou "RELATÓRIO DE ATIVIDADE"
  "titulo": "Condição Crítica do Acesso",        # capa (linha 1, termina com " —")
  "tituloCurto": "Condição crítica da via de acesso",   # cabeçalho das páginas
  "usina": "UFV Tupi Paulista I", "cidadeUf": "Tupi Paulista/SP", "cliente": "2C Energia",
  "tema": "Via de acesso à usina e segurança da equipe de O&M",      # linha pequena em caixa alta
  "destaque": "Erosão avançada no trecho de aclive e ...",          # linha em negrito
  "referencia": "Vistoria de campo em 16/11/2026",                  # linha verde
  "dataRelatorio": "17/11/2026", "responsavelTecnico": "Fred Alexandrino — Supervisor de O&M",
  "revisao": "01",
  "objetivo": "...", "enquadramento": {"rotulo": "ENQUADRAMENTO", "texto": "..."},
  "cronologia": [{"quando": "16/11 17h23", "texto": "...", "critico": true}],
  "tituloCondicoes": "CONDIÇÕES E RISCOS IDENTIFICADOS",
  "condicoes": [{"item": "...", "situacao": "...", "criticidade": "Crítico|Alto|Médio|Baixo|OK"}],
  "acoes": [{"data": "16/11/2026", "acao": "...", "status": "Concluído|Em andamento|Aguardando cliente|Pendente"}],
  "evidencias": [{"foto": 0, "legenda": "...", "data": "16/11/2026, 17h23", "registro": "João Salles"}],
  "tituloAtencao": "PONTO DE ATENÇÃO — RECUPERAÇÃO DO ACESSO",
  "atencao": {"resumo": "...", "paragrafos": ["..."], "itens": ["..."], "fecho": ["..."]},
  "conclusao": ["...", "..."],
  "local": "Tupi Paulista (SP)",
  "assinatura": {"nome": "Fred Alexandrino", "cargo": "Supervisor de O&M — Grid Co."}
}
"""
import io
import os
import re
from datetime import datetime

from PIL import Image, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Flowable, Frame, Image as RLImage, KeepTogether,
                                NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets_relatorio")

NAVY = colors.HexColor("#1B1F3C")
VERDE = colors.HexColor("#8DC73F")
VERDE_CLARO = colors.HexColor("#EEF5E1")
VERMELHO = colors.HexColor("#C62828")
VERMELHO_CLARO = colors.HexColor("#FDE8E8")
AZUL = colors.HexColor("#1565C0")
AMBAR = colors.HexColor("#C77700")
CINZA = colors.HexColor("#6B7280")
CINZA_LINHA = colors.HexColor("#D9DCE3")
CINZA_FUNDO = colors.HexColor("#F3F4F7")
TEXTO = colors.HexColor("#1F2330")

_fontes_ok = False


def _fontes():
    global _fontes_ok
    if _fontes_ok:
        return
    for nome, arq in (("Carlito", "Carlito-Regular.ttf"), ("Carlito-Bold", "Carlito-Bold.ttf"),
                      ("Carlito-Italic", "Carlito-Italic.ttf")):
        pdfmetrics.registerFont(TTFont(nome, os.path.join(ASSETS, arq)))
    pdfmetrics.registerFontFamily("Carlito", normal="Carlito", bold="Carlito-Bold", italic="Carlito-Italic",
                                  boldItalic="Carlito-Bold")
    _fontes_ok = True


def _e(s):
    """Escapa texto para o mini-HTML do Paragraph."""
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
         "outubro", "novembro", "dezembro"]


def _data_extenso(d):
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(d or ""))
    if not m:
        return str(d or "")
    return f"{int(m.group(1))} de {MESES[int(m.group(2)) - 1]} de {m.group(3)}"


def _mes_ano(d):
    m = re.match(r"\d{1,2}/(\d{1,2})/(\d{4})", str(d or ""))
    return f"{MESES[int(m.group(1)) - 1]} {m.group(2)}" if m else ""


# ── estilos ──
def _estilos():
    _fontes()
    base = dict(fontName="Carlito", textColor=TEXTO)
    return {
        "corpo": ParagraphStyle("corpo", fontSize=9.5, leading=13.2, alignment=TA_JUSTIFY, spaceAfter=6, **base),
        "callout": ParagraphStyle("callout", fontSize=9.2, leading=12.6, alignment=TA_LEFT, **base),
        "barra": ParagraphStyle("barra", fontName="Carlito-Bold", fontSize=9.6, leading=12, textColor=colors.white),
        "cel": ParagraphStyle("cel", fontSize=8.4, leading=10.6, **base),
        "celb": ParagraphStyle("celb", fontName="Carlito-Bold", fontSize=8.4, leading=10.6, textColor=TEXTO),
        "cab": ParagraphStyle("cab", fontName="Carlito-Bold", fontSize=8.4, leading=10.6, textColor=colors.white),
        "quando": ParagraphStyle("quando", fontName="Carlito-Bold", fontSize=8.4, leading=11, alignment=TA_RIGHT, textColor=NAVY),
        "quandoC": ParagraphStyle("quandoC", fontName="Carlito-Bold", fontSize=8.4, leading=11, alignment=TA_RIGHT, textColor=VERMELHO),
        "crono": ParagraphStyle("crono", fontSize=9.2, leading=12.4, alignment=TA_JUSTIFY, **base),
        "legenda": ParagraphStyle("legenda", fontName="Carlito-Bold", fontSize=9.4, leading=12, alignment=TA_CENTER, textColor=TEXTO),
        "legsub": ParagraphStyle("legsub", fontSize=8, leading=10, alignment=TA_CENTER, textColor=CINZA),
        "item": ParagraphStyle("item", fontSize=9.5, leading=13, leftIndent=14, bulletIndent=4, **base),
        "assin": ParagraphStyle("assin", fontName="Carlito-Bold", fontSize=9.5, leading=12, textColor=TEXTO),
        "assin2": ParagraphStyle("assin2", fontSize=8.4, leading=11, textColor=TEXTO),
    }


class _Barra(Flowable):
    """Barra de seção: fundo azul-marinho, acento verde à esquerda, texto branco em caixa alta."""

    def __init__(self, texto, st):
        super().__init__()
        self.p = Paragraph(_e(texto).upper(), st["barra"])

    def wrap(self, aw, ah):
        self.aw = aw
        _, h = self.p.wrap(aw - 16, ah)
        self.h = max(h + 10, 22)
        return aw, self.h

    def draw(self):
        c = self.canv
        c.setFillColor(NAVY)
        c.rect(0, 0, self.aw, self.h, stroke=0, fill=1)
        c.setFillColor(VERDE)
        c.rect(0, 0, 4, self.h, stroke=0, fill=1)
        _, h = self.p.wrap(self.aw - 16, self.h)
        self.p.drawOn(c, 11, (self.h - h) / 2 + 0.5)


def _callout(rotulo, texto, st, largura):
    p = Paragraph((f"<b>{_e(rotulo)}</b> — " if rotulo else "") + _e(texto), st["callout"])
    t = Table([[p]], colWidths=[largura])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), VERDE_CLARO),
        ("LINEBEFORE", (0, 0), (0, -1), 3, VERDE),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


COR_CRIT = {"crítico": VERMELHO, "critico": VERMELHO, "alto": VERMELHO, "médio": AMBAR, "medio": AMBAR,
            "baixo": colors.HexColor("#2E7D32"), "ok": colors.HexColor("#2E7D32"), "normal": colors.HexColor("#2E7D32")}
COR_STATUS = [("conclu", colors.HexColor("#2E7D32")), ("recuper", colors.HexColor("#2E7D32")),
              ("andamento", AZUL), ("aguard", AMBAR), ("pendente", AMBAR), ("substitu", VERMELHO), ("falh", VERMELHO)]


def _cor_status(s):
    s = str(s or "").lower()
    for k, c in COR_STATUS:
        if k in s:
            return c
    return TEXTO


def _tabela(cab, linhas, larguras, st, col_cor=None, cor_fn=None, centro=None):
    dados = [[Paragraph(_e(c), st["cab"]) for c in cab]]
    for ln in linhas:
        row = []
        for j, v in enumerate(ln):
            if col_cor is not None and j == col_cor:
                cor = cor_fn(v)
                row.append(Paragraph(f'<font color="{cor.hexval()}"><b>{_e(v)}</b></font>',
                                     ParagraphStyle("c", parent=st["cel"], alignment=TA_CENTER)))
            else:
                row.append(Paragraph(_e(v), st["cel"]))
        dados.append(row)
    t = Table(dados, colWidths=larguras, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, CINZA_LINHA),
    ]
    for i in range(1, len(dados)):
        if i % 2 == 0:
            estilo.append(("BACKGROUND", (0, i), (-1, i), CINZA_FUNDO))
    t.setStyle(TableStyle(estilo))
    return t


def _cronologia(itens, st, largura):
    linhas, estilo = [], [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEAFTER", (0, 0), (0, -1), 2, VERDE),
        ("LEFTPADDING", (0, 0), (0, -1), 0), ("RIGHTPADDING", (0, 0), (0, -1), 7),
        ("LEFTPADDING", (1, 0), (1, -1), 8), ("RIGHTPADDING", (1, 0), (1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i, it in enumerate(itens):
        crit = bool(it.get("critico"))
        linhas.append([Paragraph(_e(it.get("quando")), st["quandoC" if crit else "quando"]),
                       Paragraph(_e(it.get("texto")), st["crono"])])
        if crit:
            estilo.append(("BACKGROUND", (1, i), (1, i), VERMELHO_CLARO))
    t = Table(linhas, colWidths=[24 * mm, largura - 24 * mm])
    t.setStyle(TableStyle(estilo))
    return t


def _foto_flowable(dados_img, larg_max, alt_max):
    im = Image.open(io.BytesIO(dados_img))
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((1600, 1600))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=82)
    b.seek(0)
    w, h = im.size
    k = min(larg_max / w, alt_max / h)
    return RLImage(b, width=w * k, height=h * k)


def gerar_pdf(dados, fotos=None):
    """Monta o PDF e devolve os bytes."""
    st = _estilos()
    fotos = fotos or []
    d = dict(dados or {})
    usina = d.get("usina") or "UFV"
    titulo_curto = d.get("tituloCurto") or d.get("titulo") or ""
    rotulo = (d.get("rotulo") or "RELATÓRIO DE OCORRÊNCIA").upper()
    data_rel = d.get("dataRelatorio") or datetime.now().strftime("%d/%m/%Y")
    rodape = f"Grid Co.   |   {usina}   |   Revisão {d.get('revisao') or '01'}   |   {_mes_ano(data_rel)}"
    logo = ImageReader(os.path.join(ASSETS, "logo_gridco.png"))
    capa_bg = ImageReader(os.path.join(ASSETS, "capa_fundo.jpg"))
    W, H = A4
    ML, MR, MT, MB = 22 * mm, 22 * mm, 28 * mm, 22 * mm
    largura = W - ML - MR

    def capa(c, doc):
        c.saveState()
        c.drawImage(capa_bg, 0, 0, W, H)
        x = 25 * mm
        c.drawImage(logo, x, H - 32 * mm, 9 * mm, 9 * mm, mask="auto")
        c.setFillColor(colors.white)
        c.setFont("Carlito-Bold", 15)
        c.drawString(x + 11 * mm, H - 29.5 * mm, "Grid Co.")
        c.setFillColor(VERDE)
        c.setFont("Carlito", 7.5)
        c.drawString(x + 11 * mm, H - 33.5 * mm, "Operação & Manutenção")
        y = H * 0.62
        c.setFillColor(colors.HexColor("#B8BCCB"))
        c.setFont("Carlito", 7.5)
        c.drawString(x, y, rotulo)
        y -= 10 * mm
        c.setFillColor(colors.white)
        tit = (d.get("titulo") or "").strip()
        c.setFont("Carlito", 19)
        for ln in _quebrar(tit + (" —" if tit else ""), "Carlito", 19, 120 * mm)[:3]:
            c.drawString(x, y, ln)
            y -= 8 * mm
        y -= 3 * mm
        c.setFillColor(VERDE)
        c.setFont("Carlito-Bold", 25)
        for ln in _quebrar(usina.upper(), "Carlito-Bold", 25, 140 * mm)[:2]:
            c.drawString(x, y, ln)
            y -= 10 * mm
        y += 3 * mm
        c.setStrokeColor(VERDE)
        c.setLineWidth(1.2)
        c.line(x, y, x + 150 * mm, y)
        y -= 8 * mm
        if d.get("tema"):
            c.setFillColor(colors.HexColor("#B8BCCB"))
            c.setFont("Carlito", 7)
            c.drawString(x, y, str(d["tema"]).upper()[:110])
            y -= 6 * mm
        if d.get("destaque"):
            c.setFillColor(colors.white)
            c.setFont("Carlito-Bold", 10.5)
            for ln in _quebrar(d["destaque"], "Carlito-Bold", 10.5, 150 * mm)[:2]:
                c.drawString(x, y, ln)
                y -= 5.2 * mm
        if d.get("referencia"):
            c.setFillColor(VERDE)
            c.setFont("Carlito", 9.5)
            c.drawString(x, y, str(d["referencia"]))
        meta = [("CLIENTE", d.get("cliente")),
                ("USINA", usina + (f" — {d['cidadeUf']}" if d.get("cidadeUf") else "")),
                ("DATA DO RELATÓRIO", _data_extenso(data_rel)),
                ("RESPONSÁVEL TÉCNICO", d.get("responsavelTecnico") or "Fred Alexandrino — Supervisor de O&M")]
        y = 66 * mm
        for k, v in meta:
            if not v:
                continue
            c.setFillColor(colors.HexColor("#B8BCCB"))
            c.setFont("Carlito", 6.5)
            c.drawString(x, y, k)
            c.setFillColor(colors.white)
            c.setFont("Carlito", 9)
            c.drawString(x + pdfmetrics.stringWidth(k, "Carlito", 6.5) + 2.5 * mm, y, str(v))
            y -= 5.6 * mm
        c.restoreState()

    def interna(c, doc):
        c.saveState()
        y = H - 15 * mm
        c.drawImage(logo, ML, y - 1, 3.6 * mm, 3.6 * mm, mask="auto")
        c.setFont("Carlito", 6.8)
        c.setFillColor(CINZA)
        txt = f"{rotulo}   |   {titulo_curto} — {usina}"
        c.drawString(ML + 5 * mm, y, txt[:150])
        c.setStrokeColor(CINZA_LINHA)
        c.setLineWidth(0.6)
        c.line(ML, y - 3.5 * mm, W - MR, y - 3.5 * mm)
        c.line(ML, 16 * mm, W - MR, 16 * mm)
        c.drawString(ML, 11.5 * mm, rodape)
        c.setFont("Carlito", 8.5)
        c.setFillColor(TEXTO)
        c.drawRightString(W - MR, 11.5 * mm, str(doc.page))
        c.restoreState()

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=ML, rightMargin=MR, topMargin=MT, bottomMargin=MB,
                          title=f"{rotulo.title()} — {usina}", author="Fred Alexandrino — Grid Co.",
                          subject=titulo_curto, creator="Central O&M — Grid Co.")
    frame_capa = Frame(0, 0, W, H, id="capa")
    frame = Frame(ML, MB, largura, H - MT - MB, id="corpo", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="capa", frames=[frame_capa], onPage=capa),
                          PageTemplate(id="interna", frames=[frame], onPage=interna)])

    story = [NextPageTemplate("interna"), PageBreak()]
    n = [0]

    def secao(titulo, blocos):
        """Barra + primeiro bloco juntos (nunca barra órfã)."""
        blocos = [b for b in blocos if b is not None]
        if not blocos:
            return
        n[0] += 1
        story.append(CondPageBreak(45 * mm))
        story.append(KeepTogether([_Barra(f"{n[0]}. {titulo}", st), Spacer(1, 9), blocos[0]]))
        story.extend(blocos[1:])
        story.append(Spacer(1, 14))

    # 1. objetivo
    if d.get("objetivo") or d.get("enquadramento"):
        bl = []
        for p in _paragrafos(d.get("objetivo")):
            bl.append(Paragraph(_e(p), st["corpo"]))
        enq = d.get("enquadramento") or {}
        if isinstance(enq, str):
            enq = {"rotulo": "ENQUADRAMENTO", "texto": enq}
        if enq.get("texto"):
            bl += [Spacer(1, 4), _callout((enq.get("rotulo") or "ENQUADRAMENTO").upper(), enq["texto"], st, largura)]
        secao("Objetivo do documento", bl)
    # 2. cronologia
    cron = [c for c in (d.get("cronologia") or []) if isinstance(c, dict) and c.get("texto")]
    if cron:
        secao("Cronologia da ocorrência" if "OCORR" in rotulo else "Cronologia da atividade",
              [_cronologia(cron, st, largura)])
    # 3. condições
    cond = [c for c in (d.get("condicoes") or []) if isinstance(c, dict) and (c.get("item") or c.get("situacao"))]
    if cond:
        secao(d.get("tituloCondicoes") or "Condições e riscos identificados",
              [_tabela(["Item", "Situação identificada", "Criticidade"],
                       [[c.get("item"), c.get("situacao"), c.get("criticidade") or "—"] for c in cond],
                       [largura * 0.32, largura * 0.50, largura * 0.18], st, col_cor=2,
                       cor_fn=lambda v: COR_CRIT.get(str(v or "").strip().lower(), TEXTO))])
    # 4. ações
    acoes = [a for a in (d.get("acoes") or []) if isinstance(a, dict) and a.get("acao")]
    if acoes:
        secao("Ações realizadas e pendências",
              [_tabela(["Data", "Ação", "Status"], [[a.get("data") or "A definir", a.get("acao"), a.get("status") or "—"] for a in acoes],
                       [largura * 0.16, largura * 0.62, largura * 0.22], st, col_cor=2, cor_fn=_cor_status)])
    # 5. evidências (uma foto por página)
    evid = [e for e in (d.get("evidencias") or []) if isinstance(e, dict) and isinstance(e.get("foto"), int)
            and 0 <= e["foto"] < len(fotos)]
    if evid:
        n[0] += 1
        story.append(PageBreak())
        alt_max = H - MT - MB - 40 * mm
        for k, e in enumerate(evid):
            try:
                img = _foto_flowable(fotos[e["foto"]], largura * 0.9, alt_max)
            except Exception:
                continue
            sub = "   |   ".join(x for x in (e.get("data"), f"Registro: {e['registro']}" if e.get("registro") else "", usina) if x)
            bloco = [img, Spacer(1, 6), Paragraph(_e(e.get("legenda")), st["legenda"]), Spacer(1, 2), Paragraph(_e(sub), st["legsub"])]
            if k == 0:
                bloco = [_Barra(f"{n[0]}. Evidências fotográficas", st), Spacer(1, 12)] + bloco
            else:
                story.append(PageBreak())
            story.append(KeepTogether(bloco))
        story.append(PageBreak())
    # 6. ponto de atenção
    at = d.get("atencao") or {}
    if isinstance(at, dict) and (at.get("resumo") or at.get("paragrafos") or at.get("itens")):
        bl = []
        if at.get("resumo"):
            bl += [_callout("RESUMO", at["resumo"], st, largura), Spacer(1, 10)]
        for p in _paragrafos(at.get("paragrafos")):
            bl.append(Paragraph(_e(p), st["corpo"]))
        for it in at.get("itens") or []:
            bl.append(Paragraph(_e(it), st["item"], bulletText="•"))
        if at.get("itens"):
            bl.append(Spacer(1, 6))
        for p in _paragrafos(at.get("fecho")):
            bl.append(Paragraph(_e(p), st["corpo"]))
        secao(d.get("tituloAtencao") or "Ponto de atenção", bl)
    # 7. conclusão + fecho + assinatura (bloco único)
    concl = _paragrafos(d.get("conclusao"))
    if concl:
        n[0] += 1
        ass = d.get("assinatura") or {}
        local = d.get("local") or ""
        fecho = [_Barra(f"{n[0]}. Conclusão", st), Spacer(1, 9)]
        fecho += [Paragraph(_e(p), st["corpo"]) for p in concl]
        fecho += [Spacer(1, 22), Paragraph(_e(f"{local + ', ' if local else ''}{_data_extenso(data_rel)}."), st["corpo"]),
                  Spacer(1, 30), _Linha(60 * mm), Spacer(1, 3),
                  Paragraph(_e(ass.get("nome") or "Fred Alexandrino"), st["assin"]),
                  Paragraph(_e(ass.get("cargo") or "Supervisor de O&M — Grid Co."), st["assin2"])]
        story.append(KeepTogether(fecho))
    # tira PageBreak final sobrando
    while story and isinstance(story[-1], (PageBreak, Spacer)):
        story.pop()
    doc.build(story)
    return buf.getvalue()


class _Linha(Flowable):
    def __init__(self, w):
        super().__init__()
        self.w = w

    def wrap(self, aw, ah):
        return self.w, 1

    def draw(self):
        self.canv.setStrokeColor(TEXTO)
        self.canv.setLineWidth(0.6)
        self.canv.line(0, 0, self.w, 0)


def _paragrafos(v):
    if not v:
        return []
    if isinstance(v, str):
        return [p.strip() for p in re.split(r"\n\s*\n", v) if p.strip()]
    return [str(p).strip() for p in v if str(p or "").strip()]


def _quebrar(texto, fonte, tam, larg):
    palavras, linhas, atual = str(texto or "").split(), [], ""
    for p in palavras:
        t = (atual + " " + p).strip()
        if pdfmetrics.stringWidth(t, fonte, tam) <= larg:
            atual = t
        else:
            if atual:
                linhas.append(atual)
            atual = p
    if atual:
        linhas.append(atual)
    return linhas
