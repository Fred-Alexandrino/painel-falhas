"""
app.py — Servidor principal
Recebe webhooks do WPPConnect/Baileys, parseia mensagens de falha
e grava automaticamente no Google Sheets.

Suporta:
- Mensagens individuais de ocorrência (🔴/🟡/🟢)
- Mensagens de normalização (✅ + "NORMALIZADO")
- Rondas diárias completas (múltiplas ocorrências em uma mensagem)
"""

import os, re, json, logging
from datetime import datetime
from flask import Flask, request, jsonify
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Configuração ──────────────────────────────────────────────────────────────
SHEET_ID       = os.environ.get("SHEET_ID", "1VLo8__wxSJVWiUIFd_JTcOnadJlUt440i1M1pC0ehTs")
SHEET_NAME     = os.environ.get("SHEET_NAME", "Painel de Falhas - Fred Alexandrino")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
GRUPOS_FILTRO  = os.environ.get("GRUPOS_IDS", "").split(",")

# ── Mapeamento Usina → Cliente ────────────────────────────────────────────────
CLIENTE_POR_USINA = {
    "nova xavantina i": "RENOGRID", "nova xavantina 1": "RENOGRID",
    "nova xavantina ii": "RENOGRID", "nova xavantina 2": "RENOGRID",
    "nova xavantina": "RENOGRID",
    "colíder i": "RENOGRID", "colider i": "RENOGRID",
    "colíder 1": "RENOGRID", "colider 1": "RENOGRID",
    "colíder ii": "RENOGRID", "colider ii": "RENOGRID",
    "colíder 2": "RENOGRID", "colider 2": "RENOGRID",
    "colíder": "RENOGRID", "colider": "RENOGRID",
    "nobres": "RENOGRID", "elias fausto": "RENOGRID",
    "crateús": "RENOGRID", "crateus": "RENOGRID",
    "boa esperança do sul 1": "THOPEN", "boa esperanca do sul 1": "THOPEN",
    "boa esperança do sul 1a": "THOPEN", "boa esperanca do sul 1a": "THOPEN",
    "boa esperança do sul ia": "THOPEN", "boa esperanca do sul ia": "THOPEN",
    "boa esperança do sul 2": "THOPEN", "boa esperanca do sul 2": "THOPEN",
    "boa esperança do sul 1b": "THOPEN", "boa esperanca do sul 1b": "THOPEN",
    "boa esperança do sul ib": "THOPEN", "boa esperanca do sul ib": "THOPEN",
    "boa esperança do sul": "THOPEN", "boa esperanca do sul": "THOPEN",
    "boa esperança": "THOPEN", "boa esperanca": "THOPEN",
    "ibaté i": "THOPEN", "ibate i": "THOPEN",
    "ibaté 1": "THOPEN", "ibate 1": "THOPEN",
    "ibaté ii": "THOPEN", "ibate ii": "THOPEN",
    "ibaté 2": "THOPEN", "ibate 2": "THOPEN",
    "ibaté": "THOPEN", "ibate": "THOPEN",
    "matão i": "THOPEN", "matao i": "THOPEN",
    "matão 1": "THOPEN", "matao 1": "THOPEN",
    "matão ii": "THOPEN", "matao ii": "THOPEN",
    "matão 2 - topázio": "THOPEN", "matao 2 - topazio": "THOPEN",
    "matão 2": "THOPEN", "matao 2": "THOPEN",
    "matão": "THOPEN", "matao": "THOPEN",
    "topázio": "THOPEN", "topazio": "THOPEN",
    "sítio bonfim": "THOPEN", "sitio bonfim": "THOPEN",
    "poconé": "THOPEN", "pocone": "THOPEN",
    "canarana i": "THOPEN", "canarana 1": "THOPEN",
    "canarana ii": "THOPEN", "canarana 2": "THOPEN",
    "canarana": "THOPEN",
    "ribeirão cascalheira": "THOPEN", "ribeirao cascalheira": "THOPEN",
    "araputanga": "2C", "sete lagoas": "2C",
    "guajirú": "GD Energy", "guajiru": "GD Energy",
    "sol do norte i": "GD Energy", "sol do norte 1": "GD Energy",
    "sol do norte ii": "GD Energy", "sol do norte 2": "GD Energy",
    "sol do norte": "GD Energy",
    "abc morada nova": "Alves Lima",
}

USINAS_PERMITIDAS = set(CLIENTE_POR_USINA.keys())

