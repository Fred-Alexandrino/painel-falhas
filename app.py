"""
app.py — Servidor principal
Recebe webhooks do WPPConnect/Baileys, parseia mensagens de falha
e grava automaticamente no Google Sheets.
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
    # RENOGRID
    "nova xavantina i": "RENOGRID", "nova xavantina 1": "RENOGRID",
    "nova xavantina ii": "RENOGRID", "nova xavantina 2": "RENOGRID",
    "nova xavantina": "RENOGRID",
    "colíder i": "RENOGRID", "colider i": "RENOGRID",
    "colíder 1": "RENOGRID", "colider 1": "RENOGRID",
    "colíder ii": "RENOGRID", "colider ii": "RENOGRID",
    "colíder 2": "RENOGRID", "colider 2": "RENOGRID",
    "colíder": "RENOGRID", "colider": "RENOGRID",
    "nobres": "RENOGRID",
    "elias fausto": "RENOGRID",
    "crateús": "RENOGRID", "crateus": "RENOGRID",
    # THOPEN
    "boa esperança do sul 1": "THOPEN", "boa esperanca do sul 1": "THOPEN",
    "boa esperança do sul 2": "THOPEN", "boa esperanca do sul 2": "THOPEN",
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
    # 2C
    "araputanga": "2C",
    "sete lagoas": "2C",
    # GD Energy
    "guajirú": "GD Energy", "guajiru": "GD Energy",
    "sol do norte i": "GD Energy", "sol do norte 1": "GD Energy",
    "sol do norte ii": "GD Energy", "sol do norte 2": "GD Energy",
    "sol do norte": "GD Energy",
    # Alves Lima
    "abc morada nova": "Alves Lima",
}

# ── Usinas permitidas (só essas sobem na planilha) ────────────────────────────
USINAS_PERMITIDAS = set(CLIENTE_POR_USINA.keys())

STATUS_VALIDOS = {
    "em aberto": "Em Aberto",
    "aberto": "Em Aberto",
    "concluído": "Concluído", "concluido": "Concluído",
    "resolvido": "Concluído",
    "aguardando cliente": "Aguardando Cliente",
    "aguardando fabricante": "Aguardando Fabricante",
    "aguardando equipamento": "Aguardando Equipamento",
}

# ── Padrões de extração ───────────────────────────────────────────────────────
PADROES = {
    "usina":       re.compile(r"Usina:[ \t]*([^\n\r]+)",                    re.IGNORECASE),
    "problema":    re.compile(r"Problema:[ \t]*([^\n\r]+)",                 re.IGNORECASE),
    "descricao":   re.compile(r"Descrição dos Problemas:[ \t]*([^\n\r]+)",  re.IGNORECASE),
    "acao":        re.compile(r"Ação:[ \t]*([^\n\r]+)",                     re.IGNORECASE),
    "equipe":      re.compile(r"Equipe Acionada:[ \t]*([^\n\r]+)",          re.IGNORECASE),
    "supervisor":  re.compile(r"Supervisor Acionado:[ \t]*([^\n\r]+)",      re.IGNORECASE),
    "inicio":      re.compile(r"Inicio ocorrência:[ \t]*([^\n\r]+)",        re.IGNORECASE),
    "fim":         re.compile(r"Fim ocorrência:[ \t]*([^\n\r]*)",           re.IGNORECASE),
    "os":          re.compile(r"N[ºo°][ \t]*da[ \t]*OS:[ \t]*([^\n\r]+)",  re.IGNORECASE),
    "equipamento": re.compile(r"^\*?[ \t]*Equipamento[^:\n]*:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "causa":       re.compile(r"^\*?[ \t]*Causa[^:\n]*:[ \t]*([^\n\r]+)",       re.IGNORECASE | re.MULTILINE),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def extrair(texto, padrao):
    m = padrao.search(texto)
    return m.group(1).strip().lstrip("*").strip() if m else ""

def vazio(v):
    return not v or str(v).strip() in ("", "--", "-", "N/A", "n/a")

def inferir_cliente(usina):
    u = usina.lower().strip()
    # Tenta match exato primeiro
    if u in CLIENTE_POR_USINA:
        return CLIENTE_POR_USINA[u]
    # Tenta match parcial
    for chave, cliente in CLIENTE_POR_USINA.items():
        if chave in u or u in chave:
            return cliente
    return ""

def usina_permitida(usina):
    """Verifica se a usina está na lista de usinas do Fred."""
    u = usina.lower().strip()
    if u in USINAS_PERMITIDAS:
        return True
    for permitida in USINAS_PERMITIDAS:
        if permitida in u or u in permitida:
            return True
    return False

def normalizar_status(texto, fim_valido):
    if fim_valido:
        return "Concluído"
    t = str(texto).lower()
    for chave, val in STATUS_VALIDOS.items():
        if chave in t:
            return val
    return "Em Aberto"

def extrair_tecnico(s):
    m = re.search(r"@([\w\s]+?)(?:\s*[-–]\s*[\w-]+)?\s*$", s)
    return m.group(1).strip() if m else re.sub(r"^[Ss]im[,\s]*", "", s).strip()

def inferir_equipamento(problema, descricao):
    fonte = problema or descricao
    m = re.search(
        r"(INV-\d+|Inversor\s+\d+|Tracker\s+\d+|Motor[\w\s/TCU]*\d+|"
        r"Câmera[\w\s]*|NVR|GCU|Relé[\w\s]*|Chave[\w\s]+|RSU|"
        r"Piranometro[\w\s]+|Fieldlogger|Anemômetro|Exaustor[\w\s]*|"
        r"Nobreak[\w\s]*|EP\d+|Igate[\w\s]*|Otimizador[\w\s]*|"
        r"Fieldlogger|TCU[\w\s]*|Bateria[\w\s]*|String[\w\s]*)",
        fonte, re.IGNORECASE
    )
    if m:
        return m.group(0).strip()
    m2 = re.search(r"([A-Za-zÀ-ÿ]+)\s+(?:\w+\s+)?(\d+)\s*$", fonte)
    if m2:
        return f"{m2.group(1).capitalize()} {m2.group(2)}"
    return fonte[:60] if fonte else ""

def separar_ocorrencias(texto):
    partes = re.split(r"(?=🔴|🟡|🟢|🟠)", texto)
    return [p.strip() for p in partes if p.strip() and len(p.strip()) > 20]

def parse_mensagem(texto):
    c = {k: extrair(texto, p) for k, p in PADROES.items()}
    if not c["usina"]:
        return None

    # Verifica se é uma usina permitida
    if not usina_permitida(c["usina"]):
        log.info(f"⚪ Usina não permitida, ignorando: {c['usina']}")
        return None

    equip = c["equipamento"] if not vazio(c["equipamento"]) else inferir_equipamento(c["problema"], c["descricao"])
    causa = c["causa"] if not vazio(c["causa"]) else (c["descricao"] if not vazio(c["descricao"]) else "Em análise")

    partes_acao = []
    if not vazio(c["acao"]): partes_acao.append(c["acao"])
    tec = extrair_tecnico(c["equipe"]) if not vazio(c["equipe"]) else ""
    if not vazio(tec): partes_acao.append(f"Técnico: {tec}")
    sup = re.sub(r"^[Ss]im[,\s]*", "", c["supervisor"]).strip() if not vazio(c["supervisor"]) else ""
    if not vazio(sup): partes_acao.append(f"Supervisor: {sup}")

    fim_valido = not vazio(c["fim"])
    status = normalizar_status("", fim_valido)

    # Histórico no padrão DD/MM - texto
    hoje = datetime.now().strftime("%d/%m")
    hist = []
    if not vazio(c["inicio"]):
        m_data = re.search(r"(\d{2}/\d{2})", c["inicio"])
        data_fmt = m_data.group(1) if m_data else hoje
        hist.append(f"{data_fmt} - Registro inicial")
    else:
        hist.append(f"{hoje} - Registro inicial")
    if not vazio(c["acao"]):
        hist.append(f"{hoje} - {c['acao']}")
    if fim_valido:
        m_data = re.search(r"(\d{2}/\d{2})", c["fim"])
        data_fmt = m_data.group(1) if m_data else hoje
        hist.append(f"{data_fmt} - Ocorrência encerrada")

    return {
        "cliente":      inferir_cliente(c["usina"]),
        "usina":        c["usina"],
        "equipamento":  equip,
        "falha":        c["problema"] or c["descricao"],
        "causa":        causa,
        "equip_impact": equip,
        "acao":         " | ".join(partes_acao),
        "status":       status,
        "historico":    "\n".join(hist),
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

def proximo_id_e_linha(ws):
    todos = ws.get_all_values()
    maior_id = 0
    ultima_linha_dados = 1
    for i, row in enumerate(todos[1:], start=2):
        if row and row[0] and str(row[0]).strip():
            ultima_linha_dados = i
            try:
                maior_id = max(maior_id, int(row[0]))
            except ValueError:
                pass
    return maior_id + 1, ultima_linha_dados + 1

def gravar_ocorrencia(dados):
    ws = get_sheet()
    novo_id, proxima_linha = proximo_id_e_linha(ws)

    # Ordem exata das colunas:
    # A=ID | B=Cliente | C=Usina | D=Equipamento | E=Falha | F=Causa
    # G=Equipamentos impactados | H=Ação | I=Status atual
    # J=Ticket Fabricante | K=Número da OS | L=Histórico Cronológico
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
        "",                  # J - Ticket Fabricante (preencher manualmente)
        "",                  # K - Número da OS (preencher manualmente)
        dados["historico"],  # L - Histórico Cronológico
    ]

    ws.insert_row(linha, proxima_linha)
    log.info(f"✅ ID={novo_id} | {dados['usina']} — {dados['equipamento']} | linha {proxima_linha}")
    return novo_id


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
        eh_grupo = "@g.us" in remote_jid
        if not eh_grupo:
            return jsonify({"status": "ignored", "reason": "not a group message"}), 200

        if GRUPOS_FILTRO and GRUPOS_FILTRO[0]:
            if not any(g.strip() in remote_jid for g in GRUPOS_FILTRO):
                return jsonify({"status": "ignored", "reason": "group not in filter"}), 200

        ocorrencias = separar_ocorrencias(texto) or [texto]
        gravados = []
        ignorados = []

        for bloco in ocorrencias:
            dados = parse_mensagem(bloco)
            if dados:
                novo_id = gravar_ocorrencia(dados)
                gravados.append({"id": novo_id, "usina": dados["usina"]})
            else:
                ignorados.append("não é falha ou usina não permitida")

        if gravados:
            log.info(f"✅ {len(gravados)} ocorrência(s) gravada(s): {gravados}")
            return jsonify({"status": "ok", "gravados": gravados}), 200

        return jsonify({"status": "ignored", "reason": "no valid failures found"}), 200

    except Exception as e:
        log.error(f"❌ Erro: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()}), 200


@app.route("/test", methods=["POST"])
def test_parse():
    payload = request.get_json(force=True) or {}
    texto = payload.get("texto", "")
    ocorrencias = separar_ocorrencias(texto) or [texto]
    resultados = [parse_mensagem(b) for b in ocorrencias]
    return jsonify({"resultados": [r for r in resultados if r]}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
