"""PDF do Boletim de Medição gerado no servidor (reportlab).

Antes o PDF era uma "foto" da tela feita no navegador (html2canvas). Cada navegador
(Chrome Android, Firefox, Windows) media o texto de um jeito e o PDF saía diferente:
palavras espalhadas, linhas cortadas. Aqui o PDF é desenhado no servidor, com fonte
embutida (Carlito, métrica idêntica à Calibri): sai igual em qualquer aparelho e com
texto de verdade (selecionável).

Entrada: o mesmo objeto D que o bm.js congela no snapshot (ufvs, custos, cos, ref…).
A lógica de valores é a mesma do renderBoletim() do bm/bm.js — se mudar lá, mude aqui.
"""
import calendar
import io
import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Flowable, Image, KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)
from xml.sax.saxutils import escape

_BASE = os.path.dirname(os.path.abspath(__file__))
_ASSETS = os.path.join(_BASE, "assets_relatorio")

NAVY = colors.HexColor("#191528")
SEC = colors.HexColor("#504C63")
LIME = colors.HexColor("#A9DB21")
BD = colors.HexColor("#D9D9E0")
ZEBRA = colors.HexColor("#F0F1F4")
TOT = colors.HexColor("#E4E6EC")
ER = colors.HexColor("#B3261E")
NOTE_BG = colors.HexColor("#FBFCF5")
KPI_BIG_L = colors.HexColor("#C9C6D4")

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]
RODAPE_PADRAO = ("Grid Co. — Operação & Manutenção · documento gerado para validação do "
                 "Boletim de Medição. Valores em reais.")

_FONTES_OK = False


def _fontes():
    global _FONTES_OK
    if _FONTES_OK:
        return
    for nome, arq in (("BM", "Carlito-Regular.ttf"), ("BM-B", "Carlito-Bold.ttf"),
                      ("BM-I", "Carlito-Italic.ttf")):
        pdfmetrics.registerFont(TTFont(nome, os.path.join(_ASSETS, arq)))
    pdfmetrics.registerFontFamily("BM", normal="BM", bold="BM-B", italic="BM-I", boldItalic="BM-B")
    _FONTES_OK = True


# ───────────────────────── formatação (igual ao bm.js) ─────────────────────────
def F(n):
    n = float(n or 0)
    s = f"{abs(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if n < 0 else "") + "R$ " + s


def ML(iso):
    if not iso:
        return "—"
    y, m = str(iso).split("-")[:2]
    return f"{MESES[int(m) - 1]}/{y}"


def dt(s):
    if not s:
        return "—"
    p = str(s)[:10].split("-")
    return "/".join(reversed(p))


def e(t):
    return escape(str(t if t is not None else ""))


def _num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _dia_do_mes(ano, m0, dia):
    ano += m0 // 12
    m0 %= 12
    ult = calendar.monthrange(ano, m0 + 1)[1]
    if not dia:
        return date(ano, m0 + 1, ult)
    return date(ano, m0 + 1, min(int(dia), ult)) if int(dia) <= ult else _overflow(ano, m0, int(dia))


def _overflow(ano, m0, dia):
    # JS Date.UTC(ano, m0, 31) em mês de 30 dias rola pro mês seguinte; reproduz isso.
    from datetime import timedelta
    return date(ano, m0 + 1, 1) + timedelta(days=dia - 1)


def _fmt(d):
    return d.strftime("%d/%m/%Y")


def periodo_medicao(mob, d_ini, d_fim, o_ini, o_fim):
    if not mob:
        return None
    d_ini = 26 if d_ini is None else d_ini
    d_fim = 25 if d_fim is None else d_fim
    o_ini = -1 if o_ini is None else o_ini
    o_fim = 0 if o_fim is None else o_fim
    y, m = [int(x) for x in str(mob).split("-")[:2]]
    return _fmt(_dia_do_mes(y, (m - 1) + int(o_ini), d_ini)) + " a " + _fmt(_dia_do_mes(y, (m - 1) + int(o_fim), d_fim))


def periodo_usina(u, dI, dF, oI, oF):
    if u.get("periodoCustom"):
        return periodo_medicao(u.get("mob"), u.get("diaIni"), u.get("diaFim"), u.get("offsetIniMeses"), u.get("offsetFimMeses"))
    return periodo_medicao(u.get("mob"), dI, dF, oI, oF)