STATUS_VALIDOS = {
    "em aberto": "Em Aberto", "aberto": "Em Aberto",
    "concluído": "Concluído", "concluido": "Concluído", "resolvido": "Concluído",
    "aguardando cliente": "Aguardando Cliente",
    "aguardando fabricante": "Aguardando Fabricante",
    "aguardando equipamento": "Aguardando Equipamento",
}

# ── Padrões de extração ───────────────────────────────────────────────────────
PADROES = {
    "usina":       re.compile(r"(?:🔴|🟡|🟢|🟠|✅|⏸️)?[\s🛠️]*(?:DESVIO:?\s*)?Usina:?[ \t]*([^\n\r]+)", re.IGNORECASE),
    "problema":    re.compile(r"Probl[eo]ma[s]?:[ \t]*([^\n\r]+)",              re.IGNORECASE),
    "descricao":   re.compile(r"Descri(?:ção|cao|çao|ção)[^:]*:[ \t]*([^\n\r]+)", re.IGNORECASE),
    "acao":        re.compile(r"Ação:[ \t]*([^\n\r]+)",                          re.IGNORECASE),
    "equipe":      re.compile(r"Equipe Acionada:[ \t]*([^\n\r]+)",               re.IGNORECASE),
    "supervisor":  re.compile(r"Supervisor Acionado:[ \t]*([^\n\r]+)",           re.IGNORECASE),
    "inicio":      re.compile(r"In[ií]ci[oo][ \t]+(?:d[ao][ \t]+)?[Oo]corrên?cia:[ \t]*([^\n\r]+)", re.IGNORECASE),
    "fim":         re.compile(r"(?:Fim|Término)[ \t]+(?:d[ao][ \t]+)?[Oo]corrência:[ \t]*([^\n\r]*)", re.IGNORECASE),
    "os":          re.compile(r"N[ºo°][ \t]*da[ \t]*OS:[ \t]*([^\n\r]+)",       re.IGNORECASE),
    "impacto":     re.compile(r"Impacto:[ \t]*([^\n\r]+)",                         re.IGNORECASE),
    "equipamento": re.compile(r"^\*?[ \t]*Equipamento[^:\n]*:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "causa":       re.compile(r"^\*?[ \t]*Causa[^:\n]*:[ \t]*([^\n\r]+)",       re.IGNORECASE | re.MULTILINE),
    "tipo_manut":  re.compile(r"Tipo Manutenção[^:]*:[ \t]*([^\n\r]+)",         re.IGNORECASE),
    "identificacao": re.compile(r"identificação:[ \t]*([^\n\r]+)",               re.IGNORECASE),
    "equip_problema": re.compile(r"Equipamentos com Problema:[ \t]*([^\n\r]+)",  re.IGNORECASE),
}


# ── Helpers de texto ──────────────────────────────────────────────────────────

def extrair(texto, padrao):
    m = padrao.search(texto)
    return m.group(1).strip().lstrip("*").strip() if m else ""

def vazio(v):
    return not v or str(v).strip() in ("", "--", "-", "N/A", "n/a", "não", "nao", "Não")

def normalizar_texto(t):
    """Remove acentos e converte para minúsculo para comparação."""
    import unicodedata
    return unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode("ascii").strip()

def inferir_cliente(usina):
    u = usina.lower().strip()
    if u in CLIENTE_POR_USINA:
        return CLIENTE_POR_USINA[u]
    for chave, cliente in CLIENTE_POR_USINA.items():
        if chave in u or u in chave:
            return cliente
    return ""

def usina_permitida(usina):
    u = usina.lower().strip()
    if u in USINAS_PERMITIDAS:
        return True
    for permitida in USINAS_PERMITIDAS:
        if permitida in u or u in permitida:
            return True
    return False

def extrair_tecnico(s):
    m = re.search(r"@([\w\s]+?)(?:\s*[-–]\s*[\w-]+)?\s*$", s)
    if m:
        return m.group(1).strip()
    s = re.sub(r"^[Ss]im[,\s]*", "", s).strip()
    return re.sub(r"@", "", s).strip()

# Regex completa para identificar equipamentos em qualquer campo da mensagem
_REGEX_EQUIP = re.compile(
    r"(?<![\w-])("
    r"INV-\d+|"
    r"Inversor(?:es)?\s+\d+(?:[,\s]+\d+)*(?:\s+e\s+\d+)*|"
    r"Tracker(?:s)?\s+\d+(?:[,\s]+\d+)*(?:\s+e\s+\d+)*|"
    r"Tck(?:s)?\s+\d+(?:[,\s]+\d+)*|"
    r"Motor(?:[\w\s/]*Tracker)?\s*\d*|"
    r"TCU(?:[\w\s]*Tracker)?\s*\d*|"
    r"Fieldlogger|Smartlogger|"
    r"Rel[eé](?:\s+(?:UPR|EP\d+|de\s+[Pp]roteção|de\s+[Tt]emperatura|[A-Z0-9]+))?|"
    r"ETM|NVR|GCU|RSU|NCU|DPS|"
    r"Nobreak(?:\s+[\w]+)?|"
    r"EP\d+|Igate(?:[\w\s]*)?|"
    r"Câmera(?:s)?(?:[\w\s]*)?|"
    r"Piranometro(?:[\w\s]*)?|Anemômetro|"
    r"Exaustor(?:[\w\s]*)?|"
    r"Otimizador(?:es)?(?:[\w\s]*)?\d*|"
    r"Chave\s+Seccionadora(?:[\w\s]*)?|"
    r"Stringbox|Combiner(?:[\w\s]*)?\d*|"
    r"Transformador(?:[\w\s]*)?\d*|"
    r"Ventilador(?:[\w\s]*)?|Switch(?:[\w\s]*)?|"
    r"Bateria(?:[\w\s-]*)?(?:Tracker\s+\d+)?"
    r")(?![\w-])",
    re.IGNORECASE
)

def _limpar_equipamento(equip):
    equip = equip.strip()
    equip = re.sub(r"Tck\s*", "Tracker ", equip, flags=re.IGNORECASE)
    equip = re.sub(r"(?<=[Tt]racker\s)0+(\d)", r"\1", equip)
    equip = re.sub(r"(?<=[Mm]otor\s)0+(\d)", r"\1", equip)
    if equip:
        equip = equip[0].upper() + equip[1:]
    for tipo in [r"Rel[eé]\s+\w+", r"Nobreak\s+\w+", r"ETM", r"Igate\s*\w*"]:
        equip = re.sub(rf"({tipo})\s+(?:com|de|para|que|na|no|em)\s+.*", r"\1", equip, flags=re.IGNORECASE)
    return equip.strip()

def inferir_equipamento(problema="", descricao="", identificacao="", equip_problema="", acao="", impacto=""):
    """Extrai equipamento buscando em todos os campos na ordem de prioridade."""
    for texto in [identificacao, problema, descricao, impacto, acao, equip_problema]:
        if not texto or str(texto).strip() in ("", "--", "-", "N/A"):
            continue
        m = _REGEX_EQUIP.search(texto)
        if m:
            return _limpar_equipamento(m.group(0))
    fonte = problema or descricao or ""
    return fonte[:60] if fonte else ""

def eh_normalizacao(texto):
    return bool(re.search(r"normalizado|normalizada|✅.*normal", texto, re.IGNORECASE))

def detectar_status_emoji(bloco):
    """Detecta o status pelo emoji do bloco."""
    if re.search(r"✅", bloco):
        if eh_normalizacao(bloco):
            return "normalizado"
        return "Em Aberto"
    if re.search(r"🔴", bloco): return "Em Aberto"
    if re.search(r"🟡", bloco): return "Em Aberto"
    if re.search(r"🟠", bloco): return "Em Aberto"
    if re.search(r"⏸️", bloco): return "Em Aberto"
    return "Em Aberto"

def extrair_data_fmt(texto_data, fallback):
    """Extrai DD/MM de uma string de data."""
    if vazio(texto_data):
        return fallback
    m = re.search(r"(\d{2}/\d{2})", texto_data)
    return m.group(1) if m else fallback

def similaridade_falha(falha1, falha2):
    """Verifica se duas falhas são essencialmente a mesma."""
    n1 = normalizar_texto(falha1)
    n2 = normalizar_texto(falha2)
    # Extrai palavras-chave (ignora palavras curtas)
    palavras1 = set(p for p in n1.split() if len(p) > 3)
    palavras2 = set(p for p in n2.split() if len(p) > 3)
    if not palavras1 or not palavras2:
        return False
    intersecao = palavras1 & palavras2
    menor = min(len(palavras1), len(palavras2))
    return len(intersecao) / menor >= 0.5  # 50% de palavras em comum


# ── Parse de blocos ───────────────────────────────────────────────────────────


def normalizar_num(num_str):
    """Normaliza número removendo zeros à esquerda: '08' → '8'"""
    try:
        return str(int(num_str))
    except:
        return num_str

def extrair_atualizacoes_por_ativo(texto_acao):
    """
    Analisa o texto da Ação e extrai atualizações individuais por equipamento.
    Ex: "Tracker 24 normalizado. Tracker 48 em garantia."
    → [{"equipamento": "Tracker 24", "normalizar": True}, {"equipamento": "Tracker 48", "normalizar": False}]
    """
    PRIORIDADE = {"normalizado": 3, "tratativa fabricante": 2, "garantia": 1, "outro": 0}

    padroes_ativo = [
        (re.compile(r"(Tracker[s]?\s+[\d,\s]+(?:e\s+\d+)?)\s+normalizado[s]?", re.IGNORECASE), "normalizado"),
        (re.compile(r"(Tracker[s]?\s+[\d,\s]+(?:e\s+\d+)?)\s+em\s+operação", re.IGNORECASE), "normalizado"),
        (re.compile(r"TCU\s+dos\s+(Tracker[s]?\s+[\d,\s]+(?:e\s+\d+)?)\s+em\s+garantia", re.IGNORECASE), "garantia"),
        (re.compile(r"(Tracker[s]?\s+[\d,\s]+(?:e\s+\d+)?)\s+permanece\s+em\s+garantia", re.IGNORECASE), "garantia"),
        (re.compile(r"(Tracker[s]?\s+[\d,\s]+(?:e\s+\d+)?)\s+em\s+garantia", re.IGNORECASE), "garantia"),
        (re.compile(r"(Tracker[s]?\s+[\d,\s]+(?:e\s+\d+)?)\s+em\s+tratativa\s+com\s+fabricante", re.IGNORECASE), "tratativa fabricante"),
        (re.compile(r"(INV[-\s]\d+|Inversor\s+\d+)\s+normalizado[s]?", re.IGNORECASE), "normalizado"),
        (re.compile(r"(INV[-\s]\d+|Inversor\s+\d+)\s+em\s+(?:operação|funcionamento)", re.IGNORECASE), "normalizado"),
        (re.compile(r"(INV[-\s]\d+|Inversor\s+\d+)\s+em\s+garantia", re.IGNORECASE), "garantia"),
        (re.compile(r"(INV[-\s]\d+|Inversor\s+\d+)\s+em\s+tratativa\s+com\s+fabricante", re.IGNORECASE), "tratativa fabricante"),
    ]

    melhor = {}
    for padrao, status in padroes_ativo:
        for m in padrao.finditer(texto_acao):
            ativo_raw = m.group(1).strip()
            nums = re.findall(r"\d+", ativo_raw)
            tipo = re.search(r"(Tracker|INV|Inversor|TCU|Motor)", ativo_raw, re.IGNORECASE)
            tipo_str = tipo.group(1).capitalize() if tipo else "Tracker"
            if tipo_str.upper() == "INV":
                tipo_str = "Inversor"
            for num in nums:
                num_norm = normalizar_num(num)
                chave = f"{tipo_str.lower()}_{num_norm}"
                pri_nova = PRIORIDADE.get(status, 0)
                if chave not in melhor or pri_nova > PRIORIDADE.get(melhor[chave]["status_update"], 0):
                    melhor[chave] = {
                        "equipamento": f"{tipo_str} {num_norm}",
                        "status_update": status,
                        "normalizar": (status == "normalizado"),
                        "acao_resumida": status.capitalize(),
                    }
    return list(melhor.values())

def equipamento_match(equip_planilha, equip_busca):
    """Verifica se dois nomes de equipamento se referem ao mesmo ativo."""
    def norm(s):
        s = s.lower().strip()
        s = re.sub(r"motor\s*", "tracker ", s)
        s = re.sub(r"tcu\s*tracker\s*", "tracker ", s)
        s = re.sub(r"inv-", "inversor ", s)
        nums = re.findall(r"\d+", s)
        tipo = re.search(r"(tracker|inversor|motor|tcu|inv)", s)
        tipo_str = tipo.group(1) if tipo else ""
        if tipo_str == "inv":
            tipo_str = "inversor"
        return tipo_str, [normalizar_num(n) for n in nums]

    tipo1, nums1 = norm(equip_planilha)
    tipo2, nums2 = norm(equip_busca)
    if not nums1 or not nums2:
        return False
    tipos_ok = tipo1 == tipo2 or not tipo1 or not tipo2
    nums_ok = bool(set(nums1) & set(nums2))
    return tipos_ok and nums_ok

def separar_blocos(texto):
    """
    Separa a mensagem em blocos individuais de ocorrência.
    Suporta tanto mensagens simples quanto rondas completas.
    """
    # Tenta separar por emoji de status no início de linha
    partes = re.split(r"(?=(?:^|\n)[ \t]*(?:🔴|🟡|🟢|🟠|✅|⏸️))", texto, flags=re.MULTILINE)
    blocos = [p.strip() for p in partes if p.strip() and len(p.strip()) > 30]

    # Se não encontrou separação, tenta por "Usina:" como separador
    if len(blocos) <= 1:
        partes = re.split(r"(?=(?:^|\n)[ \t]*(?:🔴|🟡|🟢|🟠|✅|⏸️|🔧)?[ \t]*(?:DESVIO:?\s*)?Usina:)", texto, flags=re.MULTILINE | re.IGNORECASE)
        blocos = [p.strip() for p in partes if p.strip() and len(p.strip()) > 30]

    return blocos if blocos else [texto]

def parse_bloco(bloco):
    """Parseia um bloco individual de ocorrência."""
    c = {k: extrair(bloco, p) for k, p in PADROES.items()}

    if not c["usina"]:
        return None

    # Limpa o nome da usina (remove emojis e sufixos como "- NORMALIZADO")
    usina = re.sub(r"[🔴🟡🟢🟠✅⏸️🔧⚠️*]", "", c["usina"]).strip()
    usina = re.sub(r"\s*[-–]\s*(?:NORMALIZADO|NORMALIZADA|OK|TRIP\s*\d*).*$", "", usina, flags=re.IGNORECASE).strip()
    usina = usina.rstrip(".,:-")
    # Normaliza sufixos: IA→1, IB→2, IIA→1, IIB→2
    usina = re.sub(r"\s+1[Aa]$", " 1", usina)
    usina = re.sub(r"\s+1[Bb]$", " 2", usina)
    usina = re.sub(r"\s+[Ii][Aa]$", " 1", usina)
    usina = re.sub(r"\s+[Ii][Bb]$", " 2", usina)

    if not usina_permitida(usina):
        return None

    # Detecta formato 🔧 (Ronda de Trackers)
    eh_formato_tracker = not vazio(c["identificacao"]) or not vazio(c["equip_problema"])

    if eh_formato_tracker:
        # Formato 🔧:
        # identificacao → Equipamento (ex: "Tck 53" → "Tracker 53")
        id_raw = c["identificacao"] if not vazio(c["identificacao"]) else ""
        id_fmt = re.sub(r"Tck\s*", "Tracker ", id_raw, flags=re.IGNORECASE).strip()
        equip = id_fmt if id_fmt else inferir_equipamento(problema=c["problema"], descricao=c["descricao"], acao=c["acao"], impacto=c.get("impacto",""))

        # equip_problema → Causa (ex: "baterias da TCU com falha")
        equip_prob = c["equip_problema"] if not vazio(c["equip_problema"]) else ""
        # Separa causa da ação: texto antes de "acionado" é causa, depois é ação
        m_acao = re.search(r"(.+?)\.\s*(acionado.+)$", equip_prob, re.IGNORECASE | re.DOTALL)
        if m_acao:
            causa = m_acao.group(1).strip()
            acao_tracker = m_acao.group(2).strip().capitalize()
        else:
            causa = equip_prob
            acao_tracker = ""

        partes_acao = []
        if acao_tracker:
            partes_acao.append(acao_tracker)
        elif not vazio(c["acao"]):
            partes_acao.append(c["acao"])
        else:
            partes_acao.append("Inspeção em campo")
    else:
        # Formato padrão
        equip = c["equipamento"] if not vazio(c["equipamento"]) else \
                inferir_equipamento(problema=c["problema"], descricao=c["descricao"], identificacao=c["identificacao"], equip_problema=c["equip_problema"], acao=c["acao"], impacto=c.get("impacto",""))
        causa = c["causa"] if not vazio(c["causa"]) else ""

        partes_acao = []
        if not vazio(c["acao"]):
            partes_acao.append(c["acao"])
        else:
            partes_acao.append("Inspeção em campo")

    tec = extrair_tecnico(c["equipe"]) if not vazio(c["equipe"]) else ""
    if not vazio(tec): partes_acao.append(f"Técnico: {tec}")
    sup = re.sub(r"^[Ss]im[,\s]*", "", c["supervisor"]).strip() if not vazio(c["supervisor"]) else ""
    sup = re.sub(r"@", "", sup).strip()
    if not vazio(sup): partes_acao.append(f"Supervisor: {sup}")

    # OS
    os_num = ""
    if not vazio(c["os"]):
        m_os = re.search(r"[\d/]+", c["os"])
        os_num = m_os.group() if m_os else ""

    # Status e normalização
    status_emoji = detectar_status_emoji(bloco)
    normalizar = (status_emoji == "normalizado")
    fim_valido = not vazio(c["fim"])

    # Histórico
    hoje = datetime.now().strftime("%d/%m")
    hist = []
    data_inicio = extrair_data_fmt(c["inicio"], hoje)
    if normalizar:
        data_fim = extrair_data_fmt(c["fim"], hoje)
        hist.append(f"{data_inicio} - Registro inicial")
        hist.append(f"{data_fim} - Ocorrência normalizada")
    else:
        hist.append(f"{data_inicio} - Registro inicial")
        # Para formato tracker usa acao_tracker, senão usa c["acao"]
        acao_hist = acao_tracker if eh_formato_tracker and not vazio(acao_tracker) else c["acao"]
        if not vazio(acao_hist):
            hist.append(f"{hoje} - {acao_hist}")

    return {
        "usina":        usina,
        "cliente":      inferir_cliente(usina),
        "equipamento":  equip,
        "falha":        (c["problema"] or c["descricao"] or c["tipo_manut"] or (f"Tracker parado - {causa}" if eh_formato_tracker else "") or ""),
        "causa":        causa,
        "equip_impact": equip,
        "acao":         " | ".join(partes_acao),
        "status":       "Concluído" if normalizar else "Em Aberto",
        "historico":    "\n".join(hist),
        "os":           os_num,
        "normalizar":   normalizar,
        "acao_texto":   c["acao"],
    }


# ── Google Sheets ─────────────────────────────────────────────────────────────

def get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON não configurado")
    creds_dict = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID).worksheet(SHEET_NAME)

