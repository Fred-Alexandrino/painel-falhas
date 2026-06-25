"""
app.py — Servidor principal
Recebe webhooks da Evolution API, parseia mensagens de falha
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

# ── Configuração ─────────────────────────────────────────────────────────────

SHEET_ID        = os.environ.get("SHEET_ID", "1VLo8__wxSJVWiUIFd_JTcOnadJlUt440i1M1pC0ehTs")
SHEET_NAME      = os.environ.get("SHEET_NAME", "Acompanhamento de Falhas - O&M V2")
WEBHOOK_SECRET  = os.environ.get("WEBHOOK_SECRET", "")          # opcional mas recomendado
GRUPOS_FILTRO   = os.environ.get("GRUPOS_IDS", "").split(",")   # IDs dos grupos, separados por vírgula

# Mapeamento Usina → Cliente (baseado na sua planilha real)
CLIENTE_POR_USINA = {
    "ibaté": "THOPEN", "ibate": "THOPEN",
    "boa esperança": "THOPEN", "boa esperanca": "THOPEN",
    "matão": "THOPEN", "matao": "THOPEN",
    "poconé": "THOPEN", "pocone": "THOPEN",
    "sítio bonfim": "THOPEN", "sitio bonfim": "THOPEN",
    "topázio": "THOPEN", "topazio": "THOPEN",
    "colíder": "RENOGRID", "colider": "RENOGRID",
    "elias fausto": "RENOGRID",
    "nobres": "RENOGRID",
    "nova xavantina": "RENOGRID",
    "araputanga": "2C",
}

STATUS_VALIDOS = {
    "em aberto": "Em Aberto",
    "aberto": "Em Aberto",
    "concluído": "Concluído",
    "concluido": "Concluído",
    "resolvido": "Concluído",
    "aguardando cliente": "Aguardando Cliente",
    "aguardando fabricante": "Aguardando Fabricante",
    "aguardando equipamento": "Aguardando Equipamento",
}

# Padrões de extração — usa [ \t]* para não cruzar linhas
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
    "impacto":     re.compile(r"Impacto:[ \t]*([^\n\r]+)",                  re.IGNORECASE),
}


# ── Helpers de parse ─────────────────────────────────────────────────────────

def extrair(texto, padrao):
    m = padrao.search(texto)
    return m.group(1).strip().lstrip("*").strip() if m else ""

def vazio(v):
    return not v or str(v).strip() in ("", "--", "-", "N/A", "n/a")

def inferir_cliente(usina):
    u = usina.lower()
    for chave, cliente in CLIENTE_POR_USINA.items():
        if chave in u:
            return cliente
    return ""

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
        r"Câmera[\w\s]*|NVR|GCU|Relé[\w\s]*|Chave[\w\s]+|"
        r"Piranometro[\w\s]+|Fieldlogger|Anemômetro|Exaustor[\w\s]*|"
        r"Nobreak[\w\s]*|EP\d+|Igate[\w\s]*)",
        fonte, re.IGNORECASE
    )
    if m:
        return m.group(0).strip()
    m2 = re.search(r"([A-Za-zÀ-ÿ]+)\s+(?:\w+\s+)?(\d+)\s*$", fonte)
    if m2:
        return f"{m2.group(1).capitalize()} {m2.group(2)}"
    return (fonte[:60] if fonte else "")

def separar_ocorrencias(texto):
    partes = re.split(r"(?=🔴|🟡|🟢|🟠)", texto)
    return [p.strip() for p in partes if p.strip() and len(p.strip()) > 20]

def parse_mensagem(texto):
    c = {k: extrair(texto, p) for k, p in PADROES.items()}
    if not c["usina"]:
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
    status = normalizar_status(c.get("status", ""), fim_valido)

    hoje = datetime.now().strftime("%d/%m/%Y %H:%M")
    hist = []
    if not vazio(c["inicio"]):
        hist.append(f"{c['inicio']} - Abertura da ocorrência")
    if not vazio(c["descricao"]):
        hist.append(f"{hoje} - {c['descricao']}")
    if not vazio(c["acao"]):
        hist.append(f"{hoje} - {c['acao']}")
    if fim_valido:
        hist.append(f"{c['fim']} - Ocorrência encerrada")

    return {
        "cliente":     inferir_cliente(c["usina"]),
        "usina":       c["usina"],
        "equipamento": equip,
        "falha":       c["problema"] or c["descricao"],
        "causa":       causa,
        "equip_impact": equip,
        "acao":        " | ".join(partes_acao),
        "status":      status,
        "historico":   "\n".join(hist),
    }


# ── Google Sheets ─────────────────────────────────────────────────────────────

def get_sheet():
    """Conecta ao Google Sheets via Service Account."""
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
    """Retorna (próximo_id, próxima_linha) baseado nos dados existentes."""
    todos = ws.get_all_values()
    maior_id = 0
    ultima_linha_dados = 1  # linha 1 = cabeçalho
    for i, row in enumerate(todos[1:], start=2):  # pula cabeçalho
        if row and row[0] and str(row[0]).strip():
            ultima_linha_dados = i
            try:
                maior_id = max(maior_id, int(row[0]))
            except ValueError:
                pass
    return maior_id + 1, ultima_linha_dados + 1

def gravar_ocorrencia(dados):
    """Grava uma linha nova na planilha."""
    ws = get_sheet()
    novo_id, proxima_linha = proximo_id_e_linha(ws)

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
        dados["historico"],
    ]

    ws.insert_row(linha, proxima_linha)
    log.info(f"✅ Gravado ID={novo_id} | {dados['usina']} — {dados['equipamento']} na linha {proxima_linha}")
    return novo_id


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    """Recebe eventos da Evolution API."""
    try:
        payload = request.get_json(force=True)
        if not payload:
            return jsonify({"status": "ignored", "reason": "empty payload"}), 200

        # Valida secret opcional
        if WEBHOOK_SECRET:
            secret = request.headers.get("X-Webhook-Secret", "")
            if secret != WEBHOOK_SECRET:
                return jsonify({"error": "unauthorized"}), 401

        evento = payload.get("event", "")
        log.info(f"Evento recebido: {evento}")

        # Só processa mensagens recebidas
        if evento not in ("messages.upsert", "MESSAGES_UPSERT"):
            return jsonify({"status": "ignored", "event": evento}), 200

        data = payload.get("data", {})
        msg_obj = data if "message" in data else payload

        # Ignora mensagens próprias
        if msg_obj.get("key", {}).get("fromMe"):
            return jsonify({"status": "ignored", "reason": "own message"}), 200

        # Extrai texto
        message = msg_obj.get("message", {})
        texto = (
            message.get("conversation")
            or message.get("extendedTextMessage", {}).get("text")
            or ""
        )

        if not texto:
            return jsonify({"status": "ignored", "reason": "no text"}), 200

        # Filtra por grupos se configurado
        remote_jid = msg_obj.get("key", {}).get("remoteJid", "")
        eh_grupo = "@g.us" in remote_jid
        if not eh_grupo:
            return jsonify({"status": "ignored", "reason": "not a group message"}), 200

        if GRUPOS_FILTRO and GRUPOS_FILTRO[0]:
            if not any(g.strip() in remote_jid for g in GRUPOS_FILTRO):
                return jsonify({"status": "ignored", "reason": "group not in filter"}), 200

        # Processa ocorrências na mensagem
        ocorrencias = separar_ocorrencias(texto)
        if not ocorrencias:
            # Tenta a mensagem inteira como uma única ocorrência
            ocorrencias = [texto]

        gravados = []
        for bloco in ocorrencias:
            dados = parse_mensagem(bloco)
            if dados:
                novo_id = gravar_ocorrencia(dados)
                gravados.append({"id": novo_id, "usina": dados["usina"], "equipamento": dados["equipamento"]})

        if gravados:
            log.info(f"✅ {len(gravados)} ocorrência(s) gravada(s): {gravados}")
            return jsonify({"status": "ok", "gravados": gravados}), 200
        else:
            log.info("⚪ Mensagem recebida mas não é uma ocorrência de falha")
            return jsonify({"status": "ignored", "reason": "not a failure message"}), 200

    except Exception as e:
        log.error(f"❌ Erro no webhook: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()}), 200


@app.route("/test", methods=["POST"])
def test_parse():
    """Endpoint para testar o parse sem gravar na planilha."""
    payload = request.get_json(force=True) or {}
    texto = payload.get("texto", "")
    ocorrencias = separar_ocorrencias(texto) or [texto]
    resultados = [parse_mensagem(b) for b in ocorrencias]
    return jsonify({"resultados": [r for r in resultados if r]}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