def pct_de(x):
    if x.get("pct") not in (None, ""):
        return _num(x.get("pct"))
    return 20 if x.get("imp") else 0


def desc_parcial(pc):
    if not pc:
        return ""
    if pc.get("tipo") == "dias":
        s = f"{pc.get('dias')} de {pc.get('dm')} dias"
        if pc.get("ini") and pc.get("fim"):
            s += f" ({dt(pc['ini'])} a {dt(pc['fim'])})"
        return s
    if pc.get("tipo") == "percentual":
        return f"{str(pc.get('pct')).replace('.', ',')}% da mensalidade"
    return "valor acordado"


def _ptbr_key(s):
    import unicodedata
    return unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().lower()


# ───────────────────────── estilos ─────────────────────────
def _st():
    _fontes()
    base = dict(fontName="BM", fontSize=8.6, leading=10.8, textColor=colors.black)
    return {
        "td": ParagraphStyle("td", **base),
        "tdb": ParagraphStyle("tdb", **{**base, "fontName": "BM-B"}),
        "tdm": ParagraphStyle("tdm", **{**base, "textColor": SEC}),
        "tdp": ParagraphStyle("tdp", **{**base, "fontSize": 7.4, "leading": 9.2, "textColor": SEC}),
        "num": ParagraphStyle("num", **{**base, "alignment": TA_RIGHT}),
        "numb": ParagraphStyle("numb", **{**base, "fontName": "BM-B", "alignment": TA_RIGHT}),
        "numm": ParagraphStyle("numm", **{**base, "alignment": TA_RIGHT, "textColor": SEC}),
        "th": ParagraphStyle("th", fontName="BM-B", fontSize=7.4, leading=9, textColor=colors.white),
        "thn": ParagraphStyle("thn", fontName="BM-B", fontSize=7.4, leading=9, textColor=colors.white, alignment=TA_RIGHT),
        "tot": ParagraphStyle("tot", **{**base, "fontName": "BM-B", "textColor": NAVY}),
        "totn": ParagraphStyle("totn", **{**base, "fontName": "BM-B", "textColor": NAVY, "alignment": TA_RIGHT}),
        "h2": ParagraphStyle("h2", fontName="BM-B", fontSize=10, leading=12, textColor=NAVY),
        "marca": ParagraphStyle("marca", fontName="BM-B", fontSize=11, leading=13, textColor=NAVY),
        "h1": ParagraphStyle("h1", fontName="BM-B", fontSize=18, leading=22, textColor=NAVY),
        "sub": ParagraphStyle("sub", fontName="BM", fontSize=9.6, leading=12, textColor=SEC),
        "note": ParagraphStyle("note", fontName="BM", fontSize=8.8, leading=11.4, textColor=SEC),
        "kl": ParagraphStyle("kl", fontName="BM", fontSize=7.6, leading=9.4, textColor=SEC),
        "kv": ParagraphStyle("kv", fontName="BM-B", fontSize=14, leading=17, textColor=NAVY),
        "klb": ParagraphStyle("klb", fontName="BM", fontSize=7.6, leading=9.4, textColor=KPI_BIG_L),
        "kvb": ParagraphStyle("kvb", fontName="BM-B", fontSize=14, leading=17, textColor=colors.white),
        "foot": ParagraphStyle("foot", fontName="BM-I", fontSize=7.8, leading=10, textColor=SEC),
    }


class Barra(Flowable):
    """Faixa verde-limão sob títulos."""

    def __init__(self, largura, altura=2.2, espaco_antes=2, espaco_depois=4):
        super().__init__()
        self.l, self.a, self.ea, self.ed = largura, altura, espaco_antes, espaco_depois

    def wrap(self, *_):
        return self.l, self.a + self.ea + self.ed

    def draw(self):
        self.canv.setFillColor(LIME)
        self.canv.roundRect(0, self.ed, self.l, self.a, self.a / 2, stroke=0, fill=1)


class Linha(Flowable):
    def __init__(self, largura, cor=BD, esp=0.6, antes=4, depois=4):
        super().__init__()
        self.l, self.cor, self.esp, self.antes, self.depois = largura, cor, esp, antes, depois

    def wrap(self, *_):
        return self.l, self.antes + self.depois

    def draw(self):
        self.canv.setStrokeColor(self.cor)
        self.canv.setLineWidth(self.esp)
        self.canv.line(0, self.depois, self.l, self.depois)