def carregar_planilha(ws):
    """Carrega todos os dados da planilha em memória."""
    return ws.get_all_values()

def proximo_id(todos):
    maior = 0
    for row in todos[1:]:
        if row and row[0]:
            try:
                maior = max(maior, int(row[0]))
            except ValueError:
                pass
    return maior + 1

def buscar_ocorrencia_existente(todos, usina, falha):
    """
    Busca ocorrência existente na planilha.
    Retorna (num_linha, row) se encontrar mesma usina + falha similar Em Aberto.
    Retorna (num_linha, row, "diferente") se encontrar mesma usina mas falha diferente.
    """
    for i, row in enumerate(todos[1:], start=2):
        if len(row) < 9:
            continue
        usina_plan = row[2].strip()   # coluna C
        falha_plan = row[4].strip()   # coluna E
        status = row[8].strip()       # coluna I

        if status == "Concluído":
            continue

        # Verifica se é a mesma usina
        if not (usina.lower() in usina_plan.lower() or usina_plan.lower() in usina.lower()):
            continue

        # Mesma usina — verifica se é a mesma falha
        if similaridade_falha(falha, falha_plan):
            return (i, row, "mesma")
        else:
            return (i, row, "diferente")

    return None

def atualizar_ocorrencia(ws, num_linha, row, dados):
    """Atualiza histórico e ação de uma ocorrência existente."""
    hoje = datetime.now().strftime("%d/%m")

    # Atualiza Ação (coluna H = 8)
    acao_atual = row[7] if len(row) > 7 else ""
    nova_acao = dados["acao_texto"]
    if not vazio(nova_acao) and nova_acao not in acao_atual:
        nova_acao_completa = acao_atual + "\n" + nova_acao if acao_atual else nova_acao
        ws.update_cell(num_linha, 8, nova_acao_completa)

    # Atualiza Histórico (coluna L = 12)
    hist_atual = row[11] if len(row) > 11 else ""
    nova_entrada = f"{hoje} - {dados['acao_texto']}" if not vazio(dados["acao_texto"]) else f"{hoje} - Atualização de status"
    if nova_entrada not in hist_atual:
        novo_hist = hist_atual + "\n" + nova_entrada if hist_atual else nova_entrada
        ws.update_cell(num_linha, 12, novo_hist)

    # Atualiza OS se veio (coluna K = 11)
    if not vazio(dados["os"]):
        os_atual = row[10] if len(row) > 10 else ""
        if vazio(os_atual):
            ws.update_cell(num_linha, 11, dados["os"])

    log.info(f"🔄 Atualizado linha {num_linha} | {dados['usina']}")