def _caps(t):
    return " ".join(t.upper().split())


def _titulo_secao(st, texto):
    return [Spacer(1, 10), Paragraph(e(_caps(texto)), st["h2"]), Barra(15 * mm, espaco_antes=3, espaco_depois=3)]


def _tabela(st, cab, linhas, larguras, num_cols, tot_rows=(), zebra=False):
    """cab: lista de textos; linhas: listas de Paragraph/str; num_cols: índices alinhados à direita."""
    head = [Paragraph(e(_caps(h)), st["thn"] if i in num_cols else st["th"]) for i, h in enumerate(cab)]
    data = [head] + linhas
    t = Table(data, colWidths=larguras, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5.5),
        ("TOPPADDING", (0, 0), (-1, -1), 4.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.2),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, BD),
    ]
    for r in tot_rows:
        cmds += [("BACKGROUND", (0, r), (-1, r), TOT), ("LINEABOVE", (0, r), (-1, r), 1.4, NAVY)]
    t.setStyle(TableStyle(cmds))
    return t


def _p(st, k, txt):
    return Paragraph(txt, st[k])


# ───────────────────────── montagem ─────────────────────────
def gerar_pdf(D, titulo, rodape=None):
    """Retorna os bytes do PDF do boletim D (mesmo formato do snapshot do bm.js)."""
    st = _st()
    D = D or {}
    ufvs = D.get("ufvs") or []
    custos = D.get("custos") or []
    cos = D.get("cos") or []
    reaj = D.get("reaj") or None
    sM = sum(_num(u.get("men")) for u in ufvs)
    custos_add = [c for c in custos if not c.get("cond") and c.get("tipo") != "desconto"]
    descontos = [c for c in custos if not c.get("cond") and c.get("tipo") == "desconto"]
    sC = sum(_num(c.get("total")) for c in custos_add)
    sD = sum(_num(c.get("total")) for c in descontos)
    sCond = sum(_num(c.get("total")) for c in custos if c.get("cond"))
    sK = sum(_num(c.get("v")) for c in cos)
    total = round(sM + sC + sD + sK, 2)
    n = len(ufvs)
    sub = (f"Mês de referência: {ML(D.get('ref'))} · {n} usina{'' if n == 1 else 's'} "
           f"faturada{'' if n == 1 else 's'}")
    if D.get("revisao"):
        sub += f" · Revisão R{int(D['revisao']):02d}"

    pag_w, pag_h = landscape(A4)
    mx = 12 * mm
    W = pag_w - 2 * mx
    out = []

    # Cabeçalho
    out.append(Paragraph(e(_caps("Operação & Manutenção")), st["marca"]))
    out.append(Spacer(1, 4))
    out.append(Paragraph(e("Boletim de Medição — " + (titulo or "")), st["h1"]))
    out.append(Barra(32 * mm, altura=3, espaco_antes=4, espaco_depois=6))
    out.append(Linha(W, antes=0, depois=6))
    out.append(Paragraph(e(sub), st["sub"]))
    out.append(Spacer(1, 10))

    # KPIs
    kpis = [("Mensalidades Full O&M", F(sM), False, None), ("Custos adicionais", F(sC), False, None)]
    if sD:
        kpis.append(("Descontos", F(sD), False, ER))
    if sK:
        kpis.append(("Monitoramento Remoto", F(sK), False, None))
    kpis.append(("Total do BM", F(total), True, None))
    if sCond:
        kpis.append(("Sujeito a aprovação (fora do total)", F(sCond), False, None))
    cel, larg, cmds = [], [], []
    kw, gap = 50 * mm, 3.5 * mm
    for i, (lab, val, big, cor) in enumerate(kpis):
        vs = ParagraphStyle("kvx", parent=st["kvb" if big else "kv"], textColor=cor) if cor else st["kvb" if big else "kv"]
        cel.append([Paragraph(e(_caps(lab)), st["klb" if big else "kl"]), Paragraph(e(val), vs)])
        larg.append(kw)
        c = len(cel) - 1
        cmds += [("BACKGROUND", (c, 0), (c, 0), NAVY if big else ZEBRA),
                 ("BOX", (c, 0), (c, 0), 0.6, NAVY if big else BD),
                 ("LINEBEFORE", (c, 0), (c, 0), 2.6, LIME)]
        if i < len(kpis) - 1:
            cel.append("")
            larg.append(gap)
    kt = Table([cel], colWidths=larg, hAlign="LEFT")
    kt.setStyle(TableStyle(cmds + [("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                                   ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    out.append(kt)

    dI = D.get("diaIni", 26) if D.get("diaIni") is not None else 26
    dF = D.get("diaFim", 25) if D.get("diaFim") is not None else 25
    oI = D.get("offsetIniMeses", -1) if D.get("offsetIniMeses") is not None else -1
    oF = D.get("offsetFimMeses", 0) if D.get("offsetFimMeses") is not None else 0

    # Mensalidades
    if ufvs:
        linhas = []
        for u in ufvs:
            pc = u.get("parcial")
            nome = e(u.get("nome"))
            if pc:
                nome += (f"<br/><font size='7.4' color='#504C63'>Faturamento parcial — {e(pc.get('motivo') or '')}"
                         f"{(': ' + e(pc.get('desc'))) if pc.get('desc') else ''}</font>")
            if pc and pc.get("tipo") == "dias" and pc.get("ini") and pc.get("fim"):
                per = f"{dt(pc['ini'])} a {dt(pc['fim'])}"
            else:
                per = periodo_usina(u, dI, dF, oI, oF) or "—"
            if pc and pc.get("tipo") == "percentual":
                dias = str(pc.get("pct")).replace(".", ",") + "%"
            elif pc and pc.get("tipo") == "valor":
                dias = "acordo"
            else:
                dias = f"{u.get('dias') or u.get('dm') or 30}/{u.get('dm') or 30}"
            linhas.append([_p(st, "td", nome), _p(st, "td", e(u.get("uf") or "")),
                           _p(st, "num", f"{_num(u.get('mwp')):.3f}"), _p(st, "td", dt(u.get("mob"))),
                           _p(st, "tdm", e(per)), _p(st, "numm", F(u.get("valor"))), _p(st, "num", e(dias)),
                           _p(st, "numb", F(u.get("men")))])
        linhas.append([_p(st, "tot", f"Total mensalidades — {n} usina{'' if n == 1 else 's'}"), "", "", "", "", "", "",
                       _p(st, "totn", F(sM))])
        lw = [x * W for x in (0.19, 0.05, 0.065, 0.11, 0.2, 0.135, 0.11, 0.14)]
        t = _tabela(st, ["UFV", "UF", "MWp", "Data da Medição", "Período de Medição", "Valor do contrato",
                         "Dias faturados", "Mensalidade"], linhas, lw, {2, 5, 6, 7}, tot_rows=(len(linhas),))
        t.setStyle(TableStyle([("SPAN", (0, len(linhas)), (6, len(linhas)))]))
        out += _titulo_secao(st, "Mensalidades — Full O&M") + [t]
        parc = [u for u in ufvs if u.get("parcial")]
        if parc:
            txt = "<b>Faturamento parcial neste mês:</b> " + " ".join(
                f"{e(u['nome'])} — {e(u['parcial'].get('motivo') or '')}"
                f"{(' (' + e(u['parcial'].get('desc')) + ')') if u['parcial'].get('desc') else ''}: "
                f"{e(desc_parcial(u['parcial']))} do valor de {F(u.get('valor'))} = {F(u.get('men'))}." for u in parc)
            out.append(_nota(st, txt, W))

    # Custos adicionais
    out += _titulo_secao(st, "Custos adicionais")
    if custos_add:
        pcts = {pct_de(c) for c in custos_add}
        legado = len(pcts) == 1 and list(pcts)[0] in (0, 20)
        col_acr = "Imposto 20%" if legado else "Acréscimo"
        uni = bool(D.get("custoUnico"))
        ordenados = sorted(custos_add, key=lambda c: (_ptbr_key(c.get("ufv")), str(c.get("data") or "")))
        linhas = []
        for c in ordenados:
            spe = (e(c["spe"]) + "<br/>" if c.get("spe") else "") + e(c["cnpj"]) if c.get("cnpj") else "—"
            row = [_p(st, "td", e(c.get("ufv"))), _p(st, "tdp", spe), _p(st, "td", e(c.get("cat"))),
                   _p(st, "td", e(c.get("desc"))), _p(st, "td", dt(c.get("data"))), _p(st, "td", e(c.get("os") or "—"))]
            if not uni:
                p = pct_de(c)
                acr = _num(c.get("total")) - _num(c.get("unit"))
                row.append(_p(st, "num", F(c.get("unit"))))
                if p:
                    extra = "" if legado else f"<br/><font size='7' color='#504C63'>{e(c.get('rotuloAcr') or 'Encargos')} {p:g}%</font>"
                    row.append(_p(st, "num", F(acr) + extra))
                else:
                    row.append(_p(st, "numm", "—"))
            row.append(_p(st, "numb", F(c.get("total"))))
            linhas.append(row)
        ncols = 7 if uni else 9
        tot = [_p(st, "tot", "Total custos adicionais")] + [""] * (ncols - 2) + [_p(st, "totn", F(sC))]
        linhas.append(tot)
        if uni:
            cab = ["UFV", "SPE / CNPJ", "Categoria", "Descrição", "Data", "OS", "Valor"]
            lw = [x * W for x in (0.11, 0.15, 0.1, 0.39, 0.085, 0.045, 0.12)]
            nums = {6}
        else:
            cab = ["UFV", "SPE / CNPJ", "Categoria", "Descrição", "Data", "OS", "Valor base", col_acr, "Total"]
            lw = [x * W for x in (0.1, 0.14, 0.09, 0.3, 0.08, 0.04, 0.085, 0.085, 0.08)]
            nums = {6, 7, 8}
        t = _tabela(st, cab, linhas, lw, nums, tot_rows=(len(linhas),))
        t.setStyle(TableStyle([("SPAN", (0, len(linhas)), (ncols - 2, len(linhas)))]))
        out.append(t)
    else:
        out.append(_nota(st, "Nenhum custo adicional lançado neste mês. O valor do BM corresponde às mensalidades contratuais.", W))

    # Descontos
    if descontos:
        out += _titulo_secao(st, "Descontos")
        out.append(_nota(st, "Descontos por descumprimento de obrigação contratual, abatimento acordado com o cliente ou "
                             "reembolso de custo da Grid Co. pago pelo cliente. Os valores abaixo são <b>subtraídos</b> "
                             "do total deste Boletim.", W))
        linhas = []
        for c in descontos:
            spe = (e(c["spe"]) + "<br/>" if c.get("spe") else "") + e(c["cnpj"]) if c.get("cnpj") else "—"
            linhas.append([_p(st, "td", e(c.get("ufv"))), _p(st, "tdp", spe), _p(st, "td", e(c.get("cat"))),
                           _p(st, "td", e(c.get("desc"))), _p(st, "td", e(c.get("clausula") or "—")),
                           _p(st, "td", dt(c.get("data"))), _p(st, "td", e(c.get("os") or "—")),
                           Paragraph(f"<font color='#B3261E'>{F(c.get('total'))}</font>", st["numb"])])
        linhas.append([_p(st, "tot", "Total descontos")] + [""] * 6 + [Paragraph(f"<font color='#B3261E'>{F(sD)}</font>", st["totn"])])
        lw = [x * W for x in (0.1, 0.14, 0.11, 0.25, 0.13, 0.08, 0.07, 0.12)]
        t = _tabela(st, ["UFV", "SPE / CNPJ", "Motivo", "A que se refere", "Cláusula / obrigação", "Data", "OS / ref.",
                         "Valor do desconto"], linhas, lw, {7}, tot_rows=(len(linhas),))
        t.setStyle(TableStyle([("SPAN", (0, len(linhas)), (6, len(linhas)))]))
        out.append(t)

    # Monitoramento remoto
    if cos:
        out += _titulo_secao(st, "Monitoramento Remoto")
        if reaj and reaj.get("pct"):
            out.append(_nota(st, f"Contratos reajustados em {(_num(reaj.get('pct')) * 100):.2f}".replace(".", ",")
                             + f"% — {e(reaj.get('desc') or '')}.", W))
        linhas, pm = [], {}
        for c in cos:
            pm[c.get("status")] = pm.get(c.get("status"), 0) + _num(c.get("v"))
            spe = (e(c["spe"]) + "<br/>" if c.get("spe") else "") + e(c["cnpj"]) if c.get("cnpj") else "—"
            nome = e(c.get("nome")) + (f"<br/><font size='7.4' color='#504C63'>{e(c['obs'])}</font>" if c.get("obs") else "")
            d = c.get("d")
            linhas.append([_p(st, "td", nome), _p(st, "td", e(c.get("uf") or "")), _p(st, "num", f"{_num(c.get('mwp')):.3f}"),
                           _p(st, "tdp", spe), _p(st, "tdm", e(c.get("status"))), _p(st, "numm", F(c.get("valor"))),
                           _p(st, "num", e("—" if d is None else d)), _p(st, "numb", F(c.get("v")))])
        for m, v in pm.items():
            linhas.append([_p(st, "tdm", f"&nbsp;&nbsp;&nbsp;subtotal — {e(m)}")] + [""] * 6 + [_p(st, "num", F(v))])
        linhas.append([_p(st, "tot", "Total Monitoramento Remoto")] + [""] * 6 + [_p(st, "totn", F(sK))])
        lw = [x * W for x in (0.2, 0.05, 0.065, 0.16, 0.12, 0.13, 0.115, 0.16)]
        t = _tabela(st, ["UFV", "UF", "MWp", "SPE / CNPJ", "Modalidade", "Valor do contrato", "Dias monitorados",
                         "Mensalidade"], linhas, lw, {2, 5, 6, 7}, tot_rows=(len(linhas),))
        spans = [("SPAN", (0, r), (6, r)) for r in range(len(cos) + 1, len(linhas) + 1)]
        t.setStyle(TableStyle(spans))
        out.append(t)

    # Faturamento por SPE/CNPJ
    por_u = {}
    for x in custos:
        if not x.get("cond"):
            por_u[x.get("ufv")] = por_u.get(x.get("ufv"), 0) + _num(x.get("total"))
    g, sem = {}, []
    for u in ufvs:
        add = por_u.get(u.get("nome"), 0)
        if not u.get("cnpj"):
            sem.append((u.get("nome"), _num(u.get("men")) + add))
            continue
        gg = g.setdefault(u["cnpj"], {"spe": u.get("spe"), "ufvs": [], "men": 0.0, "add": 0.0})
        gg["ufvs"].append(u.get("nome"))
        gg["men"] += _num(u.get("men"))
        gg["add"] += add
    ks = sorted(g, key=lambda k: _ptbr_key(g[k]["spe"]))
    if len(ks) > 1 or (len(ks) == 1 and sem):
        out += _titulo_secao(st, f"Faturamento por SPE e CNPJ — {len(ks)} nota(s) fiscal(is)")
        linhas, tm, ta = [], 0.0, 0.0
        for cn in ks:
            x = g[cn]
            tm += x["men"]
            ta += x["add"]
            linhas.append([_p(st, "td", e(x["spe"] or "—")), _p(st, "tdm", e(cn)), _p(st, "tdp", e(", ".join(x["ufvs"]))),
                           _p(st, "num", F(x["men"])), _p(st, "num", F(x["add"]) if x["add"] else "—"),
                           _p(st, "numb", F(x["men"] + x["add"]))])
        linhas.append([_p(st, "tot", f"TOTAL — {len(ks)} NFs"), "", "", _p(st, "totn", F(tm)), _p(st, "totn", F(ta)),
                       _p(st, "totn", F(tm + ta))])
        lw = [x * W for x in (0.2, 0.15, 0.27, 0.12, 0.14, 0.12)]
        t = _tabela(st, ["SPE", "CNPJ", "UFVs", "Mensalidades", "Custos adicionais / descontos", "Valor da NF"],
                    linhas, lw, {3, 4, 5}, tot_rows=(len(linhas),))
        t.setStyle(TableStyle([("SPAN", (0, len(linhas)), (2, len(linhas)))]))
        out.append(t)
        if sem:
            out.append(_nota(st, "<b>Sem CNPJ cadastrado:</b> " + " · ".join(f"{e(nm)} ({F(v)})" for nm, v in sem) + ".", W))

    # Sujeito a aprovação
    cs = [c for c in custos if c.get("cond")]
    if cs:
        out += _titulo_secao(st, "Sujeito a aprovação — fora do total deste BM")
        out.append(_nota(st, "Os itens abaixo <b>não estão somados</b> no total do Boletim. Dependem de confirmação do "
                             "cliente quanto à forma de faturamento. Aprovados, entram no BM seguinte.", W))
        linhas = []
        for c in cs:
            spe = (e(c["spe"]) + "<br/>" if c.get("spe") else "") + e(c["cnpj"]) if c.get("cnpj") else "—"
            linhas.append([_p(st, "td", e(c.get("ufv"))), _p(st, "tdp", spe),
                           _p(st, "td", ("<b>[Desconto]</b> " if c.get("tipo") == "desconto" else "") + e(c.get("desc"))),
                           _p(st, "td", dt(c.get("data"))), _p(st, "numb", F(c.get("total")))])
        linhas.append([_p(st, "tot", "Total sujeito a aprovação"), "", "", "", _p(st, "totn", F(sCond))])
        lw = [x * W for x in (0.15, 0.18, 0.4, 0.12, 0.15)]
        t = _tabela(st, ["UFV", "SPE / CNPJ", "Descrição", "Período", "Valor"], linhas, lw, {4}, tot_rows=(len(linhas),))
        t.setStyle(TableStyle([("SPAN", (0, len(linhas)), (3, len(linhas)))]))
        out.append(t)

    # Resumo (sempre inteiro na mesma página) + rodapé
    res = [[_p(st, "td", "Mensalidades Full O&amp;M"), _p(st, "num", F(sM))],
           [_p(st, "td", "Custos adicionais"), _p(st, "num", F(sC))]]
    if sD:
        res.append([_p(st, "td", "Descontos"), Paragraph(f"<font color='#B3261E'>{F(sD)}</font>", st["num"])])
    if sK:
        res.append([_p(st, "td", "Monitoramento Remoto"), _p(st, "num", F(sK))])
    res.append([_p(st, "tot", f"TOTAL DO BM — {ML(D.get('ref'))}"), _p(st, "totn", F(total))])
    tot_r = len(res) - 1
    if sCond:
        res.append([_p(st, "tdm", "Sujeito a aprovação, fora do total"), _p(st, "numm", F(sCond))])
    rt = Table(res, colWidths=[W * 0.8, W * 0.2])
    rt.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, BD), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 5.5), ("RIGHTPADDING", (0, 0), (-1, -1), 5.5),
                            ("TOPPADDING", (0, 0), (-1, -1), 4.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 4.2),
                            ("BACKGROUND", (0, tot_r), (-1, tot_r), TOT), ("LINEABOVE", (0, tot_r), (-1, tot_r), 1.4, NAVY)]))
    out.append(KeepTogether(_titulo_secao(st, "Resumo") + [rt, Spacer(1, 16), Linha(W, antes=0, depois=5),
                                                           Paragraph(e(rodape or RODAPE_PADRAO), st["foot"])]))

    # Títulos de seção nunca sozinhos no pé da página
    for fl in out:
        if isinstance(fl, Paragraph) and fl.style.name == "h2":
            fl.keepWithNext = 1
        if isinstance(fl, Barra):
            fl.keepWithNext = 1

    buf = io.BytesIO()
    logo = os.path.join(_ASSETS, "bm_logo.png")
    marca = os.path.join(_ASSETS, "bm_marca.png")

    def _fundo(canv, doc):
        canv.saveState()
        tam = 92 * mm
        canv.drawImage(marca, (pag_w - tam) / 2, (pag_h - tam) / 2, tam, tam, mask="auto", preserveAspectRatio=True)
        lw_, lh_ = 40 * mm, 40 * mm * 92 / 520
        canv.drawImage(logo, pag_w - mx - lw_, pag_h - 9 * mm - lh_, lw_, lh_, mask="auto", preserveAspectRatio=True)
        if doc.page > 1:
            canv.setFont("BM", 7.5)
            canv.setFillColor(SEC)
            canv.drawString(mx, pag_h - 9 * mm - lh_ + 2, e("Boletim de Medição — " + (titulo or "")))
        canv.setFont("BM", 7.5)
        canv.setFillColor(SEC)
        canv.drawRightString(pag_w - mx, 6 * mm, f"Página {doc.page}")
        canv.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=(pag_w, pag_h), leftMargin=mx, rightMargin=mx,
                            topMargin=9 * mm + 40 * mm * 92 / 520 + 4 * mm, bottomMargin=11 * mm,
                            title="Boletim de Medição — " + (titulo or ""), author="Grid Co.")
    doc.build(out, onFirstPage=_fundo, onLaterPages=_fundo)
    return buf.getvalue()


def _nota(st, html, W):
    t = Table([[Paragraph(html, st["note"])]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NOTE_BG), ("BOX", (0, 0), (-1, -1), 0.6, BD),
                           ("LINEBEFORE", (0, 0), (0, -1), 2.6, LIME), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 7),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return t