def normalizar_ocorrencia(ws, num_linha, row, dados):
    """Marca ocorrência como Concluída."""
    hoje = datetime.now().strftime("%d/%m")

    # Status → Concluído (coluna I = 9)
    ws.update_cell(num_linha, 9, "Concluído")

    # OS (coluna K = 11)
    if not vazio(dados["os"]):
        ws.update_cell(num_linha, 11, dados["os"])

    # Histórico (coluna L = 12)
    hist_atual = row[11] if len(row) > 11 else ""
    nova_entrada = f"{hoje} - Ocorrência normalizada"
    if not vazio(dados["acao_texto"]):
        nova_entrada += f"\n{hoje} - {dados['acao_texto']}"
    novo_hist = hist_atual + "\n" + nova_entrada if hist_atual else nova_entrada
    ws.update_cell(num_linha, 12, novo_hist)

    log.info(f"✅ Normalizado linha {num_linha} | {dados['usina']}")

def primeira_linha_vazia(todos):
    """Encontra a primeira linha vazia após os dados (ignora linhas vazias no meio)."""
    ultima_com_dado = 1
    for i, row in enumerate(todos[1:], start=2):
        if row and row[0] and str(row[0]).strip():
            ultima_com_dado = i
    return ultima_com_dado + 1

def gravar_nova_ocorrencia(ws, todos, dados):
    """Grava uma nova ocorrência na planilha na primeira linha vazia após os dados."""
    novo_id = proximo_id(todos)
    proxima_linha = primeira_linha_vazia(todos)

    linha = [
        novo_id,
        dados["cliente"],
        dados["usina"],
        dados["equipamento"],
        dados["falha"],
        dados["causa"],
        dados["equip_impact"],
        dados["acao"],
        dados["status"],
        "",           # J - Ticket Fabricante
        dados["os"],  # K - Número da OS
        dados["historico"],  # L - Histórico
    ]
    ws.update(f"A{proxima_linha}:L{proxima_linha}", [linha])
    log.info(f"➕ Nova ocorrência ID={novo_id} | {dados['usina']} — {dados['equipamento']} | linha {proxima_linha}")
    return novo_id


# ── Processamento principal ───────────────────────────────────────────────────

def processar_texto(texto):
    """
    Processa um texto completo (mensagem simples ou ronda completa).
    Retorna resumo das ações tomadas.
    """
    ws = get_sheet()
    todos = carregar_planilha(ws)

    blocos = separar_blocos(texto)
    resultado = {"novos": [], "atualizados": [], "normalizados": [], "ignorados": 0}

    for bloco in blocos:
        dados = parse_bloco(bloco)
        if not dados:
            resultado["ignorados"] += 1
            continue

        # Verifica se a ação contém atualizações individuais por ativo
        atualizacoes_individuais = extrair_atualizacoes_por_ativo(dados.get("acao_texto", ""))

        if atualizacoes_individuais:
            # Ação analítica: aplica cada atualização no ativo correto
            alguma_acao = False
            for upd in atualizacoes_individuais:
                # Busca a linha do ativo específico na planilha
                linha_ativo = None
                for i, row in enumerate(todos[1:], start=2):
                    if len(row) < 9: continue
                    usina_p = row[2].strip()
                    equip_p = row[3].strip()
                    status_p = row[8].strip()
                    if status_p == "Concluído": continue
                    usina_ok = dados["usina"].lower() in usina_p.lower() or usina_p.lower() in dados["usina"].lower()
                    equip_ok = equipamento_match(equip_p, upd["equipamento"])
                    if usina_ok and equip_ok:
                        linha_ativo = (i, row)
                        break

                if linha_ativo:
                    num_linha, row = linha_ativo
                    hoje = datetime.now().strftime("%d/%m")
                    if upd["normalizar"]:
                        normalizar_ocorrencia(ws, num_linha, row, {
                            **dados,
                            "acao_texto": upd["acao_resumida"],
                            "os": dados.get("os", "")
                        })
                        resultado["normalizados"].append(f"{dados['usina']} - {upd['equipamento']}")
                    else:
                        atualizar_ocorrencia(ws, num_linha, row, {
                            **dados,
                            "acao_texto": upd["acao_resumida"]
                        })
                        resultado["atualizados"].append(f"{dados['usina']} - {upd['equipamento']}")
                    alguma_acao = True
                    todos = carregar_planilha(ws)

            if not alguma_acao:
                # Não encontrou nenhum ativo existente — grava como nova
                novo_id = gravar_nova_ocorrencia(ws, todos, dados)
                resultado["novos"].append({"id": novo_id, "usina": dados["usina"]})
                todos = carregar_planilha(ws)

        elif dados["normalizar"]:
            existente = buscar_ocorrencia_existente(todos, dados["usina"], dados["falha"])
            if existente:
                num_linha, row, _ = existente
                normalizar_ocorrencia(ws, num_linha, row, dados)
                resultado["normalizados"].append(dados["usina"])
            else:
                dados["status"] = "Concluído"
                novo_id = gravar_nova_ocorrencia(ws, todos, dados)
                resultado["novos"].append({"id": novo_id, "usina": dados["usina"]})
            todos = carregar_planilha(ws)

        else:
            existente = buscar_ocorrencia_existente(todos, dados["usina"], dados["falha"])
            if existente:
                num_linha, row, tipo = existente
                if tipo == "mesma":
                    atualizar_ocorrencia(ws, num_linha, row, dados)
                    resultado["atualizados"].append(dados["usina"])
                else:
                    novo_id = gravar_nova_ocorrencia(ws, todos, dados)
                    resultado["novos"].append({"id": novo_id, "usina": dados["usina"]})
            else:
                novo_id = gravar_nova_ocorrencia(ws, todos, dados)
                resultado["novos"].append({"id": novo_id, "usina": dados["usina"]})
            todos = carregar_planilha(ws)

    return resultado


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        payload = request.get_json(force=True)
        if not payload:
            return jsonify({"status": "ignored", "reason": "empty payload"}), 200

        if WEBHOOK_SECRET:
            secret = request.headers.get("X-Webhook-Secret", "")
            if secret != WEBHOOK_SECRET:
                return jsonify({"error": "unauthorized"}), 401

        evento = payload.get("event", "")
        if evento not in ("messages.upsert", "MESSAGES_UPSERT"):
            return jsonify({"status": "ignored", "event": evento}), 200

        data = payload.get("data", {})
        msg_obj = data if "message" in data else payload

        if msg_obj.get("key", {}).get("fromMe"):
            return jsonify({"status": "ignored", "reason": "own message"}), 200

        message = msg_obj.get("message", {})
        texto = (
            message.get("conversation")
            or message.get("extendedTextMessage", {}).get("text")
            or ""
        )

        if not texto:
            return jsonify({"status": "ignored", "reason": "no text"}), 200

        remote_jid = msg_obj.get("key", {}).get("remoteJid", "")
        if "@g.us" not in remote_jid:
            return jsonify({"status": "ignored", "reason": "not a group"}), 200

        if GRUPOS_FILTRO and GRUPOS_FILTRO[0]:
            if not any(g.strip() in remote_jid for g in GRUPOS_FILTRO):
                return jsonify({"status": "ignored", "reason": "group not in filter"}), 200

        # Verifica se tem algum conteúdo relevante
        tem_usina = bool(re.search(r"Usina:", texto, re.IGNORECASE))
        tem_emoji = bool(re.search(r"🔴|🟡|🟢|🟠|✅|⏸️", texto))
        if not tem_usina and not tem_emoji:
            return jsonify({"status": "ignored", "reason": "no failure content"}), 200

        resultado = processar_texto(texto)

        total = len(resultado["novos"]) + len(resultado["atualizados"]) + len(resultado["normalizados"])
        if total > 0:
            log.info(f"✅ Processado: {len(resultado['novos'])} novos, {len(resultado['atualizados'])} atualizados, {len(resultado['normalizados'])} normalizados")
            return jsonify({"status": "ok", **resultado}), 200

        return jsonify({"status": "ignored", "reason": "no valid content"}), 200

    except Exception as e:
        log.error(f"❌ Erro: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()}), 200


@app.route("/test", methods=["POST"])
def test_parse():
    """Testa o parse sem gravar na planilha."""
    payload = request.get_json(force=True) or {}
    texto = payload.get("texto", "")
    blocos = separar_blocos(texto)
    resultados = []
    for b in blocos:
        r = parse_bloco(b)
        if r:
            resultados.append(r)
    return jsonify({"total_blocos": len(blocos), "validos": len(resultados), "resultados": resultados}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
