"""
app.py — Servidor principal
Recebe webhooks do Baileys, parseia mensagens de falha
e grava automaticamente no Google Sheets.

Dois fluxos de entrada:
  1. POST /webhook  — mensagens em tempo real enviadas pelo server.js
  2. POST /rondas   — chamado pelo botão do dashboard; busca as últimas
                      6 horas de histórico em cada grupo via server.js
                      e processa as mensagens encontradas

Suporta:
- Mensagens individuais de ocorrência (🔴/🟡/🟢/🟠)
- Mensagens de normalização (✅ + "NORMALIZADO")
- Rondas diárias completas (múltiplas ocorrências em uma mensagem)
- Formato Cos Grid com bullets (·) sem emojis
"""

import os, re, json, logging, time, random, base64, uuid
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials
from relatorio_semanal import (coletar_ocorrencias_semana, coletar_atividades_semana,
                                mesclar_grupos, gerar_relatorio_pptx,
                                listar_usinas_cliente,
                                coletar_zeladoria)

# Push notifications (pywebpush)
try:
    from pywebpush import webpush, WebPushException
    PUSH_ENABLED = True
except ImportError:
    PUSH_ENABLED = False
    log_push = logging.getLogger(__name__)
    log_push.warning("pywebpush não instalado — notificações push desabilitadas")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Fuso horário ─────────────────────────────────────────────────────────
# O Render roda o servidor em UTC (sem TZ configurada). datetime.now() puro
# retorna UTC, mas todo timestamp gravado no histórico/planilha é lido por
# humanos no Brasil (GMT-3). Sem essa conversão, todo horário registrado no
# sistema aparece 3h à frente do horário real de Brasília. Use agora_br()
# em vez de datetime.now() em qualquer lugar que grave/exiba horário local.
_TZ_BR = ZoneInfo("America/Sao_Paulo")


def agora_br():
    """Retorna o datetime atual já convertido para o horário de Brasília (GMT-3)."""
    return datetime.now(_TZ_BR)


log = logging.getLogger(__name__)

app = Flask(__name__)

# Permite requisições do GitHub Pages e de qualquer origem
# (o dashboard fica em fred-alexandrino.github.io)
CORS(app, resources={r"/*": {"origins": "*"}})


@app.errorhandler(Exception)
def _tratar_erro_nao_previsto(e):
    """Rede de segurança global: sem isso, qualquer exceção não tratada
    em qualquer endpoint vira a página de erro HTML padrão do Flask/
    Werkzeug — e o frontend, que sempre espera JSON, quebra com
    'Unexpected token '<'' em vez de mostrar o erro real. Preserva o
    código HTTP de erros conhecidos (ex.: HTTPException do Werkzeug,
    como 404) e usa 500 pra qualquer outra coisa inesperada."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify({"ok": False, "error": e.description or str(e)}), e.code
    log.error(f"[erro-nao-tratado] {request.method} {request.path}: {e}")
    return jsonify({"ok": False, "error": f"Erro interno inesperado: {e}"}), 500

# ── Configuração ──────────────────────────────────────────────────────────────
SHEET_ID       = os.environ.get("SHEET_ID", "1VLo8__wxSJVWiUIFd_JTcOnadJlUt440i1M1pC0ehTs")
SHEET_NAME     = os.environ.get("SHEET_NAME", "Painel de Falhas - Fred Alexandrino")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
SHEET_EDIT_SECRET = os.environ.get("SHEET_EDIT_SECRET", "")
GRUPOS_FILTRO  = os.environ.get("GRUPOS_IDS", "").split(",")

# ── Configuração VAPID para notificações push ────────────────────────────────
VAPID_PUBLIC_KEY  = os.environ.get("VAPID_PUBLIC_KEY", "BJyGD9Lno29xj3_a6i5MjSHoZhHwfev7bRJRCqjnyL-o1vo9Hbf2zmrNtoONHtA92F59LGLc52HNE7oUkKqs5Yk")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
VAPID_CLAIMS      = {"sub": "mailto:fred@gridco.com.br"}

# Subscriptions em memória (persistidas na planilha em produção)
# { endpoint: subscription_json }
_push_subscriptions = {}

# ── URL do servidor WhatsApp (Baileys) — usado pelo endpoint /rondas (Baileys) — usado pelo endpoint /rondas
WPP_SERVER_URL = os.environ.get("WPP_SERVER_URL", "").rstrip("/")

# Nome da aba de log de mensagens
LOG_SHEET_NAME = "Log de Mensagens"

# Nome da aba onde as subscriptions de push são persistidas (sobrevive a reinícios do Render)
PUSH_SHEET_NAME = "Push Subscriptions"

# ── Cache de credenciais Google (reutiliza a conexão) ────────────────────────
_gc_cache = None

def get_gc():
    global _gc_cache
    if _gc_cache is None:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if not creds_json:
            raise ValueError("GOOGLE_CREDENTIALS_JSON não configurado")
        creds_dict = json.loads(creds_json)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        from google.oauth2.service_account import Credentials as _Creds
        creds = _Creds.from_service_account_info(creds_dict, scopes=scopes)
        _gc_cache = gspread.authorize(creds)
    return _gc_cache


def _gspread_retry(fn, tentativas=4, esperas=(3, 6, 12, 20)):
    """Executa uma chamada ao Google Sheets com retry exponencial em caso
    de erro 429 (cota de leitura/escrita por minuto excedida — limite
    padrão do Google é 60 requisições/min por usuário). Ficou comum
    depois que as funcionalidades de fotos/graus de Zeladoria passaram a
    fazer várias chamadas em sequência rápida (uma leitura + escrita por
    usina, por lote). Sem isso, o erro sobe cru como um 500 genérico pro
    frontend. `fn` deve ser uma função sem argumentos (lambda ou closure)."""
    ultima_excecao = None
    for tentativa in range(tentativas):
        try:
            return fn()
        except gspread.exceptions.APIError as e:
            ultima_excecao = e
            corpo = str(e)
            if ("429" in corpo or "Quota exceeded" in corpo) and tentativa < tentativas - 1:
                time.sleep(esperas[tentativa])
                continue
            raise
    raise ultima_excecao

def get_log_sheet():
    """Retorna a aba 'Log de Mensagens' da planilha."""
    gc = get_gc()
    return gc.open_by_key(SHEET_ID).worksheet(LOG_SHEET_NAME)

def gravar_log_mensagem(grupo_id, grupo_nome, texto):
    """
    Grava uma mensagem recebida na aba 'Log de Mensagens'.
    Colunas: Timestamp | GrupoId | GrupoNome | Texto | Processado
    """
    try:
        ws_log = get_log_sheet()
        ts = agora_br().strftime("%d/%m/%Y %H:%M:%S")
        ws_log.append_row([ts, grupo_id, grupo_nome, texto, ""])
        log.info(f"📝 [Log] Mensagem gravada: {grupo_id}")
    except Exception as e:
        log.error(f"❌ [Log] Erro ao gravar mensagem: {e}")

_log_cache = {"ts": 0, "rows": None}

def ler_log_mensagens(horas=6):
    """
    Lê mensagens do log das últimas N horas.
    Usa cache de 60s para não estourar quota da API do Google.
    Retorna lista de dicts com grupo_id, texto, timestamp.
    """
    import time
    try:
        ws_log = get_log_sheet()
        # Cache de 60s para evitar quota exceeded
        agora = time.time()
        if agora - _log_cache["ts"] > 60 or _log_cache["rows"] is None:
            _log_cache["rows"] = ws_log.get_all_values()
            _log_cache["ts"]   = agora
            log.info("[Log] Cache atualizado")
        rows = _log_cache["rows"]
        if len(rows) < 2:
            return []

        desde = agora_br().timestamp() - (horas * 3600)
        mensagens = []

        for row in rows[1:]:  # pula cabeçalho
            if len(row) < 4: continue
            ts_str     = row[0].strip()
            grupo_id   = row[1].strip()
            texto      = row[3].strip()
            processado = row[4].strip() if len(row) > 4 else ""
            # Pula mensagens já processadas pelo botão Verificar Rondas
            if processado == "✅": continue
            if not texto or not grupo_id: continue

            # Converte timestamp
            try:
                from datetime import datetime as _dt
                dt = _dt.strptime(ts_str, "%d/%m/%Y %H:%M:%S")
                ts = dt.timestamp()
            except:
                continue

            if ts < desde: continue
            mensagens.append({"grupo_id": grupo_id, "texto": texto, "timestamp": ts_str, "linha_idx": rows[1:].index(row) + 2})

        log.info(f"[Log] {len(mensagens)} mensagens nas últimas {horas}h")
        return mensagens
    except Exception as e:
        log.error(f"❌ [Log] Erro ao ler mensagens: {e}")
        return []

def marcar_processado(ws_log, linha_idx):
    """Marca uma linha do log como processada (coluna E)."""
    try:
        ws_log.update_cell(linha_idx, 5, "✅")
    except:
        pass

def ler_log_historico(horas=24):
    """
    Lê TODAS as mensagens do log das últimas N horas — incluindo já processadas.
    Usada pelo endpoint /rondas/grupos para exibição histórica (somente leitura).
    """
    import time
    try:
        ws_log = get_log_sheet()
        agora = time.time()
        if agora - _log_cache["ts"] > 60 or _log_cache["rows"] is None:
            _log_cache["rows"] = ws_log.get_all_values()
            _log_cache["ts"]   = agora
        rows = _log_cache["rows"]
        if len(rows) < 2:
            return []

        desde = agora_br().timestamp() - (horas * 3600)
        mensagens = []
        for row in rows[1:]:
            if len(row) < 4: continue
            ts_str   = row[0].strip()
            grupo_id = row[1].strip()
            texto    = row[3].strip()
            processado = row[4].strip() if len(row) > 4 else ""
            if not texto or not grupo_id: continue
            try:
                from datetime import datetime as _dt
                dt = _dt.strptime(ts_str, "%d/%m/%Y %H:%M:%S")
                ts = dt.timestamp()
            except:
                continue
            if ts < desde: continue
            mensagens.append({
                "grupo_id":   grupo_id,
                "texto":      texto,
                "timestamp":  ts_str,
                "processado": processado == "✅",
                "linha_idx":  rows[1:].index(row) + 2
            })
        return mensagens
    except Exception as e:
        log.error(f"❌ [Log] Erro ao ler histórico: {e}")
        return []

def limpar_log_antigo():
    """
    Remove linhas do 'Log de Mensagens' com mais de 5 dias.
    Chamado automaticamente no endpoint /rondas.
    """
    import time
    try:
        ws_log = get_log_sheet()
        rows = ws_log.get_all_values()
        if len(rows) < 2:
            return 0

        limite = agora_br().timestamp() - (5 * 24 * 3600)
        linhas_deletar = []

        for i, row in enumerate(rows[1:], start=2):  # pula cabeçalho
            if len(row) < 1: continue
            ts_str = row[0].strip()
            try:
                from datetime import datetime as _dt
                dt = _dt.strptime(ts_str, "%d/%m/%Y %H:%M:%S")
                if dt.timestamp() < limite:
                    linhas_deletar.append(i)
            except:
                continue

        if not linhas_deletar:
            return 0

        # Deleta de baixo para cima (evita deslocamento de índices)
        for idx in reversed(linhas_deletar):
            ws_log.delete_rows(idx)

        # Invalida cache após limpeza
        _log_cache["ts"]   = 0
        _log_cache["rows"] = None

        log.info(f"🧹 [Log] {len(linhas_deletar)} linha(s) antigas removidas (>5 dias)")
        return len(linhas_deletar)
    except Exception as e:
        log.error(f"❌ [Log] Erro ao limpar log antigo: {e}")
        return 0

# ══════════════════════════════════════════════════════════════════════════════
# CATÁLOGO CANÔNICO DE USINAS
#
# Estrutura: nome_oficial → { cliente, aliases: [lista de variações] }
#
# Regras gerais aplicadas automaticamente pela função canonizar_usina():
#   - Remove prefixos "UFV ", "Usina ", "UFV Usina "
#   - Normaliza acentos para comparação (ç→c, ã→a, etc.)
#   - Trata 1/I/A/1A/IA como sufixo "1" e 2/II/B/1B/IB como sufixo "2"
#   - Usinas sem alias explícito são reconhecidas pelo nome base
# ══════════════════════════════════════════════════════════════════════════════

CATALOGO_USINAS = {
    # ── RENOGRID ──────────────────────────────────────────────────────────────
    "Nova Xavantina I": {
        "cliente": "RENOGRID",
        "aliases": [
            "nova xavantina 1", "nova xavantina i",
            "xavantina 1", "xavantina i",
            "nova xavantina 1a", "nova xavantina ia",
            "xavantina 1a", "xavantina ia",
        ],
    },
    "Nova Xavantina II": {
        "cliente": "RENOGRID",
        "aliases": [
            "nova xavantina 2", "nova xavantina ii",
            "xavantina 2", "xavantina ii",
            "nova xavantina 1b", "nova xavantina ib",
            "xavantina 1b", "xavantina ib",
        ],
    },
    "Colíder I": {
        "cliente": "RENOGRID",
        "aliases": [
            "colider i", "colider 1", "colíder 1", "colíder i",
            "colider 1a", "colider ia", "colíder 1a", "colíder ia",
        ],
    },
    "Colíder II": {
        "cliente": "RENOGRID",
        "aliases": [
            "colider ii", "colider 2", "colíder 2", "colíder ii",
            "colider 1b", "colider ib", "colíder 1b", "colíder ib",
        ],
    },
    "Nobres": {
        "cliente": "RENOGRID",
        "aliases": ["nobres"],
    },
    "Elias Fausto": {
        "cliente": "RENOGRID",
        "aliases": ["elias fausto"],
    },
    "Crateús": {
        "cliente": "RENOGRID",
        "aliases": ["crateus", "crateús", "cratéus"],
    },

    # ── THOPEN ────────────────────────────────────────────────────────────────
    "Boa Esperança do Sul I": {
        "cliente": "THOPEN",
        "aliases": [
            "boa esperanca do sul i", "boa esperanca do sul 1",
            "boa esperanca do sul a", "boa esperanca do sul 1a",
            "boa esperanca do sul ia",
            "boa esperança do sul i", "boa esperança do sul 1",
            "boa esperança do sul a", "boa esperança do sul 1a",
            "boa esperança do sul ia",
            "boa esperanca i", "boa esperanca 1",
            "boa esperança i", "boa esperança 1",
        ],
    },
    "Boa Esperança do Sul II": {
        "cliente": "THOPEN",
        "aliases": [
            "boa esperanca do sul ii", "boa esperanca do sul 2",
            "boa esperanca do sul b", "boa esperanca do sul 1b",
            "boa esperanca do sul ib",
            "boa esperança do sul ii", "boa esperança do sul 2",
            "boa esperança do sul b", "boa esperança do sul 1b",
            "boa esperança do sul ib",
            "boa esperanca ii", "boa esperanca 2",
            "boa esperança ii", "boa esperança 2",
        ],
    },
    "Ibaté I": {
        "cliente": "THOPEN",
        "aliases": [
            "ibate i", "ibate 1", "ibate 1a", "ibate ia", "ibate a",
            "ibaté i", "ibaté 1", "ibaté 1a", "ibaté ia", "ibaté a",
        ],
    },
    "Ibaté II": {
        "cliente": "THOPEN",
        "aliases": [
            "ibate ii", "ibate 2", "ibate 1b", "ibate ib", "ibate b",
            "ibaté ii", "ibaté 2", "ibaté 1b", "ibaté ib", "ibaté b",
        ],
    },
    "Matão 1": {
        "cliente": "THOPEN",
        "aliases": [
            "matao 1", "matao i", "matao 1a", "matao ia", "matao a",
            "matão 1", "matão i", "matão 1a", "matão ia", "matão a",
        ],
    },
    "Matão II - Topázio": {
        "cliente": "THOPEN",
        "aliases": [
            "matao 2", "matao ii", "matao 1b", "matao ib", "matao b",
            "matão 2", "matão ii", "matão 1b", "matão ib", "matão b",
            "matao 2 topazio", "matão 2 topázio",
            "topazio", "topázio",
        ],
    },
    "Sítio Bonfim": {
        "cliente": "THOPEN",
        "aliases": [
            "sitio bonfim", "sítio bonfim",
            "bonfim",
        ],
    },
    "Poconé": {
        "cliente": "THOPEN",
        "aliases": ["pocone", "poconé", "poconé"],
    },
    "Canarana I": {
        "cliente": "THOPEN",
        "aliases": [
            "canarana i", "canarana 1", "canarana 1a", "canarana ia", "canarana a",
        ],
    },
    "Canarana II": {
        "cliente": "THOPEN",
        "aliases": [
            "canarana ii", "canarana 2", "canarana 1b", "canarana ib", "canarana b",
        ],
    },
    "Ribeirão Cascalheira": {
        "cliente": "THOPEN",
        "aliases": [
            "ribeirao cascalheira", "ribeirão cascalheira",
            "ribeirao", "cascalheira",
        ],
    },

    # ── 2C ───────────────────────────────────────────────────────────────────
    "Araputanga": {
        "cliente": "2C",
        "aliases": ["araputanga"],
    },
    "Sete Lagoas": {
        "cliente": "2C",
        "aliases": ["sete lagoas"],
    },

    # ── GD Energy ─────────────────────────────────────────────────────────────
    "Guajirú": {
        "cliente": "GD Energy",
        "aliases": ["guajiru", "guajirú", "guajiru"],
    },
    "Sol do Norte I": {
        "cliente": "GD Energy",
        "aliases": [
            "sol do norte i", "sol do norte 1",
            "sol do norte 1a", "sol do norte ia", "sol do norte a",
        ],
    },
    "Sol do Norte II": {
        "cliente": "GD Energy",
        "aliases": [
            "sol do norte ii", "sol do norte 2",
            "sol do norte 1b", "sol do norte ib", "sol do norte b",
        ],
    },

    # ── Alves Lima ────────────────────────────────────────────────────────────
    "ABC Morada Nova": {
        "cliente": "Alves Lima",
        "aliases": ["abc morada nova", "morada nova"],
    },
}

# ── Índice invertido: alias_normalizado → nome_oficial ────────────────────────
import unicodedata as _ud_usina

def _norm_usina(s):
    """Normaliza string de usina para lookup: sem acento, minúsculo, sem espaços duplos."""
    s = _ud_usina.normalize("NFKD", (s or "").lower())
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s).strip()
    return s

# Constrói índice na inicialização
_ALIAS_INDEX = {}   # alias_norm → nome_oficial
_CLIENTE_INDEX = {} # nome_oficial → cliente

for _nome_oficial, _info in CATALOGO_USINAS.items():
    _CLIENTE_INDEX[_nome_oficial] = _info["cliente"]
    # Adiciona o próprio nome oficial como alias
    _ALIAS_INDEX[_norm_usina(_nome_oficial)] = _nome_oficial
    for _alias in _info["aliases"]:
        _ALIAS_INDEX[_norm_usina(_alias)] = _nome_oficial

# Prefixos a remover antes de lookup
_PREFIXOS_USINA = re.compile(
    r"^(?:ufv\s+)?(?:usina\s+)?(?:ufv\s+)?",
    re.IGNORECASE
)
# Sufixos a remover (lixo que pode vir junto)
_SUFIXOS_USINA = re.compile(
    r"\s*[-–|]\s*(?:normaliz\w+|ok|trip\s*\w*|desvio\w*).*$",
    re.IGNORECASE
)

def canonizar_usina(texto_bruto):
    """
    Recebe qualquer variação de nome de usina e retorna o nome oficial canônico.
    Retorna None se a usina não estiver no catálogo (outro supervisor).

    Exemplos:
      "UFV Xavantina 1"         → "Nova Xavantina I"
      "Boa Esperança do Sul IB" → "Boa Esperança do Sul II"
      "Usina Crateus"           → "Crateús"
      "UFV Topázio"             → "Matão II - Topázio"
      "Fazenda XYZ"             → None  (fora do catálogo)
    """
    if not texto_bruto:
        return None

    # Remove emojis e caracteres especiais comuns
    s = re.sub(r"[🔴🟡🟢🟠✅⏸️🔧⚠️*]", "", texto_bruto).strip()
    # Remove sufixos como "| NORMALIZADA | Trip 59B"
    s = _SUFIXOS_USINA.sub("", s).strip()
    # Remove prefixos "UFV ", "Usina ", etc.
    s = _PREFIXOS_USINA.sub("", s).strip()
    # Remove pontuação final
    s = s.rstrip(".,:-|").strip()

    # Normaliza para lookup
    s_norm = _norm_usina(s)

    # 1. Lookup direto no índice
    if s_norm in _ALIAS_INDEX:
        return _ALIAS_INDEX[s_norm]

    # 2. Busca parcial — útil para variações não previstas
    # Tenta encontrar qual usina tem maior sobreposição com o texto
    melhor = None
    melhor_score = 0
    for alias_norm, nome_oficial in _ALIAS_INDEX.items():
        # Match se o alias está contido no texto ou vice-versa
        if alias_norm in s_norm or s_norm in alias_norm:
            score = len(alias_norm)  # prefere matches mais longos
            if score > melhor_score:
                melhor_score = score
                melhor = nome_oficial

    if melhor and melhor_score >= 4:  # evita matches em strings muito curtas
        return melhor

    return None  # usina não reconhecida — ignorar


def inferir_cliente(usina_canonical):
    """Retorna o cliente dado o nome canônico da usina."""
    return _CLIENTE_INDEX.get(usina_canonical, "")


def usina_permitida(texto):
    """Retorna True se a usina for reconhecida no catálogo."""
    return canonizar_usina(texto) is not None


# Mantém compatibilidade com código legado que usava CLIENTE_POR_USINA
CLIENTE_POR_USINA = {
    _norm_usina(nome): info["cliente"]
    for nome, info in CATALOGO_USINAS.items()
}
USINAS_PERMITIDAS = set(CATALOGO_USINAS.keys())

STATUS_VALIDOS = {
    "em aberto": "Em Aberto", "aberto": "Em Aberto",
    "concluído": "Concluído", "concluido": "Concluído", "resolvido": "Concluído",
    "aguardando cliente": "Aguardando Cliente",
    "aguardando fabricante": "Aguardando Fabricante",
    "aguardando equipamento": "Aguardando Equipamento",
    "em andamento": "Em Andamento",
    "corrigir ronda": "Corrigir Ronda - COS",
    "corrigir ronda - cos": "Corrigir Ronda - COS",
    "fechado": "Fechado",
}

# ── Padrões de extração ───────────────────────────────────────────────────────
_P = r"^[\s*·\-–]*"

PADROES = {
    "usina": re.compile(
        r"^(?:(?:🔴|🟡|🟢|🟠|✅|⏸️|🔧)[\s]*)?(?:DESVIO:[\s]*|UFV[\s]+DESVIO:[\s]*)?(?:UFV[\s]+)?Usina:?[\s]*([^\n\r*·:]{2,60}?)\s*(?:\*[^\n\r]*)?$",
        re.IGNORECASE | re.MULTILINE
    ),
    "problema": re.compile(_P + r"Probl[eo]ma[s]?(?:\s+do\s+\w+)?:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "descricao": re.compile(_P + r"Descri(?:ção|cao|çao|ção|c[aã]o)?(?:\s+d[oa]s?\s+\w+)?:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "acao": re.compile(_P + r"A[çc][aã]o(?:es)?:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "equipe": re.compile(_P + r"(?:Equipe[:\s]+(?:Acionada:?)?|T[eé]cnico\s+Acionado:)[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "supervisor": re.compile(_P + r"Supervisor[:\s]+(?:Acionado:?)?[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "inicio": re.compile(_P + r"In[ií]ci[oo](?:[\s]+(?:d[ao][\s]+)?[Oo]corrên?cia)?:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "fim": re.compile(_P + r"(?:Fim|T[eé]rmino)(?:[\s]+(?:d[ao][\s]+)?[Oo]corrên?cia)?:[ \t]*([^\n\r]*)", re.IGNORECASE | re.MULTILINE),
    "os": re.compile(_P + r"N[ºo°]?\.?[\s]*(?:da[\s]+)?OS:?[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "impacto": re.compile(_P + r"Impacto[s]?:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "equipamento": re.compile(_P + r"Equipamento[s]?[^:\n]*:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "causa": re.compile(_P + r"Causa[^:\n]*:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "chamado_conc": re.compile(_P + r"Chamado\s+Concession[aá]ria:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "tipo_manut": re.compile(_P + r"Tipo\s+Manuten[çc][aã]o[^:]*:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "identificacao": re.compile(_P + r"[Ii]dentifica[çc][aã]o:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "equip_problema": re.compile(_P + r"Equipamentos\s+com\s+Problema:[ \t]*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    "cos_problema":   re.compile(r"·\s*Probl[eo]ma[s]?:[ \t]*([^\n\r]+)", re.IGNORECASE),
    "cos_descricao":  re.compile(r"·\s*Descri[çc][aã]o[^:]*:[ \t]*([^\n\r]+)", re.IGNORECASE),
    "cos_impacto":    re.compile(r"·\s*Impacto[s]?:[ \t]*([^\n\r]+)", re.IGNORECASE),
    "cos_acao":       re.compile(r"·\s*A[çc][aã]o(?:es)?:[ \t]*([^\n\r]+)", re.IGNORECASE),
    "cos_equipe":     re.compile(r"[·*]\s*(?:Equipe\s+Acionada|T[eé]cnico\s+Acionado):[ \t]*([^\n\r]+)", re.IGNORECASE),
    "cos_supervisor": re.compile(r"[·*]\s*Supervisor(?:\s+Acionado)?:[ \t]*([^\n\r]+)", re.IGNORECASE),
    "cos_inicio":     re.compile(r"[·*]\s*In[ií]ci[oo](?:\s+da\s+[Oo]corrência)?:[ \t]*([^\n\r]+)", re.IGNORECASE),
    "cos_fim":        re.compile(r"[·*]\s*(?:Fim|T[eé]rmino)(?:\s+da\s+[Oo]corrência)?:[ \t]*([^\n\r]*)", re.IGNORECASE),
    "cos_os":         re.compile(r"[·*]\s*N[ºo°]\.?[\s]*(?:da[\s]+)?OS:?[ \t]*([^\n\r]+)", re.IGNORECASE),
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def eh_formato_cos_grid(texto):
    tem_bullet = bool(re.search(r"[·*]\s*(?:Problema|Descrição|Impacto|Ação|Equipe|Supervisor|Início|Fim|Nº)", texto, re.IGNORECASE))
    tem_usina  = bool(re.search(r"Usina:", texto, re.IGNORECASE))
    return tem_bullet and tem_usina

def extrair(texto, padrao):
    m = padrao.search(texto)
    return m.group(1).strip().lstrip("*·").strip() if m else ""

def vazio(v):
    return not v or str(v).strip() in ("", "--", "-", "N/A", "n/a", "não", "nao", "Não")


def gravar_data_se_vazia(ws, num_linha, coluna_idx, row, label=""):
    """
    Grava a data/hora atual (agora_br()) na coluna informada, SOMENTE se a
    célula ainda estiver vazia. Nunca sobrescreve um valor já existente —
    seja ele gravado pelo robô antes, seja uma correção manual feita por Fred
    direto na planilha.

    coluna_idx: índice 0-based da coluna dentro de `row` (M=12, N=13, O=14).
    A escrita usa update_cell com o índice 1-based correspondente.
    """
    valor_atual = (row[coluna_idx] if len(row) > coluna_idx else "").strip()
    if not vazio(valor_atual):
        return False  # já preenchido (robô ou manual) — não mexe

    agora = agora_br().strftime("%d/%m/%Y %H:%M:%S")
    ws.update_cell(num_linha, coluna_idx + 1, agora)
    log.info(f"   → {label} gravada automaticamente: linha {num_linha} = {agora}")
    return True


def anexar_mensagem_original(ws, num_linha, coluna_idx, row, texto_bruto):
    """
    Anexa o texto bruto da mensagem do WhatsApp (já segmentado por ocorrência,
    vindo de parse_bloco) na coluna de 'Mensagens Originais' (V), no mesmo
    padrão do Histórico Cronológico: timestamp + texto, separado por linha
    em branco do conteúdo anterior. Sempre acrescenta, nunca substitui.

    Evita duplicar a mesma mensagem se o webhook reprocessar o mesmo texto
    (reenvio, retry de rede) — mesma proteção usada no Histórico Cronológico.
    """
    if vazio(texto_bruto):
        return
    texto_limpo = texto_bruto.strip()
    atual = (row[coluna_idx] if len(row) > coluna_idx else "").strip()
    if texto_limpo in atual:
        return  # mensagem idêntica já registrada — não duplica
    agora = agora_br().strftime("%d/%m %H:%M")
    entrada = f"{agora} - {texto_limpo}"
    novo = (atual + "\n\n" + entrada).strip() if atual else entrada
    ws.update_cell(num_linha, coluna_idx + 1, novo)


def normalizar_texto(t):
    import unicodedata
    return unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode("ascii").strip()

# inferir_cliente e usina_permitida definidas acima via canonizar_usina()

def extrair_tecnico(s):
    m = re.search(r"@([\w\s]+?)(?:\s*[-–]\s*[\w-]+)?\s*$", s)
    if m:
        return m.group(1).strip()
    s = re.sub(r"^[Ss]im[,\s]*", "", s).strip()
    return re.sub(r"@", "", s).strip()

def limpar_nome(s):
    s = re.sub(r"^[Ss]im[,\s]+", "", s).strip()
    s = re.sub(r"[@~]", "", s).strip()
    s = re.sub(r"\s*\|.*$", "", s).strip()
    s = re.sub(r"^[Tt][eé]cnico\s+", "", s).strip()
    return s

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
    # Normaliza Inversor N e INV-N → INV-NN
    equip = re.sub(r"(?i)\bInversor(?:es)?\s+(\d+)\b", lambda m: f"INV-{int(m.group(1)):02d}", equip)
    equip = re.sub(r"\bINV-?(\d+)\b", lambda m: f"INV-{int(m.group(1)):02d}", equip)
    if equip and not equip.startswith("INV-"):
        equip = equip[0].upper() + equip[1:]
    return equip.strip()

def inferir_equipamento(problema="", descricao="", identificacao="", equip_problema="", acao="", impacto=""):
    for texto in [identificacao, problema, descricao, impacto, acao, equip_problema]:
        if not texto or str(texto).strip() in ("", "--", "-", "N/A"):
            continue
        m = _REGEX_EQUIP.search(texto)
        if m:
            return _limpar_equipamento(m.group(0))
    fonte = problema or descricao or ""
    return fonte[:60] if fonte else ""

def normalizar_inversores(texto):
    """
    Padroniza nomenclatura de inversores para INV-XX.
    Exemplos:
      "inversor 4"    → "INV-04"
      "inversor 04"   → "INV-04"
      "Inversor 14"   → "INV-14"
      "INV-4"         → "INV-04"
    """
    if not texto:
        return texto
    def fmt(n):
        return f"INV-{int(n):02d}"
    # inversor N → INV-NN
    texto = re.sub(
        r'\bInversor(?:es)?\s+(\d+)\b',
        lambda m: fmt(m.group(1)),
        texto, flags=re.IGNORECASE
    )
    # INV-N → INV-NN (sem zero à esquerda)
    texto = re.sub(
        r'\bINV-(\d+)\b',
        lambda m: fmt(m.group(1)),
        texto
    )
    return texto


def extrair_inversores_multiplos(bloco, dados_base):
    """
    Detecta mensagens com múltiplos inversores (ex: "Inversores 6 e 7")
    e retorna lista de dados individuais, um por inversor.

    Se houver ações/causas individuais por inversor no texto, distribui.
    Caso contrário, replica as mesmas informações para cada um.

    Retorna [] se não houver múltiplos inversores (processamento normal).
    """
    falha = dados_base.get("falha", "")
    acao  = dados_base.get("acao_texto", "") or dados_base.get("acao", "")

    # Detecta padrão: "inversores N e M" ou "inversores N, M e K"
    # Exemplos: "Inversores 6 e 7", "Inversores 06, 07 e 08"
    m = re.search(
        r'\bInversores?\s+((?:\d+(?:\s*[,e]\s*)?)+)',
        falha + " " + acao,
        re.IGNORECASE
    )
    if not m:
        return []

    nums_raw = re.findall(r'\d+', m.group(1))
    if len(nums_raw) < 2:
        return []  # só um inversor — processamento normal

    nums = [f"{int(n):02d}" for n in nums_raw]
    log.info(f"[Multi-INV] Detectados {len(nums)} inversores: {nums}")

    # Tenta extrair ações individuais por inversor no texto completo
    # Padrão: "INV-06: texto... INV-07: texto..."
    acoes_individuais = {}
    causas_individuais = {}

    for num in nums:
        inv_tag = f"INV-{num}"
        # Busca padrão "INV-XX: ..." ou "Inversor XX: ..."
        m_acao = re.search(
            rf'(?:INV-{num}|[Ii]nversor\s+0*{int(num)})\s*[:\-–]\s*([^\n\.]+)',
            acao
        )
        if m_acao:
            acoes_individuais[num] = m_acao.group(1).strip()

        m_causa = re.search(
            rf'(?:INV-{num}|[Ii]nversor\s+0*{int(num)})\s*[:\-–]\s*([^\n\.]+)',
            dados_base.get("causa", "")
        )
        if m_causa:
            causas_individuais[num] = m_causa.group(1).strip()

    # Gera lista de dados individuais
    lista = []
    for num in nums:
        inv_nome = f"INV-{num}"
        # Falha: substitui referência genérica pelo inversor específico
        # Ex: "Falha nos inversores 6 e 7" → "Falha no INV-06"
        falha_ind = re.sub(
            r'(?:nos\s+|no\s+)?\bInversores?\s+[\d,\s]+(?:e\s+\d+)?',
            f"no {inv_nome}",
            falha, flags=re.IGNORECASE
        ).strip() or falha

        dados_ind = {
            **dados_base,
            "equipamento":  inv_nome,
            "equip_impact": inv_nome,
            "falha":        falha_ind,
            "acao_texto":   acoes_individuais.get(num, dados_base.get("acao_texto", "")),
            "causa":        causas_individuais.get(num, dados_base.get("causa", "")),
        }
        # Recalcula ação composta
        partes = []
        if dados_ind["acao_texto"]:
            partes.append(dados_ind["acao_texto"])
        dados_ind["acao"] = " | ".join(partes) if partes else dados_base.get("acao", "")
        lista.append(dados_ind)

    return lista



def eh_normalizacao(texto):
    """
    Detecta se um bloco/texto indica normalização de ocorrência.
    Cobre:
      - ✅ + NORMALIZADO (qualquer posição)
      - Palavra NORMALIZADO/NORMALIZADA no campo usina (ex: 'Colider 1 - NORMALIZADO')
      - Fim da Ocorrência preenchido
      - Termos como 'ocorrência normalizada', 'usina normalizada'
    """
    return bool(re.search(
        r'normalizado|normalizada|✅.*normal|normal.*✅|ocorr[êe]ncia\s+encerrada',
        texto, re.IGNORECASE
    ))


def detectar_status_emoji(bloco):
    if re.search(r"✅", bloco):
        if eh_normalizacao(bloco):
            return "normalizado"
        return "Em Aberto"
    if re.search(r"🔴|🟡|🟠|⏸️", bloco): return "Em Aberto"
    return "Em Aberto"

def extrair_data_fmt(texto_data, fallback):
    if vazio(texto_data):
        return fallback
    m = re.search(r"(\d{2}/\d{2})", texto_data)
    return m.group(1) if m else fallback

def similaridade_falha(falha1, falha2):
    n1 = normalizar_texto(falha1)
    n2 = normalizar_texto(falha2)
    palavras1 = set(p for p in n1.split() if len(p) > 3)
    palavras2 = set(p for p in n2.split() if len(p) > 3)
    if not palavras1 or not palavras2:
        return False
    intersecao = palavras1 & palavras2
    menor = min(len(palavras1), len(palavras2))
    return len(intersecao) / menor >= 0.5


# ── Parse formato Cos Grid ────────────────────────────────────────────────────

def parse_bloco_cos_grid(bloco):
    usina_raw = extrair(bloco, PADROES["usina"])
    if not usina_raw:
        return None

    # Canoniza usando o catálogo oficial — resolve qualquer variação de nome
    usina_canonical = canonizar_usina(usina_raw)
    if not usina_canonical:
        log.info(f"Usina não reconhecida (Cos Grid): {usina_raw!r}")
        return None
    usina = usina_canonical

    normalizar_usina = bool(re.search(r"NORMALIZ", usina_raw, re.IGNORECASE))

    problema    = extrair(bloco, PADROES["cos_problema"])
    descricao   = extrair(bloco, PADROES["cos_descricao"])
    impacto     = extrair(bloco, PADROES["cos_impacto"])
    acao_txt    = extrair(bloco, PADROES["cos_acao"])
    equipe_raw  = extrair(bloco, PADROES["cos_equipe"])
    superv_raw  = extrair(bloco, PADROES["cos_supervisor"])
    inicio_txt  = extrair(bloco, PADROES["cos_inicio"])
    fim_txt     = extrair(bloco, PADROES["cos_fim"])
    os_txt      = extrair(bloco, PADROES["cos_os"])

    if not problema:  problema  = extrair(bloco, PADROES["problema"])
    if not descricao: descricao = extrair(bloco, PADROES["descricao"])
    if not acao_txt:  acao_txt  = extrair(bloco, PADROES["acao"])
    if not os_txt:    os_txt    = extrair(bloco, PADROES["os"])

    falha = problema or descricao or impacto or ""

    # Não cria ocorrência se não há falha identificada E não é normalização
    if vazio(falha) and not normalizar_usina:
        log.info(f"[COS Grid] Sem falha/problema identificado para {usina} — ignorando")
        return None

    equip = inferir_equipamento(problema=problema, descricao=descricao, acao=acao_txt, impacto=impacto)
    if not equip:
        equip = "Usina / Sistema Geral"

    tec = limpar_nome(equipe_raw) if not vazio(equipe_raw) else ""
    sup = limpar_nome(superv_raw) if not vazio(superv_raw) else ""

    partes_acao = []
    if not vazio(acao_txt): partes_acao.append(acao_txt)
    if not vazio(tec):      partes_acao.append(f"Técnico: {tec}")
    if not vazio(sup):      partes_acao.append(f"Supervisor: {sup}")
    if not partes_acao:     partes_acao.append("Inspeção em campo")

    os_num = ""
    if not vazio(os_txt):
        m_os = re.search(r"[\d]+", os_txt)
        os_num = m_os.group() if m_os else ""

    fim_preenchido = not vazio(fim_txt) and fim_txt.strip() not in ("", "-", "--")
    normalizar = normalizar_usina or fim_preenchido or eh_normalizacao(bloco)

    hoje     = agora_br().strftime("%d/%m")
    data_ini = extrair_data_fmt(inicio_txt, hoje)
    hist     = [f"{data_ini} - Registro inicial"]
    if not vazio(acao_txt):
        hist.append(f"{hoje} - {acao_txt}")
    if not vazio(tec):
        hist.append(f"{hoje} - Técnico em campo: {tec}")
    if normalizar:
        data_fim = extrair_data_fmt(fim_txt, hoje)
        hist.append(f"{data_fim} - Ocorrência normalizada")

    # "Descrição dos Problemas" → causa (motivo técnico da falha)
    # "Impacto"               → equipamentos impactados
    causa_final = descricao or ""
    equip_impact = impacto or equip

    # Status: se equipe foi acionada = "Em Andamento", senão "Em Aberto"
    equipe_acionada = not vazio(equipe_raw)
    if normalizar:
        status_calc = "Concluído"
    elif equipe_acionada:
        status_calc = "Em Andamento"
    else:
        status_calc = "Em Aberto"

    return {
        "usina":       usina,
        "cliente":     inferir_cliente(usina),
        "equipamento": equip,
        "falha":       falha,
        "causa":       causa_final,
        "equip_impact":equip_impact,
        "acao":        " | ".join(partes_acao),
        "status":      status_calc,
        "historico":   "\n".join(hist),
        "os":          os_num,
        "normalizar":  normalizar,
        "acao_texto":  acao_txt,
        "mensagem_bruta": bloco.strip(),
    }


# ── Parse de blocos (formato original) ───────────────────────────────────────

def normalizar_num(num_str):
    try:
        return str(int(num_str))
    except:
        return num_str

def extrair_atualizacoes_por_ativo(texto_acao):
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
                        "acao_resumida": {
                            "normalizado":          "Ocorrência normalizada em campo",
                            "garantia":             "Aguardando garantia com fabricante",
                            "tratativa fabricante": "Em tratativa com fabricante",
                        }.get(status, status.capitalize()),
                    }
    return list(melhor.values())

def equipamento_match(equip_planilha, equip_busca):
    def norm(s):
        s = s.lower().strip()
        s = re.sub(r"motor\s*", "tracker ", s)
        s = re.sub(r"tcu\s*tracker\s*", "tracker ", s)
        s = re.sub(r"inv-", "inversor ", s)
        nums = re.findall(r"\d+", s)
        tipo = re.search(r"(tracker|inversor|motor|tcu|inv)", s)
        tipo_str = tipo.group(1) if tipo else ""
        if tipo_str == "inv": tipo_str = "inversor"
        return tipo_str, [normalizar_num(n) for n in nums]
    tipo1, nums1 = norm(equip_planilha)
    tipo2, nums2 = norm(equip_busca)
    if not nums1 or not nums2: return False
    tipos_ok = tipo1 == tipo2 or not tipo1 or not tipo2
    nums_ok  = bool(set(nums1) & set(nums2))
    return tipos_ok and nums_ok

def separar_blocos(texto):
    if eh_formato_cos_grid(texto):
        partes = re.split(r"(?=(?:^|\n)Usina:)", texto, flags=re.MULTILINE | re.IGNORECASE)
        blocos = [p.strip() for p in partes if p.strip() and len(p.strip()) > 20]
        return blocos if blocos else [texto]

    partes = re.split(r"(?=(?:^|\n)[ \t]*(?:🔴|🟡|🟢|🟠|✅|⏸️))", texto, flags=re.MULTILINE)
    blocos = [p.strip() for p in partes if p.strip() and len(p.strip()) > 30]

    if len(blocos) <= 1:
        partes = re.split(r"(?=(?:^|\n)[ \t]*(?:🔴|🟡|🟢|🟠|✅|⏸️|🔧)?[ \t]*(?:DESVIO:?\s*)?(?:Usina|UFV):)", texto, flags=re.MULTILINE | re.IGNORECASE)
        blocos = [p.strip() for p in partes if p.strip() and len(p.strip()) > 30]

    return blocos if blocos else [texto]

def parse_bloco(bloco):
    if eh_formato_cos_grid(bloco):
        return parse_bloco_cos_grid(bloco)

    c = {k: extrair(bloco, p) for k, p in PADROES.items()}

    if not c["usina"] or len(c["usina"]) > 60:
        primeira = bloco.split('\n')[0].strip()
        m_desvio = re.search(r'(?:🔴|🟡|🟢|🟠|✅|⏸️)?\s*(?:DESVIO:\s*|Usina:\s*)?(?:UFV\s+)?(.+?)[\s:*]*$', primeira, re.IGNORECASE)
        if m_desvio:
            candidato = m_desvio.group(1).strip().rstrip(':*').strip()
            if candidato and len(candidato) < 60:
                c["usina"] = candidato

    if not c["usina"]:
        return None

    # Canoniza usando o catálogo oficial — resolve qualquer variação de nome
    usina_canonical = canonizar_usina(c["usina"])
    if not usina_canonical:
        log.info(f"Usina não reconhecida (formato original): {c['usina']!r}")
        return None
    usina = usina_canonical

    eh_formato_tracker = not vazio(c["identificacao"]) or not vazio(c["equip_problema"])

    if eh_formato_tracker:
        id_raw  = c["identificacao"] if not vazio(c["identificacao"]) else ""
        id_fmt  = re.sub(r"Tck\s*", "Tracker ", id_raw, flags=re.IGNORECASE).strip()
        equip   = id_fmt if id_fmt else inferir_equipamento(problema=c["problema"], descricao=c["descricao"], acao=c["acao"], impacto=c.get("impacto",""))
        equip_prob = c["equip_problema"] if not vazio(c["equip_problema"]) else ""
        m_acao  = re.search(r"(.+?)\.\s*(acionado.+)$", equip_prob, re.IGNORECASE | re.DOTALL)
        if m_acao:
            causa        = m_acao.group(1).strip()
            acao_tracker = m_acao.group(2).strip().capitalize()
        else:
            causa        = equip_prob
            acao_tracker = ""
        partes_acao = []
        if acao_tracker:
            partes_acao.append(acao_tracker)
        elif not vazio(c["acao"]):
            partes_acao.append(c["acao"])
        else:
            partes_acao.append("Inspeção em campo")
    else:
        equip = c["equipamento"] if not vazio(c["equipamento"]) else \
                inferir_equipamento(problema=c["problema"], descricao=c["descricao"], identificacao=c["identificacao"], equip_problema=c["equip_problema"], acao=c["acao"], impacto=c.get("impacto",""))
        causa        = c["causa"] if not vazio(c["causa"]) else ""
        acao_tracker = ""
        partes_acao  = []
        if not vazio(c["acao"]):
            partes_acao.append(c["acao"])
        else:
            partes_acao.append("Inspeção em campo")

    tec = extrair_tecnico(c["equipe"]) if not vazio(c["equipe"]) else ""
    if not vazio(tec): partes_acao.append(f"Técnico: {tec}")
    sup = re.sub(r"^[Ss]im[,\s]*", "", c["supervisor"]).strip() if not vazio(c["supervisor"]) else ""
    sup = re.sub(r"@", "", sup).strip()
    if not vazio(sup): partes_acao.append(f"Supervisor: {sup}")

    os_num = ""
    if not vazio(c["os"]):
        m_os = re.search(r"[\d/]+", c["os"])
        os_num = m_os.group() if m_os else ""

    status_emoji = detectar_status_emoji(bloco)
    normalizar   = (status_emoji == "normalizado")

    hoje       = agora_br().strftime("%d/%m")
    hist       = []
    data_inicio = extrair_data_fmt(c["inicio"], hoje)
    if normalizar:
        data_fim = extrair_data_fmt(c["fim"], hoje)
        hist.append(f"{data_inicio} - Registro inicial")
        hist.append(f"{data_fim} - Ocorrência normalizada")
    else:
        hist.append(f"{data_inicio} - Registro inicial")
        acao_hist = acao_tracker if eh_formato_tracker and not vazio(acao_tracker) else c["acao"]
        if not vazio(acao_hist):
            hist.append(f"{hoje} - {acao_hist}")

    return {
        "usina":       usina,
        "cliente":     inferir_cliente(usina),
        "equipamento": equip,
        "falha":       (c["problema"] or c["descricao"] or c["tipo_manut"] or (f"Tracker parado - {causa}" if eh_formato_tracker else "") or ""),
        "causa":       causa,
        "equip_impact":equip,
        "acao":        " | ".join(partes_acao),
        "status":      "Concluído" if normalizar else "Em Aberto",
        "historico":   "\n".join(hist),
        "os":          os_num,
        "normalizar":  normalizar,
        "acao_texto":  c["acao"],
        "mensagem_bruta": bloco.strip(),
    }


# ── Google Sheets ─────────────────────────────────────────────────────────────

def get_sheet():
    gc = get_gc()
    return gc.open_by_key(SHEET_ID).worksheet(SHEET_NAME)

ZELADORIA_GID = 987654321

def get_zeladoria_sheet():
    gc = get_gc()
    return gc.open_by_key(SHEET_ID).get_worksheet_by_id(ZELADORIA_GID)


# ── Controle de Fotos de Zeladoria (vegetação/sujidade) ──────────────────
# Duas abas: uma fila crua (uma linha por foto recebida, sem processar) e
# um resumo processado (uma linha por leva já classificada pela IA). A
# separação evita que o webhook do WhatsApp precise esperar a IA responder
# — ele só salva o arquivo e registra a linha crua; a classificação roda
# depois, sob demanda, via /zeladoria-processar-fotos-pendentes.
ZELADORIA_FOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zeladoria_fotos")
os.makedirs(ZELADORIA_FOTOS_DIR, exist_ok=True)

ZELADORIA_FOTOS_RAW_SHEET_NAME = "_ZeladoriaFotosRaw"
ZELADORIA_FOTOS_RAW_HEADERS = ["id", "grupoId", "semanaISO", "recebidoEm", "legenda", "arquivo", "processado"]

ZELADORIA_FOTOS_SHEET_NAME = "_ZeladoriaFotos"
ZELADORIA_FOTOS_HEADERS = [
    "id", "semanaISO", "grupoId", "clusterProvavel", "usinaCandidataIA", "confiancaIA",
    "justificativaIA", "usinaConfirmada", "qtdSujidade", "qtdVegetacao", "qtdIndefinido",
    "arquivos", "confirmadoFred", "criadoEm", "atualizadoEm", "descartado",
    "grauSujidadeIA", "grauVegetacaoIA",
]


def get_zeladoria_fotos_raw_sheet():
    gc = get_gc()
    ss = gc.open_by_key(SHEET_ID)
    try:
        ws = ss.worksheet(ZELADORIA_FOTOS_RAW_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=ZELADORIA_FOTOS_RAW_SHEET_NAME, rows=2000, cols=len(ZELADORIA_FOTOS_RAW_HEADERS))
        ws.append_row(ZELADORIA_FOTOS_RAW_HEADERS)
    return ws


_zeladoria_fotos_sheet_migrada = False


def get_zeladoria_fotos_sheet():
    global _zeladoria_fotos_sheet_migrada
    gc = get_gc()
    ss = gc.open_by_key(SHEET_ID)
    try:
        ws = ss.worksheet(ZELADORIA_FOTOS_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=ZELADORIA_FOTOS_SHEET_NAME, rows=500, cols=len(ZELADORIA_FOTOS_HEADERS))
        ws.append_row(ZELADORIA_FOTOS_HEADERS)
        _zeladoria_fotos_sheet_migrada = True
        return ws
    # migração incremental: garante que colunas novas (ex: descartado)
    # existam. Só checa uma vez por ciclo de vida do processo (variável
    # global) — não a cada chamada, que é o que estava sobrecarregando a
    # cota de leitura do Sheets em fluxos que abrem essa planilha várias
    # vezes seguidas (processar fotos, confirmar, status, etc.).
    if not _zeladoria_fotos_sheet_migrada:
        header = _gspread_retry(lambda: ws.row_values(1))
        if len(header) < len(ZELADORIA_FOTOS_HEADERS):
            if ws.col_count < len(ZELADORIA_FOTOS_HEADERS):
                ws.add_cols(len(ZELADORIA_FOTOS_HEADERS) - ws.col_count)
            for i in range(len(header), len(ZELADORIA_FOTOS_HEADERS)):
                ws.update_cell(1, i + 1, ZELADORIA_FOTOS_HEADERS[i])
        _zeladoria_fotos_sheet_migrada = True
    return ws


def _semana_iso(dt):
    ano, semana, _ = dt.isocalendar()
    return f"{ano}-W{semana:02d}"


# ── Controle de Zeladoria (grau de sujidade / vegetação, 1-5) ────────────
# Mesma escala das planilhas STATUS_CONSOLIDADO usadas pela Grid Co.:
# Sujidade: L=limpeza em execução, 1=leve … 5=crítica
# Vegetação: R=roçagem em andamento, 1=muito baixa … 5=muito alta
ZELADORIA_GRAUS_SHEET_NAME = "_ZeladoriaGraus"
ZELADORIA_GRAUS_HEADERS = ["usina", "cliente", "tipo", "semanaISO", "grau", "observacoes", "editor", "atualizadoEm"]


def get_zeladoria_graus_sheet():
    gc = get_gc()
    ss = gc.open_by_key(SHEET_ID)
    try:
        ws = ss.worksheet(ZELADORIA_GRAUS_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=ZELADORIA_GRAUS_SHEET_NAME, rows=2000, cols=len(ZELADORIA_GRAUS_HEADERS))
        ws.append_row(ZELADORIA_GRAUS_HEADERS)
    return ws


_DIAS_SEMANA_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def _dia_semana_pt(dt):
    return _DIAS_SEMANA_PT[dt.weekday()]


# Regras de agenda conhecidas — contexto extra pra IA tentar identificar a
# usina certa quando um mesmo grupo de WhatsApp cobre mais de uma usina.
# Se a lista crescer, migrar pra aba _Sistema como as outras regras
# editáveis sem deploy (ex.: "agenda_usina:<Usina>" = "segunda-feira").
_AGENDA_EQUIPES_CONHECIDA = {
    "Guajirú": "equipe Cláudio Ferreira/Isake Costa costuma atender as usinas GD Energy (Guajirú, Sol do Norte I/II) às QUARTAS-FEIRAS",
    "Sol do Norte": "equipe Cláudio Ferreira/Isake Costa costuma atender as usinas GD Energy (Guajirú, Sol do Norte I/II) às QUARTAS-FEIRAS",
    "ABC Morada Nova": "equipe Cláudio Ferreira/Isake Costa costuma atender ABC Morada Nova (Alves Lima) às SEGUNDAS-FEIRAS",
}

ATIVIDADES_SHEET_NAME = "Painel de Atividades"
ATIVIDADES_HEADERS = ["ID", "Cliente", "Usina", "Equipamento", "Descricao", "Responsavel", "Prazo",
                       "Prioridade", "Status", "DataCriacao", "DataConclusao", "Historico", "Editor",
                       "NumeroOS"]

DESLIGAMENTO_MANUAL_SHEET_NAME = "_DesligamentoManual"
DESLIGAMENTO_MANUAL_HEADERS = ["origem", "id", "valor", "editor", "atualizadoEm"]

CHAMADOS_FABRICANTE_SHEET_NAME = "ChamadosFabricante"
CHAMADOS_FABRICANTE_HEADERS = [
    "Ativo", "UFV", "Cliente", "Equipe", "Supervisor", "OS de Abertura", "Ticket/RMA",
    "Fabricante", "Identificação Supervisório", "Identificação do Equipamento", "Serial Number",
    "Data da ocorrência", "Data da abertura do chamado", "Data da Última Atualização",
    "Motivo da abertura do chamado", "Causa da Falha", "Dias corridos", "Data de finalização",
    "Status", "Título do E-mail", "Observações", "N° da Solicitação de OS", "Supervisor Antigo",
    "Status OS", "Resolução",
]

def get_chamados_fabricante_sheet():
    gc = get_gc()
    ss = gc.open_by_key(SHEET_ID)
    try:
        ws = ss.worksheet(CHAMADOS_FABRICANTE_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=CHAMADOS_FABRICANTE_SHEET_NAME, rows=500,
                               cols=len(CHAMADOS_FABRICANTE_HEADERS))
        ws.append_row(CHAMADOS_FABRICANTE_HEADERS)
    return ws


def get_desligamento_manual_sheet():
    gc = get_gc()
    ss = gc.open_by_key(SHEET_ID)
    try:
        ws = ss.worksheet(DESLIGAMENTO_MANUAL_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=DESLIGAMENTO_MANUAL_SHEET_NAME, rows=200, cols=len(DESLIGAMENTO_MANUAL_HEADERS))
        ws.append_row(DESLIGAMENTO_MANUAL_HEADERS)
    return ws


def get_atividades_sheet():
    gc = get_gc()
    ss = gc.open_by_key(SHEET_ID)
    try:
        ws = ss.worksheet(ATIVIDADES_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=ATIVIDADES_SHEET_NAME, rows=1000, cols=len(ATIVIDADES_HEADERS))
        ws.append_row(ATIVIDADES_HEADERS)
        return ws
    # migração incremental: garante que colunas novas (ex: Equipamento, NumeroOS) existam
    header = ws.row_values(1)
    if len(header) < len(ATIVIDADES_HEADERS):
        if ws.col_count < len(ATIVIDADES_HEADERS):
            ws.add_cols(len(ATIVIDADES_HEADERS) - ws.col_count)  # expande a grade antes de escrever
        for i in range(len(header), len(ATIVIDADES_HEADERS)):
            ws.update_cell(1, i + 1, ATIVIDADES_HEADERS[i])
    return ws

def carregar_planilha(ws):
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

# ── Fingerprint de deduplicação ───────────────────────────────────────────────

import unicodedata as _ud

def _norm(s):
    """Normaliza string para comparação: sem acento, minúsculo, só alfanum."""
    s = _ud.normalize("NFKD", (s or "").lower())
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(s.split())

def fingerprint_ocorrencia(usina, equipamento, falha):
    """
    Chave de identidade única de uma ocorrência.
    Formato: usina | tipo_equip | num_equip | palavras_falha
    Exemplos:
      "boa esperanca do sul 1 | tracker | 6 | geracao inversores perda"
      "ibate ii | inversor | 4 | funcionamento parcial strings"
    """
    usina_n = _norm(usina)
    equip_n = _norm(equipamento)

    # Números do equipamento ("Tracker 06" → "6")
    nums    = [str(int(n)) for n in re.findall(r"\d+", equip_n)]
    num_str = "_".join(nums) if nums else ""

    # Tipo do equipamento
    tipo_m  = re.search(
        r"(tracker|inversor|motor|tcu|nobreak|camera|exaustor|piranometro|"
        r"fieldlogger|smartlogger|ep\d+|igate|rele|switch|transformador|"
        r"bateria|stringbox|anemometro|otimizador|seccionadora|combiner|ncu|gcu|etm|nvr)",
        equip_n
    )
    tipo_str = tipo_m.group(1) if tipo_m else equip_n[:12]

    # Top-5 palavras significativas da falha
    stop = {"para", "com", "que", "dos", "das", "nos", "nas", "pelo", "pela",
            "esse", "esta", "este", "uma", "uns", "umas", "nao", "sem"}
    palavras = sorted(set(
        p for p in _norm(falha).split()
        if len(p) > 3 and p not in stop
    ))[:5]

    return f"{usina_n}|{tipo_str}|{num_str}|{'_'.join(palavras)}"


def _norm_equip_key(equip):
    """
    Gera chave de comparação de equipamento:
    extrai tipo + números normalizados.
    Ex: "INV-03" → ("inversor", ["3"])
        "Tracker 08" → ("tracker", ["8"])
        "Motor Tracker 5" → ("tracker", ["5"])
    """
    s = _norm(equip)
    s = re.sub(r"motor\s+tracker", "tracker", s)
    s = re.sub(r"tcu\s+tracker", "tracker", s)
    s = re.sub(r"inv-?", "inversor ", s)
    tipo_m = re.search(
        r"(inversor|tracker|motor|tcu|nobreak|camera|exaustor|piranometro|"
        r"fieldlogger|smartlogger|igate|rele|switch|transformador|"
        r"bateria|stringbox|otimizador|seccionadora|combiner|ep\d+)",
        s
    )
    tipo = tipo_m.group(1) if tipo_m else s[:10]
    nums = [str(int(n)) for n in re.findall(r"\d+", s)]
    return tipo, nums


def equipamentos_sao_iguais(equip1, equip2):
    """
    Compara dois equipamentos de forma tolerante.
    Considera iguais se tipo E pelo menos um número coincidem.
    """
    if not equip1 or not equip2: return False
    tipo1, nums1 = _norm_equip_key(equip1)
    tipo2, nums2 = _norm_equip_key(equip2)
    if not nums1 or not nums2: return False
    tipos_ok = tipo1 == tipo2 or not tipo1 or not tipo2
    nums_ok  = bool(set(nums1) & set(nums2))
    return tipos_ok and nums_ok


def usinas_sao_iguais(usina1, usina2):
    """Compara usinas usando o catálogo canônico."""
    c1 = canonizar_usina(usina1) or _norm(usina1)
    c2 = canonizar_usina(usina2) or _norm(usina2)
    return c1 == c2


def buscar_por_fingerprint(todos, usina, equipamento, falha, os_num=""):
    """
    Busca ocorrência existente EM ABERTO usando hierarquia de critérios:

    NÍVEL 1 (mais forte) — OS + usina + equipamento:
      Se a mensagem tem número de OS, busca por OS+usina+equip.
      Isso garante que atualizações de um chamado específico sempre
      encontrem a ocorrência certa, independente da descrição da falha.

    NÍVEL 2 — usina + equipamento (tipo + número):
      Compara usina (via catálogo canônico) + tipo e número do equipamento.
      Ex: INV-03 e "Inversor 3" são o mesmo; Tracker 8 e Motor 08 também.

    NÍVEL 3 (fallback) — fingerprint de palavras:
      Só usa se os níveis anteriores não encontrarem nada.

    Retorna (num_linha, row) ou None.
    """
    candidatos = []

    candidatos_concluidos = []  # para reabrir recentemente concluídas

    for i, row in enumerate(todos[1:], start=2):
        if len(row) < 9: continue
        status = row[8].strip().lower()
        eh_concluido = "conclu" in status or "resolv" in status or "fechad" in status

        usina_plan = row[2].strip()
        equip_plan = row[3].strip()
        os_plan    = (row[10] if len(row) > 10 else "").strip()

        # Usinas devem ser a mesma (obrigatório em todos os níveis)
        if not usinas_sao_iguais(usina, usina_plan):
            continue

        # NÍVEL 1a: OS + usina (mais forte — mesma OS = mesma ocorrência)
        # Normaliza: remove prefixos "OS", "#", zeros à esquerda, espaços
        def _norm_os(s):
            s = s.strip()
            m = re.match(r"(?i)^(?:os|n[oº°]?|#)\s*(\d+)", s)
            if m: return str(int(m.group(1)))
            try: return str(int(s))
            except: return s.lower().strip()
        os_n  = _norm_os(os_num)
        os_p  = _norm_os(os_plan)
        invalidos = {"", "n/a", "na", "-", "s/n", "sn", "0"}
        if os_n and os_p and os_n not in invalidos and os_p not in invalidos and os_n == os_p:
            log.info(f"🎯 Match NÍVEL 1 (OS+usina): linha {i} | OS={os_num} | {equip_plan}")
            return (i, row)

        if eh_concluido:
            # Verifica se foi concluída recentemente (≤ 7 dias) pelo histórico
            hist_txt = row[11] if len(row) > 11 else ""
            hoje = agora_br()
            datas = re.findall(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", hist_txt)
            reabrir = False
            for d_match in datas:
                try:
                    dia, mes = int(d_match[0]), int(d_match[1])
                    ano = int(d_match[2]) if d_match[2] else hoje.year
                    dt = datetime(ano, mes, dia)
                    if (hoje - dt).days <= 7:
                        reabrir = True
                        break
                except:
                    pass
            if reabrir and equipamentos_sao_iguais(equipamento, equip_plan):
                candidatos_concluidos.append((i, row, "reabrir"))
            continue  # não adiciona concluídas nos candidatos normais

        # NÍVEL 2: usina + equipamento
        if equipamentos_sao_iguais(equipamento, equip_plan):
            candidatos.append((i, row, "equip"))
            continue

        # NÍVEL 3: fingerprint de palavras (fallback)
        fp_novo   = fingerprint_ocorrencia(usina, equipamento, falha)
        fp_plan   = fingerprint_ocorrencia(usina_plan, equip_plan, row[4])
        if fp_novo == fp_plan:
            candidatos.append((i, row, "fingerprint"))

    if not candidatos:
        # Tenta reabrir ocorrência recentemente concluída (reincidência < 7 dias)
        if candidatos_concluidos:
            i, row, _ = candidatos_concluidos[0]
            log.info(f"🔄 Reincidência detectada — reabrindo linha {i} | {row[3]} (concluída há ≤ 7 dias)")
            return (i, row)
        return None

    # Prioriza match por equipamento sobre fingerprint
    por_equip = [c for c in candidatos if c[2] == "equip"]
    if por_equip:
        i, row, _ = por_equip[0]
        log.info(f"🎯 Match NÍVEL 2 (usina+equip): linha {i} | {row[3]}")
        return (i, row)

    i, row, _ = candidatos[0]
    log.info(f"🎯 Match NÍVEL 3 (fingerprint): linha {i} | {row[3]}")
    return (i, row)


def acao_mudou(row, acao_nova):
    """
    Retorna True se a ação nova contém informação não presente no campo Ação
    atual nem no Histórico cronológico da planilha.
    """
    if vazio(acao_nova):
        return False
    acao_atual = _norm(row[7] if len(row) > 7 else "")
    historico  = _norm(row[11] if len(row) > 11 else "")
    acao_norm  = _norm(acao_nova)
    # Considera mudança se pelo menos 60% das palavras novas não estão no conteúdo atual
    palavras_novas = [p for p in acao_norm.split() if len(p) > 3]
    if not palavras_novas:
        return False
    ja_conhecidas = sum(1 for p in palavras_novas if p in acao_atual or p in historico)
    return (ja_conhecidas / len(palavras_novas)) < 0.6


def status_mudou(row, novo_status):
    """Retorna True se o status da planilha é diferente do novo."""
    atual = (row[8] if len(row) > 8 else "").strip().lower()
    novo  = (novo_status or "").strip().lower()
    return atual != novo and not vazio(novo_status)


# ── Operações na planilha ─────────────────────────────────────────────────────

def detectar_aguardando_fabricante(texto):
    """
    Retorna True quando o texto indica:
      A) Número de chamado com fabricante (Case #XXXXX, Chamado Nº XXXXX, etc.)
      B) Normalidade em campo (normal em campo, 100% normal, etc.)
    Nessa combinação → status deve ser 'Aguardando Fabricante'.
    """
    if not texto:
        return False
    t = texto.lower()
    import re as _re
    tem_chamado = bool(_re.search(
        r'(?:case|chamado|ticket|n°)\s*[#°]?\s*\d{5,}',
        t
    ))
    tem_normal_campo = any(p in t for p in [
        "normal em campo", "normalizado em campo", "normalidade em campo",
        "em campo está normal", "campo está normal",
        "em campo esta normal", "campo esta normal",
        "100% normal", "100 % normal", "normalizado no campo",
        "apresenta normalidade em campo",
    ])
    return tem_chamado and tem_normal_campo


def extrair_ticket_fabricante(texto):
    """
    Extrai o número/código do chamado/ticket do fabricante a partir do texto
    da mensagem, cobrindo os formatos reais usados no dia a dia:
      - Prefixados: "SOL-10596", "SOL - 12634", "RMA 25814"
      - Explícitos: "Chamado 25065", "Ticket 6843263", "Case #45231"
      - Contextuais: "Caso deferido 6843263", "Acionamento Fabricante 15817311"
    Retorna a string do ticket (ex: "SOL-10596") ou "" se não encontrar.
    Não confundir com 'os_num' (Número da OS interna), que é capturado
    separadamente por outro padrão.
    """
    if not texto:
        return ""

    # Formato prefixado: SOL-12345, SOL 12345, RMA-25814 (aceita espaços/
    # hífen variáveis ao redor do prefixo). Exclui prefixos que na prática
    # são outra coisa (código de rastreio, OS interna, etc.)
    m = re.search(r'\b([A-Z]{2,5})\s*[-\s]\s*(\d{4,})\b', texto, re.IGNORECASE)
    if m:
        prefixo = m.group(1).upper()
        numero  = m.group(2)
        if prefixo not in ("OS", "PV", "EP", "ID", "QN", "AD", "OY", "BR"):
            return f"{prefixo}-{numero}"

    # Formato explícito: "chamado/case/ticket/n°/rma + número longo"
    m = re.search(
        r'(?:case|chamado|ticket|n°|rma)\s*[#°]?\s*(\d{5,})',
        texto, re.IGNORECASE
    )
    if m:
        return m.group(1)

    # Contexto de fabricante/garantia próximo a um número isolado de 6-8
    # dígitos (cobre "Caso deferido 6843263", "Acionamento Fabricante 15817311")
    m = re.search(
        r'(?:fabricante|deferid[oa]|garantia)\D{0,30}\b(\d{6,8})\b',
        texto, re.IGNORECASE
    )
    if m:
        return m.group(1)

    return ""


# ── Detecção de gatilhos T1 / T2 / T3 (Tempo Ativo O&M) ────────────────────
#
# Estas funções alimentam as colunas M/N/O (Data 1ª Ação, Data Encaminhamento,
# Data Retorno Externo) da planilha. A regra de ouro é: o app.py SÓ grava
# nessas colunas se a célula estiver VAZIA — nunca sobrescreve uma correção
# manual feita por Fred na planilha. Essa checagem é feita em
# gravar_nova_ocorrencia() e atualizar_ocorrencia(), não aqui — estas funções
# apenas respondem True/False para o texto analisado.

def detectar_primeira_acao(texto):
    """
    Retorna True quando o texto da mensagem (de abertura ou de uma atualização)
    já indica que a equipe Grid começou a atuar na ocorrência — ex: técnico
    acionado, equipe a caminho, já verificando, etc.

    Cobre tanto o caso em que a 1ª ação vem embutida na própria mensagem de
    abertura (T1 ~ 0) quanto uma atualização posterior que só agora informa
    que a equipe agiu.
    """
    if not texto:
        return False
    t = texto.lower()
    termos = [
        "técnico acionado", "tecnico acionado",
        "equipe acionada", "equipe foi acionada",
        "técnico a caminho", "tecnico a caminho",
        "equipe a caminho", "em deslocamento",
        "já estamos verificando", "ja estamos verificando",
        "já está verificando", "ja esta verificando",
        "estamos verificando", "equipe verificando",
        "verificando em campo", "verificando a ocorrência",
        "técnico em campo", "tecnico em campo",
        "equipe em campo", "equipe em atendimento",
        "atendimento iniciado", "iniciado atendimento",
        "já em atendimento", "ja em atendimento",
        "técnico foi enviado", "tecnico foi enviado",
        "enviamos técnico", "enviamos tecnico",
        "deslocando equipe", "deslocando técnico", "deslocando tecnico",
        "iniciada a verificação", "iniciada a verificacao",
        "já estamos atuando", "ja estamos atuando",
        "equipe já está no local", "equipe ja esta no local",
        "técnico já está no local", "tecnico ja esta no local",
        "já iniciamos o atendimento", "ja iniciamos o atendimento",
    ]
    return any(p in t for p in termos)


def detectar_encaminhamento(texto):
    """
    Retorna True quando o texto indica que a ocorrência foi encaminhada para
    fora do controle direto da equipe Grid — fabricante, cliente ou
    fornecedor de equipamento. Reaproveita a varredura de
    detectar_aguardando_fabricante() e expande para Aguardando Cliente /
    Aguardando Equipamento.

    Marca o fim do T2 e o início do T3 (espera externa).
    """
    if not texto:
        return False
    t = texto.lower()

    # Reaproveita a lógica de chamado fabricante + campo normal
    if detectar_aguardando_fabricante(texto):
        return True

    termos = [
        "aguardando fabricante", "aguardando o fabricante",
        "aguardando cliente", "aguardando o cliente",
        "aguardando equipamento", "aguardando peça", "aguardando peca",
        "aguardando material", "aguardando envio",
        "encaminhado ao fabricante", "encaminhado para o fabricante",
        "encaminhado ao cliente", "encaminhado para o cliente",
        "chamado aberto no fabricante", "chamado aberto com o fabricante",
        "chamado aberto com fabricante", "chamado aberto fabricante",
        "os aberta no fabricante", "os aberta com o fabricante",
        "solicitado ao fabricante", "solicitamos ao fabricante",
        "acionamos o fabricante", "acionado o fabricante",
        "aguardando retorno do fabricante", "aguardando retorno fabricante",
        "aguardando posição do fabricante", "aguardando posicao do fabricante",
        "aguardando garantia", "em garantia",
        "peça solicitada", "peca solicitada",
        "material solicitado",
    ]
    return any(p in t for p in termos)


def detectar_retorno_externo(texto):
    """
    Retorna True quando o texto indica que o fabricante/cliente respondeu
    ou que o material/equipamento chegou — fim da espera externa (T3),
    início da execução final pela equipe Grid (T4).
    """
    if not texto:
        return False
    t = texto.lower()
    termos = [
        "fabricante retornou", "fabricante respondeu",
        "cliente retornou", "cliente respondeu",
        "peça chegou", "peca chegou",
        "material chegou", "equipamento chegou",
        "peça recebida", "peca recebida",
        "material recebido", "equipamento recebido",
        "peça entregue", "peca entregue",
        "material entregue",
        "retorno do fabricante", "retorno do cliente",
        "posição do fabricante", "posicao do fabricante",
        "fabricante enviou", "fabricante autorizou",
        "garantia aprovada", "garantia autorizada",
        "liberado pelo fabricante", "liberado pelo cliente",
        "chegou a peça", "chegou a peca", "chegou o material",
        "chegou o equipamento",
        "já estamos com a peça", "ja estamos com a peca",
        "já estamos com o material", "ja estamos com o material",
    ]
    return any(p in t for p in termos)


def atualizar_ocorrencia(ws, num_linha, row, dados, origem="qualquer"):
    """
    Atualiza uma ocorrência existente.

    REGRAS:
    - Status de ocorrência existente NUNCA é alterado por esta função,
      EXCETO quando detecta chamado fabricante + campo normal
      → define 'Aguardando Fabricante'.
    - Status só é definido na CRIAÇÃO (gravar_nova_ocorrencia) ou
      na NORMALIZAÇÃO (normalizar_ocorrencia → Concluído).
    - Histórico e Ação: sempre acrescenta (nunca substitui).
    """
    hoje = agora_br().strftime("%d/%m")

    # Ação — acrescenta (não sobrescreve)
    acao_nova = (dados.get("acao_texto") or "").strip()
    if not vazio(acao_nova):
        acao_atual = (row[7] if len(row) > 7 else "").strip()
        if acao_nova not in acao_atual:
            nova_acao = (acao_atual + "\n" + acao_nova).strip() if acao_atual else acao_nova
            ws.update_cell(num_linha, 8, nova_acao)

    # Histórico — sempre acrescenta entrada nova
    hist_atual = (row[11] if len(row) > 11 else "").strip()
    # Monta entrada do histórico com o texto mais informativo disponível
    if not vazio(acao_nova):
        entrada_hist = f"{hoje} - {acao_nova}"
    else:
        novo_status = dados.get("status", "")
        if not vazio(novo_status):
            entrada_hist = f"{hoje} - Status: {novo_status}"
        else:
            entrada_hist = f"{hoje} - Atualização"
    if entrada_hist not in hist_atual:
        novo_hist = (hist_atual + "\n" + entrada_hist).strip() if hist_atual else entrada_hist
        ws.update_cell(num_linha, 12, novo_hist)

    # Status — NUNCA é alterado ao atualizar ocorrência existente.
    # O status só muda em dois casos:
    #   1. Criação de nova ocorrência (gravar_nova_ocorrencia) — usa o status do parse
    #   2. Normalização (normalizar_ocorrencia) — define Concluído
    #   3. Detecção explícita de "Aguardando Fabricante" (chamado + campo normal)
    #
    # Mensagens de grupo (webhook ou ronda) NÃO alteram o status de ocorrências abertas.
    status_atual = (row[8] if len(row) > 8 else "").strip().lower()
    ja_concluido = any(x in status_atual for x in ["conclu", "resolv", "fechad"])

    if not ja_concluido:
        # Única exceção permitida: detecção de chamado fabricante + campo normal
        texto_analise = " ".join(filter(None, [
            acao_nova,
            dados.get("causa", ""),
            dados.get("falha", ""),
        ]))
        if detectar_aguardando_fabricante(texto_analise):
            if status_atual not in ["aguardando fabricante"]:
                ws.update_cell(num_linha, 9, "Aguardando Fabricante")
                log.info(f"   → Status → Aguardando Fabricante (chamado+campo normal): linha {num_linha}")
        # NENHUMA outra condição altera o status de uma ocorrência existente

    # OS — preenche se estava vazio
    os_num = dados.get("os", "")
    if not vazio(os_num):
        os_atual = (row[10] if len(row) > 10 else "").strip()
        if vazio(os_atual):
            ws.update_cell(num_linha, 11, os_num)

    # Ticket Fabricante (J) — extrai do texto e preenche SOMENTE se vazio
    # (preserva qualquer ticket já preenchido manualmente por Fred).
    try:
        ticket_atual = (row[9] if len(row) > 9 else "").strip()
        if vazio(ticket_atual):
            texto_para_ticket = " ".join(filter(None, [
                acao_nova, dados.get("falha", ""), dados.get("causa", ""),
            ]))
            ticket_novo = extrair_ticket_fabricante(texto_para_ticket)
            if ticket_novo:
                ws.update_cell(num_linha, 10, ticket_novo)
                log.info(f"   → Ticket Fabricante detectado e gravado: linha {num_linha} = {ticket_novo}")
    except Exception as e:
        log.error(f"[Ticket] Erro ao extrair/gravar ticket fabricante: {e}")

    # ── Tempo Ativo O&M (T1-T3) — colunas M/N/O ─────────────────────────────
    # Cada gatilho só grava se a célula correspondente ainda estiver vazia
    # (preserva qualquer correção manual feita por Fred direto na planilha).
    try:
        texto_gatilho = " ".join(filter(None, [
            acao_nova,
            dados.get("causa", ""),
            dados.get("falha", ""),
        ]))

        # N — Data 1ª Ação (T1): equipe começou a atuar
        if detectar_primeira_acao(texto_gatilho):
            gravar_data_se_vazia(ws, num_linha, 13, row, label="Data 1ª Ação")

        # O — Data Encaminhamento (T2 → T3): foi pra fabricante/cliente/equipamento
        if detectar_encaminhamento(texto_gatilho):
            gravar_data_se_vazia(ws, num_linha, 14, row, label="Data Encaminhamento")

        # P — Data Retorno Externo (T3 → T4): fabricante/cliente retornou
        if detectar_retorno_externo(texto_gatilho):
            gravar_data_se_vazia(ws, num_linha, 15, row, label="Data Retorno Externo")
    except Exception as e:
        log.error(f"[T1-T4] Erro ao avaliar gatilhos de tempo ativo O&M: {e}")

    # ── Mensagem Original (V) — anexa o texto bruto desta atualização ─────────
    try:
        msg_bruta = dados.get("mensagem_bruta", "")
        if not vazio(msg_bruta):
            anexar_mensagem_original(ws, num_linha, 21, row, msg_bruta)
    except Exception as e:
        log.error(f"[Mensagens] Erro ao anexar mensagem original: {e}")

    log.info(f"🔄 Atualizado linha {num_linha} | {dados['usina']} / {dados.get('equipamento','')} [{origem}]")



def normalizar_ocorrencia(ws, num_linha, row, dados):
    """Fecha uma ocorrência: status → Concluído + entrada no histórico."""
    hoje = agora_br().strftime("%d/%m")
    ws.update_cell(num_linha, 9, "Concluído")

    if not vazio(dados.get("os", "")):
        ws.update_cell(num_linha, 11, dados["os"])

    hist_atual   = (row[11] if len(row) > 11 else "").strip()
    nova_entrada = f"{hoje} - Ocorrência normalizada"
    acao_txt = dados.get("acao_texto", "")
    if not vazio(acao_txt):
        nova_entrada += f"\n{hoje} - {acao_txt}"
    novo_hist = (hist_atual + "\n" + nova_entrada).strip() if hist_atual else nova_entrada
    ws.update_cell(num_linha, 12, novo_hist)

    # ── Data de Fechamento (U) — gravada na normalização, SOMENTE se vazia ──
    # Mesma regra de prioridade das demais datas (M/N/O/P): preenchimento
    # manual sempre tem prioridade sobre o automático. Se Fred já corrigiu
    # essa célula manualmente, o robô nunca sobrescreve.
    try:
        gravar_data_se_vazia(ws, num_linha, 20, row, label="Data de Fechamento")
    except Exception as e:
        log.error(f"[T1-T4] Erro ao gravar Data de Fechamento: {e}")

    # ── Mensagem Original (V) — anexa a mensagem de normalização ──────────────
    try:
        msg_bruta = dados.get("mensagem_bruta", "")
        if not vazio(msg_bruta):
            anexar_mensagem_original(ws, num_linha, 21, row, msg_bruta)
    except Exception as e:
        log.error(f"[Mensagens] Erro ao anexar mensagem original na normalização: {e}")

    log.info(f"✅ Normalizado linha {num_linha} | {dados['usina']}")


def primeira_linha_vazia(todos):
    ultima_com_dado = 1
    for i, row in enumerate(todos[1:], start=2):
        if row and row[0] and str(row[0]).strip():
            ultima_com_dado = i
    return ultima_com_dado + 1


def gravar_nova_ocorrencia(ws, todos, dados):
    novo_id       = proximo_id(todos)
    proxima_linha = primeira_linha_vazia(todos)
    agora_str = agora_br().strftime("%d/%m/%Y %H:%M:%S")

    # Tenta extrair o número do chamado/ticket do fabricante já na abertura
    texto_para_ticket = " ".join(filter(None, [
        dados.get("acao", ""), dados.get("acao_texto", ""),
        dados.get("falha", ""), dados.get("causa", ""), dados.get("historico", ""),
    ]))
    ticket = extrair_ticket_fabricante(texto_para_ticket)

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
        ticket,
        dados["os"],
        dados["historico"],
    ]
    ws.update(f"A{proxima_linha}:L{proxima_linha}", [linha])
    log.info(f"➕ Nova ocorrência ID={novo_id} | {dados['usina']} — {dados['equipamento']} | linha {proxima_linha}")

    # ── Data de Abertura (M) — sempre gravada na criação ───────────────────
    try:
        ws.update_cell(proxima_linha, 13, agora_str)
    except Exception as e:
        log.error(f"[T1-T4] Erro ao gravar Data de Abertura: {e}")

    # ── Data 1ª Ação (N) — se a própria mensagem de abertura já indicar que
    #    a equipe começou a atuar (técnico acionado, equipe a caminho, etc.)
    try:
        texto_analise = " ".join(filter(None, [
            dados.get("acao", ""), dados.get("falha", ""), dados.get("historico", ""),
        ]))
        if detectar_primeira_acao(texto_analise):
            ws.update_cell(proxima_linha, 14, agora_str)
            log.info(f"   → Data 1ª Ação gravada na abertura (gatilho na própria mensagem): linha {proxima_linha}")
    except Exception as e:
        log.error(f"[T1-T4] Erro ao gravar Data 1ª Ação na abertura: {e}")

    # ── Mensagem Original (V) — guarda o texto bruto que originou o registro ──
    try:
        msg_bruta = dados.get("mensagem_bruta", "")
        if not vazio(msg_bruta):
            ws.update_cell(proxima_linha, 22, f"{agora_br().strftime('%d/%m %H:%M')} - {msg_bruta.strip()}")
    except Exception as e:
        log.error(f"[Mensagens] Erro ao gravar mensagem original na abertura: {e}")


    # Notificação push — nova ocorrência
    try:
        usina_nome = dados.get("usina", "")
        equip_nome = dados.get("equipamento", "")
        falha_txt  = dados.get("falha", "")
        cliente    = dados.get("cliente", "")

        # Detecta desligamento
        fc = (falha_txt + " " + dados.get("causa", "")).lower()
        eh_deslig = bool(re.search(
            r"usina\s+desligad|ufv\s+desligad|desligamento\s+da\s+usina|usina\s+parad", fc
        ))

        if eh_deslig:
            enviar_push(
                titulo=f"⚡ USINA DESLIGADA — {usina_nome}",
                corpo=f"{falha_txt or 'Usina sem geração'} · {cliente}",
                tipo="desligamento",
                url=f"https://fred-alexandrino.github.io/PAINELDEFALHAS/?ocorrencia={novo_id}",
            )
        else:
            enviar_push(
                titulo=f"🔴 Nova falha — {usina_nome}",
                corpo=f"{equip_nome}: {falha_txt[:80] if falha_txt else 'Nova ocorrência registrada'} · {cliente}",
                tipo="nova_ocorrencia",
                url=f"https://fred-alexandrino.github.io/PAINELDEFALHAS/?ocorrencia={novo_id}",
            )
    except Exception as e:
        log.error(f"[Push] Erro ao notificar nova ocorrência: {e}")

    return novo_id


# ── Processamento principal ───────────────────────────────────────────────────
#
# LÓGICA POR BLOCO (mesma para tempo real e botão Verificar Rondas):
#
#  1. Parseia o bloco → extrai usina, equipamento, falha, ação, status, OS
#  2. Busca na planilha por fingerprint (usina + tipo_equip + num + palavras_falha)
#
#  CASO A — NÃO encontrou na planilha:
#    → CRIA nova linha
#
#  CASO B — Encontrou, é normalização (✅ NORMALIZADO):
#    → FECHA a ocorrência (status = Concluído, histórico atualizado)
#
#  CASO C — Encontrou, ação NÃO mudou e status NÃO mudou:
#    → IGNORA (mensagem repetida de ronda sem informação nova)
#
#  CASO D — Encontrou, ação OU status mudou:
#    → ATUALIZA (acrescenta ação + entrada no histórico + status se diferente)

def processar_texto(texto, origem="webhook"):
    ws     = get_sheet()
    todos  = carregar_planilha(ws)
    blocos = separar_blocos(texto)
    resultado = {"novos": [], "atualizados": [], "normalizados": [], "ignorados": 0}

    for bloco in blocos:
        dados = parse_bloco(bloco)
        if not dados:
            resultado["ignorados"] += 1
            continue

        usina     = dados.get("usina", "")
        equip     = dados.get("equipamento", "")
        falha     = dados.get("falha", "")
        normalizar = dados.get("normalizar", False)

        # ── Caso especial: formato com atualizações individuais por ativo ──
        # Ex: "Tracker 3 normalizado, Tracker 5 em garantia"
        atualizacoes_individuais = extrair_atualizacoes_por_ativo(dados.get("acao_texto", ""))

        if atualizacoes_individuais:
            alguma_acao = False
            for upd in atualizacoes_individuais:
                existente = buscar_por_fingerprint(todos, usina, upd["equipamento"], falha, dados.get("os",""))
                if existente:
                    num_linha, row = existente
                    if upd["normalizar"]:
                        normalizar_ocorrencia(ws, num_linha, row, {
                            **dados,
                            "acao_texto": upd["acao_resumida"],
                            "os": dados.get("os", ""),
                        })
                        resultado["normalizados"].append(f"{usina} - {upd['equipamento']}")
                    else:
                        atualizar_ocorrencia(ws, num_linha, row, {
                            **dados,
                            "acao_texto": upd["acao_resumida"],
                        }, origem=origem)
                        resultado["atualizados"].append(f"{usina} - {upd['equipamento']}")
                    alguma_acao = True
                    todos = carregar_planilha(ws)
            if not alguma_acao:
                # Nenhum ativo encontrado → cria novo
                novo_id = gravar_nova_ocorrencia(ws, todos, dados)
                resultado["novos"].append({"id": novo_id, "usina": usina})
                todos = carregar_planilha(ws)
            continue

        # ── Múltiplos inversores numa mesma mensagem ──────────────────────
        # Ex: "Inversores 6 e 7" → cria/atualiza INV-06 e INV-07 separadamente
        multi_inv = extrair_inversores_multiplos(bloco, dados)
        if multi_inv:
            for dados_inv in multi_inv:
                existente_inv = buscar_por_fingerprint(todos, dados_inv["usina"], dados_inv["equipamento"], dados_inv["falha"], dados_inv.get("os",""))
                if not existente_inv:
                    novo_id = gravar_nova_ocorrencia(ws, todos, dados_inv)
                    resultado["novos"].append({"id": novo_id, "usina": dados_inv["usina"]})
                    todos = carregar_planilha(ws)
                elif dados_inv.get("normalizar"):
                    num_linha, row = existente_inv
                    normalizar_ocorrencia(ws, num_linha, row, dados_inv)
                    resultado["normalizados"].append(f"{dados_inv['usina']} - {dados_inv['equipamento']}")
                    todos = carregar_planilha(ws)
                else:
                    num_linha, row = existente_inv
                    if acao_mudou(row, dados_inv.get("acao_texto","")):
                        atualizar_ocorrencia(ws, num_linha, row, dados_inv, origem="ronda")
                        resultado["atualizados"].append(f"{dados_inv['usina']} - {dados_inv['equipamento']}")
                        todos = carregar_planilha(ws)
                    else:
                        resultado["ignorados"] += 1
            continue  # pula o fluxo principal — já foi tratado acima

        # ── Normaliza nomenclatura de inversores na falha ──────────────────
        dados["falha"]        = normalizar_inversores(dados.get("falha", ""))
        dados["equipamento"]  = _limpar_equipamento(dados.get("equipamento", ""))
        dados["equip_impact"] = dados["equipamento"]
        equip = dados["equipamento"]
        falha = dados["falha"]

        # ── Fluxo principal ────────────────────────────────────────────────
        existente = buscar_por_fingerprint(todos, usina, equip, falha, dados.get("os",""))

        # Se não encontrou aberta e é normalização, busca também nas concluídas
        # (para não criar linha nova quando a ocorrência já estava concluída em outro grupo)
        if not existente and normalizar:
            for i2, row2 in enumerate(todos[1:], start=2):
                if len(row2) < 4: continue
                if not usinas_sao_iguais(usina, row2[2].strip()): continue
                if equipamentos_sao_iguais(equip, row2[3].strip()):
                    existente = (i2, row2)
                    log.info(f"[Normaliz] Encontrada ocorrência (incl. concluídas) para {usina} / {equip}: linha {i2}")
                    break

        if not existente and normalizar:
            # Normalização sem ocorrência existente — ignora, não cria linha nova
            log.info(f"[Normaliz] Sem ocorrência para normalizar — ignorando: {usina} / {equip}")
            resultado["ignorados"] += 1

        elif not existente:
            # CASO A — nova ocorrência
            novo_id = gravar_nova_ocorrencia(ws, todos, dados)
            resultado["novos"].append({"id": novo_id, "usina": usina})
            todos = carregar_planilha(ws)

        elif normalizar:
            # CASO B — normalização / conclusão
            num_linha, row = existente
            status_atual = row[8].strip().lower() if len(row) > 8 else ""
            if "conclu" in status_atual or "resolv" in status_atual:
                log.info(f"[Normaliz] Já concluída — ignorando duplicata: {usina} / {equip}")
                resultado["ignorados"] += 1
            else:
                normalizar_ocorrencia(ws, num_linha, row, dados)
                resultado["normalizados"].append(usina)
                todos = carregar_planilha(ws)

        else:
            num_linha, row = existente
            acao_nova = dados.get("acao_texto", "")

            # CASO D: atualiza apenas se houver ação nova OU chamado+campo normal
            # Status NUNCA é alterado ao atualizar ocorrência existente
            tem_info_nova = acao_mudou(row, acao_nova)
            tem_aguardando = detectar_aguardando_fabricante(
                " ".join(filter(None, [acao_nova, dados.get("causa",""), dados.get("falha","")]))
            )

            if tem_info_nova or tem_aguardando:
                atualizar_ocorrencia(ws, num_linha, row, dados, origem="ronda")
                resultado["atualizados"].append(usina)
                todos = carregar_planilha(ws)
            else:
                # CASO C — nenhuma informação nova → ignora
                log.info(f"⏭️  Sem novidade: {usina} / {equip} — ignorado")
                resultado["ignorados"] += 1

    return resultado


def eh_ronda_status_ok(texto):
    """
    Retorna True quando a mensagem é uma ronda de status informando que
    tudo está OK na usina — sem falhas, sem ocorrências.

    Detecta combinações como:
    - "RONDA DIÁRIA" + "Sem Ocorrência" (em Ocorrências durante o turno E pendentes)
    - "<usina> OK." sem emoji de falha (🔴🟡🟠)
    - "Status Atual:" + "<usina> OK" + "Sem Ocorrência"
    """
    t = texto.lower()

    # Presença de emoji de falha = há problema real → não ignorar
    tem_falha_emoji = bool(re.search(r"🔴|🟡|🟠|⏸️", texto))
    if tem_falha_emoji:
        return False

    # Padrão 1: RONDA DIÁRIA / RONDA / Status do dia com "Sem Ocorrência" explícito
    eh_ronda = bool(re.search(
        r"(?:ronda\s+di[aá]ria|status\s+do\s+dia|status\s+operacional|cos\s+[-–]\s*grid|ronda\s+de\s+campo)",
        t
    ))
    sem_ocorrencia = bool(re.search(
        r"sem\s+ocorr[eê]ncia|sem\s+ocorr[eê]ncias|sem\s+ocorr[eê]nci[ao]",
        t
    ))

    if eh_ronda and sem_ocorrencia:
        return True

    # Padrão 2: "<usina nome> OK." sem qualquer desvio (formato COS Grid OK)
    # Ex: "ABC Morada Nova OK." ou "Araputanga OK."
    tem_usina_ok = bool(re.search(r"\w[\w\s]+\s+ok\.", t))
    tem_desvio = bool(re.search(
        r"desvio|falha|problema|ocorr[eê]ncia|parado|desligad|comunica[cç]",
        t
    ))
    if tem_usina_ok and not tem_desvio and sem_ocorrencia:
        return True

    return False


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Recebe mensagens em tempo real do server.js.
    Chamado automaticamente pelo monitoramento — não depende do botão.
    """
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

        data    = payload.get("data", {})
        msg_obj = data if "message" in data else payload

        if msg_obj.get("key", {}).get("fromMe"):
            return jsonify({"status": "ignored", "reason": "own message"}), 200

        message = msg_obj.get("message", {})
        texto   = (
            message.get("conversation") or
            message.get("extendedTextMessage", {}).get("text") or ""
        )

        if not texto:
            return jsonify({"status": "ignored", "reason": "no text"}), 200

        remote_jid = msg_obj.get("key", {}).get("remoteJid", "")
        if "@g.us" not in remote_jid:
            return jsonify({"status": "ignored", "reason": "not a group"}), 200

        if GRUPOS_FILTRO and GRUPOS_FILTRO[0]:
            if not any(g.strip() in remote_jid for g in GRUPOS_FILTRO):
                return jsonify({"status": "ignored", "reason": "group not in filter"}), 200

        if eh_atualizacao_atividade(texto):
            grupo_nome = remote_jid.split("@")[0]
            gravar_log_mensagem(remote_jid, grupo_nome, texto)
            resultado_ativ = processar_atualizacao_atividade(texto, editor=f"tecnico:{grupo_nome}")
            log.info(f"[Atividades WhatsApp] grupo={grupo_nome} resultado={resultado_ativ}")
            return jsonify({"status": "ok", "tipo": "atividade", **resultado_ativ}), 200

        tem_usina  = bool(re.search(r"Usina:", texto, re.IGNORECASE))
        tem_emoji  = bool(re.search(r"🔴|🟡|🟢|🟠|✅|⏸️", texto))
        tem_bullet = eh_formato_cos_grid(texto)

        if not tem_usina and not tem_emoji and not tem_bullet:
            return jsonify({"status": "ignored", "reason": "no failure content"}), 200

        # Ignora mensagens de ronda diária que informam tudo OK / sem ocorrência
        if eh_ronda_status_ok(texto):
            gravar_log_mensagem(remote_jid, remote_jid.split("@")[0], texto)
            return jsonify({"status": "ignored", "reason": "ronda_diaria_ok"}), 200

        # Grava no log antes de processar (para histórico de varredura)
        grupo_nome = remote_jid.split("@")[0]
        gravar_log_mensagem(remote_jid, grupo_nome, texto)

        resultado = processar_texto(texto)

        total = len(resultado["novos"]) + len(resultado["atualizados"]) + len(resultado["normalizados"])
        if total > 0:
            log.info(f"✅ [Tempo real] {len(resultado['novos'])} novos, {len(resultado['atualizados'])} atualizados, {len(resultado['normalizados'])} normalizados")

        # Limpeza automática: remove linhas do log com mais de 5 dias
        try:
            removidas = limpar_log_antigo()
            if removidas > 0:
                log.info(f"🧹 [Rondas] Log limpo: {removidas} linha(s) com mais de 5 dias removidas")
        except Exception as e_clean:
            log.warning(f"[Rondas] Limpeza do log falhou (não crítico): {e_clean}")

        return jsonify({"status": "ok", **resultado}), 200

    except Exception as e:
        log.error(f"❌ Erro no webhook: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/processar-texto-manual", methods=["POST"])
def processar_texto_manual():
    """Ferramenta de recuperação: processa manualmente o texto de uma
    mensagem de ocorrência (útil quando uma mensagem real chegou num
    grupo do WhatsApp mas não foi capturada — ex.: sessão do WhatsApp
    caiu no momento). Usa o mesmo parser do webhook normal, então o
    resultado fica idêntico ao que teria acontecido automaticamente."""
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    texto = body.get("texto", "")
    resultado = processar_texto(texto)
    return jsonify({"ok": True, **resultado}), 200


@app.route("/rondas", methods=["POST"])
def verificar_rondas():
    """
    Botão "Verificar Rondas" do dashboard.

    Busca as mensagens das últimas 6 horas em cada grupo configurado
    via GET /api/messages/:grupoId no server.js, e processa as relevantes.

    O monitoramento em tempo real NÃO é afetado por este endpoint.

    NOTA: Este endpoint é chamado diretamente pelo dashboard (GitHub Pages)
    via fetch(). Por isso NÃO exige WEBHOOK_SECRET — a autenticação é feita
    pelo login do próprio dashboard. O WEBHOOK_SECRET é usado apenas na
    comunicação interna entre server.js → /webhook.

    Body (opcional):
      { "horas": 6 }
    """
    try:
        payload = request.get_json(force=True) or {}
        horas   = int(payload.get("horas", 6))

        if not WPP_SERVER_URL:
            return jsonify({
                "ok":    False,
                "error": "WPP_SERVER_URL não configurado",
                "hint":  "Adicione a variável de ambiente WPP_SERVER_URL com a URL do servidor Baileys (server.js)",
            }), 400

        log.info(f"[Rondas] Iniciando varredura no log | últimas {horas}h")

        resultado_total = {
            "novos":        [],
            "atualizados":  [],
            "normalizados": [],
            "ignorados":    0,
            "mensagens_lidas": 0,
            "mensagens_processadas": 0,
        }

        # Lê mensagens do log das últimas N horas
        mensagens = ler_log_mensagens(horas)
        resultado_total["mensagens_lidas"] = len(mensagens)

        # Marca todas as mensagens como processadas em lote após processar
        ws_log = get_log_sheet()
        rows_log = ws_log.get_all_values()
        linhas_para_marcar = []

        for i, msg in enumerate(mensagens):
            texto = msg.get("texto", "")
            if not texto:
                continue

            # Filtra apenas mensagens de ronda/ocorrência
            tem_usina  = bool(re.search(r"Usina:", texto, re.IGNORECASE))
            tem_emoji  = bool(re.search(r"🔴|🟡|🟢|🟠|✅|⏸️", texto))
            tem_desvio = bool(re.search(r"DESVIO:", texto, re.IGNORECASE))
            tem_bullet = eh_formato_cos_grid(texto)

            relevante = tem_usina or tem_emoji or tem_desvio or tem_bullet

            # Mensagem de ronda diária informando tudo OK → não cria ocorrências
            if relevante and eh_ronda_status_ok(texto):
                relevante = False
                log.info(f"[Rondas] Ronda diária OK ignorada: {texto[:60]!r}")

            if relevante:
                try:
                    res = processar_texto(texto, origem="ronda")
                    resultado_total["novos"]        += res.get("novos", [])
                    resultado_total["atualizados"]  += res.get("atualizados", [])
                    resultado_total["normalizados"] += res.get("normalizados", [])
                    resultado_total["ignorados"]    += res.get("ignorados", 0)
                    resultado_total["mensagens_processadas"] += 1
                except Exception as e:
                    log.error(f"[Rondas] Erro ao processar mensagem: {e}")

            # Marca como processada (relevante ou não) para não reprocessar
            linhas_para_marcar.append(msg.get("linha_idx"))

        # Marca em lote no Sheets — uma única requisição para todas as linhas
        try:
            if linhas_para_marcar:
                idxs_validos = [idx for idx in linhas_para_marcar if idx]
                if idxs_validos:
                    # batch_update: uma única chamada à API
                    ws_log.batch_update([{
                        'range': f'E{idx}',
                        'values': [['✅']]
                    } for idx in idxs_validos])
                    log.info(f"[Rondas] {len(idxs_validos)} mensagens marcadas como processadas")
        except Exception as e:
            log.warning(f"[Rondas] Erro ao marcar processadas: {e}")

        total = (len(resultado_total["novos"]) +
                 len(resultado_total["atualizados"]) +
                 len(resultado_total["normalizados"]))

        log.info(f"[Rondas] Concluído: {total} ação(ões) | {resultado_total['mensagens_lidas']} msgs lidas do log")
        return jsonify({"ok": True, "horas_verificadas": horas, **resultado_total}), 200

    except Exception as e:
        log.error(f"[Rondas] Erro geral: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Notificações Push ────────────────────────────────────────────────────────

def get_push_sheet():
    """Retorna a aba 'Push Subscriptions', criando-a com cabeçalho se não existir."""
    gc = get_gc()
    sh = gc.open_by_key(SHEET_ID)
    try:
        return sh.worksheet(PUSH_SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=PUSH_SHEET_NAME, rows=200, cols=3)
        ws.update("A1:C1", [["Endpoint", "Subscription", "DataCriacao"]])
        return ws

def carregar_push_subscriptions():
    """
    Carrega as subscriptions salvas na planilha para dentro de _push_subscriptions
    (em memória). Chamado uma vez na inicialização do processo, para que os
    dispositivos cadastrados sobrevivam a reinícios do Render.
    """
    try:
        ws = get_push_sheet()
        rows = ws.get_all_values()[1:]  # pula cabeçalho
        carregadas = 0
        for row in rows:
            if len(row) < 2 or not row[0] or not row[1]:
                continue
            try:
                _push_subscriptions[row[0]] = json.loads(row[1])
                carregadas += 1
            except (json.JSONDecodeError, TypeError):
                continue
        log.info(f"[Push] {carregadas} subscription(ões) carregada(s) da planilha")
    except Exception as e:
        log.error(f"[Push] Erro ao carregar subscriptions da planilha: {e}")

def salvar_push_subscription(endpoint, sub):
    """Persiste (ou atualiza) uma subscription na planilha. Retorna True/False."""
    try:
        ws = get_push_sheet()
        cell = ws.find(endpoint, in_column=1)
        linha = [endpoint, json.dumps(sub), agora_br().strftime("%d/%m/%Y %H:%M:%S")]
        if cell:
            ws.update(f"A{cell.row}:C{cell.row}", [linha])
        else:
            ws.append_row(linha)
        return True
    except Exception as e:
        log.error(f"[Push] Erro ao salvar subscription na planilha: {e}", exc_info=True)
        return False

def remover_push_subscription(endpoint):
    """Remove uma subscription da planilha (expirada ou desativada pelo usuário)."""
    try:
        ws = get_push_sheet()
        cell = ws.find(endpoint, in_column=1)
        if cell:
            ws.delete_rows(cell.row)
    except Exception as e:
        log.error(f"[Push] Erro ao remover subscription da planilha: {e}")

def enviar_push(titulo, corpo, tipo="geral", url="https://fred-alexandrino.github.io/PAINELDEFALHAS/"):
    """
    Envia notificação push para todos os dispositivos registrados.
    tipo: "desligamento" | "nova_ocorrencia" | "geral"
    """
    if not PUSH_ENABLED:
        log.warning("[Push] pywebpush não disponível")
        return 0
    if not VAPID_PRIVATE_KEY:
        log.warning("[Push] VAPID_PRIVATE_KEY não configurada")
        return 0
    if not _push_subscriptions:
        log.info("[Push] Nenhum dispositivo registrado")
        return 0

    payload = json.dumps({
        "title": titulo,
        "body":  corpo,
        "tipo":  tipo,
        "url":   url,
        "tag":   f"painel-{tipo}",
        "icon":  "https://fred-alexandrino.github.io/PAINELDEFALHAS/icon-192.png",
    })

    enviados = 0
    expirados = []
    for endpoint, sub in list(_push_subscriptions.items()):
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS,
                headers={"Urgency": "high"},
                ttl=86400,
            )
            enviados += 1
            log.info(f"[Push] Enviado para {endpoint[:40]}...")
        except WebPushException as e:
            if "410" in str(e) or "404" in str(e):
                # Subscription expirada — remove
                expirados.append(endpoint)
                log.info(f"[Push] Subscription expirada removida: {endpoint[:40]}")
            else:
                log.error(f"[Push] Erro ao enviar: {e}")
        except Exception as e:
            log.error(f"[Push] Erro inesperado: {e}")

    for ep in expirados:
        _push_subscriptions.pop(ep, None)
        remover_push_subscription(ep)

    log.info(f"[Push] {enviados} notificação(ões) enviada(s)")
    return enviados


@app.route("/push/subscribe", methods=["POST", "OPTIONS"])
def push_subscribe():
    """
    Registra subscription de notificação push de um dispositivo.
    Chamado pelo dashboard ao clicar em "Ativar Notificações".
    """
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200

    try:
        payload = request.get_json(force=True) or {}
        sub = payload.get("subscription")
        if not sub or not sub.get("endpoint"):
            return jsonify({"error": "subscription inválida"}), 400

        endpoint = sub["endpoint"]
        ja_existia = endpoint in _push_subscriptions
        _push_subscriptions[endpoint] = sub

        salvo = salvar_push_subscription(endpoint, sub)
        if not salvo:
            # Reverte o registro em memória — melhor reportar erro real do que
            # fingir sucesso e perder essa subscription num futuro restart.
            _push_subscriptions.pop(endpoint, None)
            log.error(f"[Push] FALHA ao persistir subscription (endpoint não salvo na planilha): {endpoint[:60]}...")
            return jsonify({"ok": False, "error": "Falha ao salvar a inscrição no servidor. Tente novamente em instantes."}), 500

        log.info(f"[Push] Subscription registrada ({'já existia' if ja_existia else 'nova'}): {endpoint[:60]}...")
        log.info(f"[Push] Total de dispositivos: {len(_push_subscriptions)}")

        # Envia notificação de boas-vindas apenas para dispositivos realmente novos
        if not ja_existia:
            try:
                webpush(
                    subscription_info=sub,
                    data=json.dumps({
                        "title": "🔔 Painel O&M — Notificações ativas!",
                        "body":  "Você receberá alertas de desligamentos e novas ocorrências.",
                        "tipo":  "geral",
                        "url":   "https://fred-alexandrino.github.io/PAINELDEFALHAS/",
                    }),
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=VAPID_CLAIMS,
                    headers={"Urgency": "high"},
                    ttl=86400,
                ) if PUSH_ENABLED and VAPID_PRIVATE_KEY else None
            except Exception as e:
                log.warning(f"[Push] Erro na notificação de boas-vindas: {e}")

        return jsonify({"ok": True, "total": len(_push_subscriptions)}), 200

    except Exception as e:
        log.error(f"[Push] Erro ao registrar subscription: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/push/unsubscribe", methods=["POST", "OPTIONS"])
def push_unsubscribe():
    """
    Remove a subscription de notificação push de um dispositivo (do backend
    e da planilha). Chamado pelo dashboard ao clicar em "Desativar Notificações".
    """
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200

    try:
        payload = request.get_json(force=True) or {}
        endpoint = payload.get("endpoint") or (payload.get("subscription") or {}).get("endpoint")
        if not endpoint:
            return jsonify({"error": "endpoint não informado"}), 400

        _push_subscriptions.pop(endpoint, None)
        remover_push_subscription(endpoint)
        log.info(f"[Push] Subscription removida: {endpoint[:60]}...")
        return jsonify({"ok": True, "total": len(_push_subscriptions)}), 200

    except Exception as e:
        log.error(f"[Push] Erro ao remover subscription: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/push/test", methods=["POST"])
def push_test():
    """Envia notificação de teste para todos os dispositivos registrados."""
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"error": "unauthorized"}), 401
    n = enviar_push(
        titulo="🧪 Teste — Painel O&M",
        corpo="Se você está vendo isso, as notificações estão funcionando!",
        tipo="geral",
    )
    return jsonify({"ok": True, "enviados": n}), 200


@app.route("/notificar-edicao-planilha", methods=["POST"])
def notificar_edicao_planilha():
    """
    Recebido do Apps Script (gatilho onEdit) sempre que alguém edita
    manualmente qualquer célula da planilha pela interface do Google Sheets.
    Edições feitas por script/API (bot WhatsApp, dashboard, gspread) NÃO
    disparam o onEdit do Google — só edição humana direta na UI.
    """
    try:
        if SHEET_EDIT_SECRET:
            secret = request.headers.get("X-Sheet-Secret", "")
            if secret != SHEET_EDIT_SECRET:
                return jsonify({"error": "unauthorized"}), 401

        body = request.get_json(force=True) or {}
        aba          = body.get("aba", "planilha")
        linha        = body.get("linha", "")
        cabecalho    = body.get("cabecalho") or f"coluna {body.get('coluna', '?')}"
        valor_antigo = body.get("valorAntigo", "")
        valor_novo   = body.get("valorNovo", "")
        usuario      = body.get("usuario", "desconhecido")
        id_registro  = str(body.get("idValor", "")).strip()

        titulo = f"✏️ Edição manual — {aba}"
        corpo = (f"Linha {linha} · {cabecalho}: "
                 f"\"{valor_antigo or '—'}\" → \"{valor_novo or '—'}\" (por {usuario})")

        url = "https://fred-alexandrino.github.io/PAINELDEFALHAS/"
        if id_registro:
            if aba == ATIVIDADES_SHEET_NAME:
                url += f"?atividade={id_registro}"
            elif aba == SHEET_NAME:
                url += f"?ocorrencia={id_registro}"

        n = enviar_push(titulo=titulo, corpo=corpo, tipo="edicao_manual", url=url)
        return jsonify({"ok": True, "enviados": n}), 200
    except Exception as e:
        log.error(f"[EdicaoPlanilha] Erro: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/rondas/grupos", methods=["POST"])
def rondas_por_grupo():
    """
    Retorna as últimas mensagens de CADA grupo monitorado — somente leitura.
    Usa ler_log_historico() que inclui mensagens já processadas.
    """
    try:
        payload = request.get_json(force=True) or {}
        horas   = int(payload.get("horas", 24))
        # Usa histórico completo (inclui processadas) — somente para visualização
        mensagens = ler_log_historico(horas)

        # Agrupa por grupo_id
        grupos_map = {}
        for msg in mensagens:
            gid = msg.get("grupo_id", "")
            if gid not in grupos_map:
                grupos_map[gid] = []
            grupos_map[gid].append({
                "texto":      msg.get("texto", ""),
                "timestamp":  msg.get("timestamp", ""),
                "processado": msg.get("processado", False),
            })

        grupos = []
        for gid, msgs in grupos_map.items():
            grupos.append({
                "id":        gid,
                "total":     len(msgs),
                "mensagens": msgs[-5:],  # últimas 5 por grupo
            })

        # Garante que todos os grupos configurados aparecem (mesmo sem msgs)
        ids_com_msgs = {g["id"] for g in grupos}
        for gid in GRUPOS_FILTRO:
            gid = gid.strip()
            if gid and gid not in ids_com_msgs:
                grupos.append({"id": gid, "total": 0, "mensagens": []})

        return jsonify({"ok": True, "horas": horas, "grupos": grupos}), 200

    except Exception as e:
        log.error(f"[Grupos] Erro: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


APP_VERSION = "2026-07-01-fix-get_sheet"

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":     "ok",
        "version":    APP_VERSION,
        "timestamp":  agora_br().isoformat(),
        "wpp_server": WPP_SERVER_URL or "não configurado",
    }), 200


# ── Mapeamento de campo (nome JS) → coluna na planilha (1-based) ──────────
CAMPO_COL = {
    "falha":               5,
    "causa":               6,
    "impactados":          7,
    "acao":                8,
    "status":              9,
    "ticketFabricante":    10,
    "numeroOS":            11,
    "historico":           12,
    "dataAbertura":        13,
    "dataPrimeiraAcao":    14,
    "dataEncaminhamento":  15,
    "dataRetornoExterno":  16,
    "dataFechamento":      21,
}

@app.route("/atualizar-campo", methods=["POST", "OPTIONS"])
def atualizar_campo():
    """
    Endpoint chamado pelo dashboard para salvar alterações de campo individual.
    Body JSON: { id, field, value, editor, append? }
    """
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "Body inválido"}), 400

    ocorrencia_id = str(body.get("id", "")).strip()
    field         = str(body.get("field", "")).strip()
    value         = str(body.get("value", "")).strip()
    append        = body.get("append", False)
    editor        = str(body.get("editor", "dashboard")).strip()

    if not ocorrencia_id or not field:
        return jsonify({"ok": False, "error": "id e field são obrigatórios"}), 400

    col = CAMPO_COL.get(field)
    if col is None:
        return jsonify({"ok": False, "error": f"Campo '{field}' não mapeado"}), 400

    try:
        ws   = get_sheet()
        rows = ws.get_all_values()
    except Exception as e:
        log.error(f"[atualizar-campo] Erro ao abrir planilha: {e}")
        return jsonify({"ok": False, "error": f"Erro ao acessar planilha: {str(e)}"}), 500

    # Busca por ID + Equipamento + OS (chave composta para evitar colisão de IDs duplicados)
    # Body pode trazer campos extras: equipamento, numeroOS
    equip_busca = str(body.get("equipamento", "")).strip().upper()
    os_busca    = str(body.get("numeroOS", body.get("os", ""))).strip()

    def _norm_id(v):
        v = str(v).strip()
        try: v = str(int(float(v)))
        except: pass
        return v

    ocorrencia_id_norm = _norm_id(ocorrencia_id)
    candidatos = []
    for i, row in enumerate(rows[1:], start=2):
        if not row or len(row) < 1:
            continue
        if _norm_id(row[0]) == ocorrencia_id_norm:
            candidatos.append((i, row))

    num_linha = None
    if len(candidatos) == 1:
        # ID único — usa direto
        num_linha = candidatos[0][0]
    elif len(candidatos) > 1:
        # ID duplicado — refina por Equipamento (col D = índice 3) e OS (col K = índice 10)
        for (i, row) in candidatos:
            row_equip = row[3].strip().upper() if len(row) > 3 else ""
            row_os    = row[10].strip() if len(row) > 10 else ""
            equip_match = (not equip_busca) or (equip_busca in row_equip) or (row_equip in equip_busca)
            os_match    = (not os_busca) or (os_busca == row_os)
            if equip_match and os_match:
                num_linha = i
                break
        # Se não achou com os dois critérios, tenta só por equipamento
        if num_linha is None and equip_busca:
            for (i, row) in candidatos:
                row_equip = row[3].strip().upper() if len(row) > 3 else ""
                if equip_busca in row_equip or row_equip in equip_busca:
                    num_linha = i
                    break
        # Último recurso: primeiro candidato
        if num_linha is None:
            num_linha = candidatos[0][0]
            log.warning(f"[atualizar-campo] ID {ocorrencia_id} duplicado, sem match por equip/OS — usando linha {num_linha}")

    if num_linha is None:
        ids_existentes = [_norm_id(r[0]) for r in rows[1:5] if r]
        log.warning(f"[atualizar-campo] ID {ocorrencia_id!r} não encontrado. Primeiros IDs: {ids_existentes}")
        return jsonify({"ok": False, "error": f"Ocorrência {ocorrencia_id} não encontrada"}), 404

    log.info(f"[atualizar-campo] ID={ocorrencia_id} → linha={num_linha} (candidatos={len(candidatos)}, equip={equip_busca!r})")

    try:
        if field == "historico" and append:
            # Acrescenta ao histórico existente sem sobrescrever
            hist_atual = (rows[num_linha - 1][11] if len(rows[num_linha - 1]) > 11 else "").strip()
            hoje = agora_br().strftime("%d/%m")
            nova_entrada = f"{hoje} - {value}"
            # Deduplicação: não adiciona se já existir
            if nova_entrada in hist_atual or value in hist_atual.split("\n")[-1]:
                return jsonify({"ok": True, "dedup": True}), 200
            novo_hist = (hist_atual + "\n" + nova_entrada).strip() if hist_atual else nova_entrada
            ws.update_cell(num_linha, col, novo_hist)
        else:
            ws.update_cell(num_linha, col, value)
        log.info(f"[atualizar-campo] ✅ GRAVADO ID={ocorrencia_id} linha={num_linha} campo={field} valor={value[:40]!r}")
        return jsonify({"ok": True, "linha": num_linha}), 200
    except Exception as e:
        log.error(f"[atualizar-campo] Erro ao gravar: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/nova-ocorrencia", methods=["POST", "OPTIONS"])
def nova_ocorrencia_dashboard():
    """
    Registra uma nova ocorrência criada manualmente pelo dashboard.
    Body JSON: { cliente, usina, equipamento, falha, causa, acao, status, historico, editor }
    """
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "Body inválido"}), 400

    cliente    = body.get("cliente", "").strip()
    usina      = body.get("usina", "").strip()
    equipamento= body.get("equipamento", "").strip()
    falha      = body.get("falha", "").strip()
    causa      = body.get("causa", "").strip()
    acao       = body.get("acao", "").strip()
    status     = body.get("status", "Em Aberto").strip()
    historico  = body.get("historico", "").strip()
    numero_os  = body.get("numeroOS", "").strip()
    editor     = body.get("editor", "dashboard").strip()

    if not equipamento or not falha:
        return jsonify({"ok": False, "error": "equipamento e falha são obrigatórios"}), 400

    try:
        ws   = get_sheet()
        todos = ws.get_all_values()
    except Exception as e:
        log.error(f"[nova-ocorrencia] Erro ao abrir planilha: {e}")
        return jsonify({"ok": False, "error": f"Erro ao acessar planilha: {str(e)}"}), 500

    try:
        dados = {
            "cliente":      cliente,
            "usina":        usina,
            "equipamento":  equipamento,
            "falha":        falha,
            "causa":        causa,
            "equip_impact": equipamento,
            "acao":         acao,
            "status":       status,
            "os":           numero_os,
            "historico":    historico or f"{agora_br().strftime('%d/%m')} - Registro inicial via dashboard.",
        }
        gravar_nova_ocorrencia(ws, todos, dados)
        log.info(f"[nova-ocorrencia] {usina} — {equipamento} | editor={editor}")
        return jsonify({"ok": True}), 200
    except Exception as e:
        log.error(f"[nova-ocorrencia] Erro ao gravar: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

def _is_concluido_atividade(status):
    s = (status or "").lower()
    return any(x in s for x in ["concluído", "concluido", "resolvido", "fechado"])


def _proximo_id_atividade(todos):
    ids = []
    for row in todos[1:]:
        if row and str(row[0]).strip().isdigit():
            ids.append(int(str(row[0]).strip()))
    return str(max(ids) + 1) if ids else "1"


ATIV_HEADERS_JSON = ["id", "cliente", "usina", "equipamento", "descricao", "responsavel", "prazo",
                      "prioridade", "status", "dataCriacao", "dataConclusao", "historico", "editor",
                      "numeroOS", "statusOS", "observacoesOS", "linkOS", "statusTarefaOS", "etiquetasOS",
                      "anotacoesPessoais", "percentualOS", "statusGeralOS", "detalhesEquipamentosOS",
                      "ultimaVerificacaoOS", "visualizado"]

ATIV_CAMPO_COL = {
    "cliente": 2, "usina": 3, "equipamento": 4, "descricao": 5, "responsavel": 6,
    "prazo": 7, "prioridade": 8, "status": 9, "dataConclusao": 11, "historico": 12, "numeroOS": 14,
    "statusOS": 15, "observacoesOS": 16, "linkOS": 17, "statusTarefaOS": 18, "etiquetasOS": 19,
    "anotacoesPessoais": 20, "percentualOS": 21, "statusGeralOS": 22, "detalhesEquipamentosOS": 23,
    "ultimaVerificacaoOS": 24, "visualizado": 25,
}

ATIV_TOTAL_COLUNAS = 25

_ativ_headers_ensured = {"done": False}


def _garantir_headers_atividades(ws):
    """
    Garante que a aba Painel de Atividades tenha colunas suficientes na
    grade (a grade do Sheets tem um limite físico de colunas, separado do
    cabeçalho) e que o cabeçalho (linha 1) tenha as colunas novas.

    A expansão de colunas é tentada em TODA chamada (é uma checagem barata
    e idempotente — só chama a API se realmente precisar crescer), pra não
    ficar travada pra sempre caso uma tentativa anterior tenha falhado
    silenciosamente. Só o conteúdo do cabeçalho (linha 1) é cacheado por
    processo, já que isso sim é mais caro de checar toda hora.
    """
    try:
        if ws.col_count < ATIV_TOTAL_COLUNAS:
            ws.resize(cols=ATIV_TOTAL_COLUNAS)
            log.info(f"[Atividades] Grade expandida para {ATIV_TOTAL_COLUNAS} colunas")
    except Exception as e:
        log.error(f"[Atividades] Erro ao expandir colunas da grade: {e}")

    if _ativ_headers_ensured["done"]:
        return
    try:
        header = ws.row_values(1)
        extras = {15: "statusOS", 16: "observacoesOS", 17: "linkOS", 18: "statusTarefaOS",
                  19: "etiquetasOS", 20: "anotacoesPessoais", 21: "percentualOS",
                  22: "statusGeralOS", 23: "detalhesEquipamentosOS", 24: "ultimaVerificacaoOS",
                  25: "visualizado"}
        precisa = False
        visualizado_e_novo = (len(header) < 25 or header[24].strip() != "visualizado")
        for col, nome in extras.items():
            atual = header[col - 1] if len(header) >= col else ""
            if atual.strip() != nome:
                precisa = True
                break
        if precisa:
            novo_header = header + [""] * max(0, ATIV_TOTAL_COLUNAS - len(header))
            novo_header = novo_header[:ATIV_TOTAL_COLUNAS]
            for col, nome in extras.items():
                novo_header[col - 1] = nome
            ws.update(f"A1:{chr(64 + ATIV_TOTAL_COLUNAS)}1", [novo_header])
            log.info("[Atividades] Header estendido com todos os campos Fracttal")
        if visualizado_e_novo:
            # backfill: atividades JÁ existentes não devem aparecer como
            # "não lidas" quando essa funcionalidade é ligada pela primeira
            # vez — só atividades genuinamente novas (criadas depois disso)
            # devem nascer sem o marcador de "visualizado".
            try:
                total_linhas = len(ws.get_all_values())
                if total_linhas > 1:
                    coluna_letra = chr(64 + ATIV_CAMPO_COL["visualizado"])
                    valores = [["sim"]] * (total_linhas - 1)
                    ws.update(f"{coluna_letra}2:{coluna_letra}{total_linhas}", valores)
                    log.info(f"[Atividades] Backfill: {total_linhas - 1} atividades existentes marcadas como já visualizadas")
            except Exception as e:
                log.error(f"[Atividades] Erro no backfill de visualizado: {e}")
        _ativ_headers_ensured["done"] = True
    except Exception as e:
        log.error(f"[Atividades] Erro ao garantir conteúdo do header estendido: {e}")


@app.route("/disparar-comunicado-cluster", methods=["POST"])
def disparar_comunicado_cluster():
    """Dispara manualmente (via botão no dashboard) o comunicado de um
    cluster específico pro grupo de WhatsApp correspondente. O grupo é
    derivado das usinas do cluster (reaproveita o mapeamento grupo_usina
    já existente — normalmente todas as usinas de um mesmo cluster caem
    no mesmo grupo, já que representam a mesma equipe de campo).

    Sem exigência de WEBHOOK_SECRET aqui de propósito: é chamado direto do
    navegador pelo botão no dashboard (não tem como o frontend guardar o
    secret com segurança), então a única proteção é o próprio login no
    painel (role manager)."""
    if not WPP_SERVER_URL:
        return jsonify({"ok": False, "error": "WPP_SERVER_URL não configurado"}), 400

    dados = request.get_json(force=True, silent=True) or {}
    cluster = (dados.get("cluster") or "").strip()
    texto = (dados.get("texto") or "").strip()
    if not cluster or not texto:
        return jsonify({"ok": False, "error": "cluster e texto são obrigatórios"}), 400

    mapa_cluster_usina = _mapa_cluster_usina()
    mapa_grupo_usina = _mapa_grupo_usina()
    usinas_do_cluster = [u for u, c in mapa_cluster_usina.items() if c == cluster]
    grupo_id = next((mapa_grupo_usina[u] for u in usinas_do_cluster if u in mapa_grupo_usina), None)
    if not grupo_id:
        return jsonify({"ok": False, "error": (f"Nenhum grupo de WhatsApp configurado pras usinas do cluster "
                        f"\"{cluster}\". Configure em _Sistema: \"grupo_usina:<Usina>\" = \"<id>@g.us\".")}), 400

    try:
        r = requests.post(
            f"{WPP_SERVER_URL}/api/enviar-mensagem",
            json={"grupoId": grupo_id, "texto": texto},
            headers={"X-Webhook-Secret": WEBHOOK_SECRET} if WEBHOOK_SECRET else {},
            timeout=20,
        )
        if r.ok and r.json().get("ok"):
            return jsonify({"ok": True, "grupo": grupo_id}), 200
        return jsonify({"ok": False, "error": r.text[:300]}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _montar_texto_comunicado_zeladoria(cluster, usinas, semana):
    """Monta o texto do comunicado quinzenal pedindo as fotos de zeladoria
    (vegetação e sujidade) das usinas de um cluster."""
    lista_usinas = "\n".join(f"• {u}" for u in usinas)
    return (
        f"🌿 *Comunicado de Zeladoria — Semana {semana}*\n\n"
        f"Como de costume, precisamos das fotos de vegetação e sujidade dos módulos das usinas abaixo:\n\n"
        f"{lista_usinas}\n\n"
        f"📸 Padrão das fotos (por usina):\n"
        f"• 5 fotos de vegetação: próxima aos módulos, cabine primária, inversores e sala de O&M\n"
        f"• 3 fotos de sujidade: face do painel mostrando claramente a sujidade, com uma parte limpa ao lado pra comparação (usar pano com água)\n\n"
        f"Por favor, enviem aqui no grupo o quanto antes. Qualquer dúvida, me chamem."
    )


@app.route("/gerar-comunicado-zeladoria", methods=["GET"])
def gerar_comunicado_zeladoria():
    """Monta, por cluster/equipe, a lista de usinas e o texto do comunicado
    quinzenal pedindo as fotos de zeladoria (vegetação e sujidade).
    Reaproveita o mesmo mapeamento cluster/grupo usado no comunicado de
    Atividades (aba _Sistema: "cluster_usina:<Usina>" e "grupo_usina:
    <Usina>"), então os comunicados de Zeladoria saem pros mesmos grupos
    de WhatsApp das equipes."""
    mapa_cluster = _mapa_cluster_usina()  # usina -> cluster
    semana = agora_br().isocalendar()[1]

    por_cluster = {}
    for usina, cluster in mapa_cluster.items():
        por_cluster.setdefault(cluster, []).append(usina)

    resultado = []
    for cluster, usinas in sorted(por_cluster.items(), key=lambda kv: -len(kv[1])):
        usinas_ordenadas = sorted(usinas)
        texto = _montar_texto_comunicado_zeladoria(cluster, usinas_ordenadas, semana)
        resultado.append({"cluster": cluster, "usinas": usinas_ordenadas, "texto": texto})

    return jsonify({"ok": True, "semana": semana, "clusters": resultado}), 200


@app.route("/grupos-fotos-permitidos", methods=["GET"])
def grupos_fotos_permitidos():
    """Lista os IDs de grupo do WhatsApp que são canal de fotos de
    zeladoria — os mesmos grupos usados nos comunicados (mapeamento
    grupo_usina da aba _Sistema). O server.js consulta essa rota
    periodicamente pra saber de quais grupos deve capturar imagens;
    outros grupos monitorados (ex.: rondas/ocorrências) não devem ter
    fotos baixadas nem encaminhadas."""
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    grupos = sorted(set(_mapa_grupo_usina().values()))
    return jsonify({"ok": True, "grupos": grupos}), 200


@app.route("/webhook-foto-zeladoria", methods=["POST"])
def webhook_foto_zeladoria():
    """Recebe uma foto individual encaminhada pelo server.js (mensagem de
    imagem num grupo monitorado). Só salva o arquivo e registra uma linha
    crua na fila — a classificação por IA roda depois, em lote, via
    /zeladoria-processar-fotos-pendentes. Isso evita segurar o webhook
    esperando a IA responder e permite juntar várias fotos da mesma leva
    antes de classificar em conjunto."""
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = request.get_json(force=True, silent=True) or {}
    grupo_id = (payload.get("grupoId") or "").strip()
    imagem_b64 = payload.get("imagemBase64") or ""
    mime_type = payload.get("mimeType") or "image/jpeg"
    legenda = (payload.get("legenda") or "").strip()

    if not grupo_id or not imagem_b64:
        return jsonify({"ok": False, "error": "grupoId e imagemBase64 são obrigatórios"}), 400

    # Só aceita fotos dos grupos usados nos comunicados (mesmo mapeamento
    # grupo_usina da aba _Sistema) — outros grupos monitorados (ex.:
    # grupos de ronda/ocorrência que não são canal de fotos de zeladoria)
    # são ignorados aqui, mesmo que o server.js encaminhe por engano.
    grupos_permitidos = set(_mapa_grupo_usina().values())
    if grupo_id not in grupos_permitidos:
        return jsonify({"ok": True, "ignorado": True, "motivo": "grupo não é canal de fotos de zeladoria"}), 200

    try:
        bruto = base64.b64decode(imagem_b64)
    except Exception:
        return jsonify({"ok": False, "error": "imagemBase64 inválida"}), 400

    agora = agora_br()
    semana = _semana_iso(agora)
    ext = "png" if "png" in mime_type else "jpg"
    nome_arquivo = f"{semana}_{uuid.uuid4().hex[:10]}.{ext}"
    pasta_semana = os.path.join(ZELADORIA_FOTOS_DIR, semana)
    os.makedirs(pasta_semana, exist_ok=True)
    try:
        with open(os.path.join(pasta_semana, nome_arquivo), "wb") as f:
            f.write(bruto)
    except Exception as e:
        log.error(f"[webhook-foto-zeladoria] Erro ao salvar arquivo: {e}")
        return jsonify({"ok": False, "error": "falha ao salvar arquivo no servidor"}), 500

    try:
        ws = get_zeladoria_fotos_raw_sheet()
        novo_id = str(uuid.uuid4())[:8]
        _gspread_retry(lambda: ws.append_row([
            novo_id, grupo_id, semana, agora.strftime("%d/%m/%Y %H:%M:%S"),
            legenda, f"{semana}/{nome_arquivo}", "nao",
        ]))
    except Exception as e:
        log.error(f"[webhook-foto-zeladoria] Erro ao gravar na planilha: {e}")
        return jsonify({"ok": False, "error": "falha ao registrar na planilha"}), 500

    return jsonify({"ok": True, "id": novo_id, "semana": semana}), 200


@app.route("/zeladoria_fotos/<path:filename>")
def servir_foto_zeladoria(filename):
    return send_from_directory(ZELADORIA_FOTOS_DIR, filename)


def _montar_prompt_classificacao_zeladoria(fotos_info, candidatas_usinas, cluster, dia_semana, dica_agenda):
    candidatas_str = ", ".join(candidatas_usinas) if candidatas_usinas else "não identificado"
    dica = f"\nDica de agenda conhecida: {dica_agenda}" if dica_agenda else ""
    legendas = "\n".join(
        f"- Foto {i + 1}: legenda = \"{f.get('legenda') or '(sem legenda)'}\""
        for i, f in enumerate(fotos_info)
    )
    return f"""Você é um assistente de O&M de usinas solares fotovoltaicas. Vai analisar um lote de fotos enviadas por uma equipe de campo no WhatsApp, referentes à zeladoria quinzenal (vegetação ao redor da usina e sujidade na face dos módulos). O lote pode conter fotos de MAIS DE UMA usina misturadas — separe corretamente cada foto pra sua usina, foto por foto.

CONTEXTO:
- Cluster/equipe responsável pelo grupo: {cluster or 'não identificado'}
- Usina(s) candidata(s) que esse grupo de WhatsApp costuma atender: {candidatas_str}
- Dia da semana em que as fotos foram enviadas: {dia_semana}{dica}
- Padrão esperado por usina: 5 fotos de vegetação + 3 fotos de sujidade (mas pode vir diferente na prática — não invente fotos que não existem).
- Legendas escritas pela equipe (se houver):
{legendas}

IDENTIFICAÇÃO DA USINA — SINAL PRIMÁRIO (leia isso com atenção antes de tudo):
As fotos são tiradas com um app de georreferenciamento (tipo "Timemark") que grava uma marca d'água NA PRÓPRIA IMAGEM com data/hora, endereço completo, cidade/UF e coordenadas (lat/long). Essa marca d'água é a fonte MAIS CONFIÁVEL de localização — sempre leia e priorize esse texto antes de qualquer outra pista.
- Muitas usinas de Fred têm o mesmo nome do município onde ficam (ex.: a usina "Araputanga" fica na cidade de Araputanga-MT, a usina "Nobres" fica em Nobres-MT). Ao ler a cidade/município na marca d'água, compare esse texto diretamente com o nome de cada usina candidata — bate na maioria dos casos.
- Ordem de prioridade das evidências: 1º) cidade/endereço/coordenadas lidos na marca d'água da própria foto — 2º) legenda escrita pela equipe — 3º) dica de agenda conhecida (só usa se as duas primeiras não bastarem).
- Se a marca d'água não estiver visível ou legível em alguma foto, tente pelas outras evidências, mas marque confiança mais baixa.

CLASSIFICAÇÃO DE GRAU/NÍVEL — escalas oficiais de Fred (use com critério técnico, olhando a foto de verdade, não chute):
Sujidade (só se classificacao="sujidade"):
  L = Limpeza em execução (dá pra ver alguém limpando ou equipamento de limpeza em uso)
  1 = Leve (poeira fina, quase imperceptível)
  2 = Moderada (sujidade visível mas ainda não compromete muito a geração)
  3 = Elevada (camada de sujidade clara cobrindo boa parte do painel)
  4 = Severa (sujidade pesada, escurecendo visivelmente os módulos)
  5 = Crítica (módulo muito sujo, sujidade grossa/acumulada, tipo terra/lama seca)
Vegetação (só se classificacao="vegetacao"):
  R = Roçagem em andamento (dá pra ver equipe/máquina roçando)
  1 = Muito baixa (grama bem baixa, controlada, sem contato com estrutura)
  2 = Baixa (atenção leve, grama um pouco mais alta mas longe dos módulos)
  3 = Média (alerta operacional, vegetação já se aproximando da estrutura/trackers)
  4 = Alta (crítica, vegetação encostando ou próxima de tocar os módulos/equipamentos)
  5 = Muito alta (emergencial, vegetação tocando/invadindo módulos, cabine ou inversores)

TAREFA — pra CADA foto, na ordem enviada, determine:
1. Tipo: "sujidade" (foto de perto da face de um módulo, mostrando poeira/sujeira), "vegetacao" (vegetação/mato ao redor dos módulos, cabine primária, inversores ou sala de O&M) ou "indefinido" (não dá pra classificar com confiança).
2. Usina: qual das candidatas acima essa foto pertence, seguindo a ordem de prioridade de evidências acima. Se só existir 1 candidata, use-a pra todas as fotos. Se não conseguir determinar com nenhuma evidência, deixe em branco (não chute às cegas).
3. Confiança dessa atribuição de usina pra essa foto: "alta" (cidade/endereço da marca d'água bate claramente com uma candidata), "media" (legenda ou outros indícios razoáveis, sem confirmação clara da marca d'água) ou "baixa" (chute entre candidatas sem evidência clara).
4. Grau: usando a escala correspondente ao tipo da foto (sujidade ou vegetação) acima. Deixe vazio se o tipo for "indefinido".

Responda APENAS em JSON estrito, neste formato exato (um item por foto, na MESMA ORDEM em que as fotos foram enviadas):
{{
  "fotos": [
    {{"classificacao": "sujidade", "usina": "nome da usina ou vazio", "confianca": "alta|media|baixa", "cidade_lida": "cidade/endereço lido na marca d'água, ou vazio se não visível", "grau": "L|1|2|3|4|5|R|vazio"}}
  ],
  "justificativa_geral": "1-2 frases explicando como você separou as fotos entre as usinas, citando a cidade/endereço lido quando relevante"
}}"""


def _agregar_grau_zeladoria(graus, letra_especial):
    """Combina os graus individuais das fotos de um mesmo tipo (sujidade
    ou vegetação) num grau só pra usina — usa a letra especial (L/R) se
    QUALQUER foto indicar limpeza/roçagem em andamento (é a informação
    mais acionável), senão usa o PIOR (maior) grau numérico encontrado,
    já que o card deve refletir o cenário mais urgente visto nas fotos."""
    graus = [g for g in graus if g]
    if not graus:
        return ""
    if letra_especial in graus:
        return letra_especial
    numericos = [int(g) for g in graus if g.isdigit()]
    if not numericos:
        return ""
    return str(max(numericos))


def _processar_lote_fotos_zeladoria(grupo_id, fotos_raw):
    """fotos_raw: lista de dicts {id, legenda, arquivo, recebidoEm} da
    mesma leva (mesmo grupo, ainda não processados). Retorna uma LISTA de
    resultados — um por usina identificada dentro do lote, já que um
    mesmo grupo de WhatsApp pode cobrir mais de uma usina (ex.: equipe
    que atende GD Energy e Alves Lima no mesmo grupo). As fotos são
    classificadas e atribuídas à usina foto a foto, depois agrupadas."""
    mapa_grupo_usina = _mapa_grupo_usina()
    mapa_cluster_usina = _mapa_cluster_usina()
    candidatas = sorted({u for u, g in mapa_grupo_usina.items() if g == grupo_id})
    cluster_padrao = next((mapa_cluster_usina.get(u) for u in candidatas if mapa_cluster_usina.get(u)), None)

    dica_agenda = None
    for chave, texto in _AGENDA_EQUIPES_CONHECIDA.items():
        if any(chave.lower() in u.lower() for u in candidatas):
            dica_agenda = texto
            break

    try:
        dt_ref = datetime.strptime(fotos_raw[0]["recebidoEm"], "%d/%m/%Y %H:%M:%S")
    except Exception:
        dt_ref = agora_br()
    dia_semana = _dia_semana_pt(dt_ref)

    parts = [{"text": _montar_prompt_classificacao_zeladoria(fotos_raw, candidatas, cluster_padrao, dia_semana, dica_agenda)}]
    fotos_validas = []
    for foto in fotos_raw:
        caminho_completo = os.path.join(ZELADORIA_FOTOS_DIR, foto["arquivo"])
        if not os.path.exists(caminho_completo):
            continue
        with open(caminho_completo, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        mime = "image/png" if foto["arquivo"].lower().endswith(".png") else "image/jpeg"
        parts.append({"inline_data": {"mime_type": mime, "data": img_b64}})
        fotos_validas.append(foto)

    if not fotos_validas:
        raise RuntimeError("nenhum arquivo de foto encontrado no disco pra essa leva")

    resp = _chamar_gemini_com_retry(
        {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.15,
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 0},
            },
        },
        timeout=60,
    )
    data = resp.json()
    texto = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    texto_limpo = re.sub(r"^```json\s*|\s*```$", "", texto)
    resultado = json.loads(texto_limpo)

    fotos_ia = resultado.get("fotos", [])
    justificativa_geral = resultado.get("justificativa_geral", "")

    # casa o nome de usina devolvido pela IA (pode vir sem acento/variado)
    # com o nome canônico das candidatas, usando o normalizador já
    # existente no sistema (sem acento, minúsculo, só alfanum)
    candidatas_norm = {_norm(u): u for u in candidatas}

    ordem_confianca = {"alta": 3, "media": 2, "baixa": 1}
    grupos_por_usina = {}  # usina canônica ("" = não identificada) -> agregados
    for i, foto in enumerate(fotos_validas):
        item_ia = fotos_ia[i] if i < len(fotos_ia) else {}
        classificacao = item_ia.get("classificacao", "indefinido")
        usina_bruta = (item_ia.get("usina") or "").strip()
        confianca = item_ia.get("confianca", "baixa")
        cidade_lida = (item_ia.get("cidade_lida") or "").strip()
        grau = (item_ia.get("grau") or "").strip().upper()

        if len(candidatas) == 1:
            usina_final = candidatas[0]
        else:
            usina_final = candidatas_norm.get(_norm(usina_bruta), usina_bruta)

        chave = usina_final
        if chave not in grupos_por_usina:
            grupos_por_usina[chave] = {
                "fotos": [], "sujidade": 0, "vegetacao": 0, "indefinido": 0,
                "confiancas": [], "cidades": [], "graus_sujidade": [], "graus_vegetacao": [],
            }
        g = grupos_por_usina[chave]
        g["fotos"].append(foto)
        g["confiancas"].append(confianca)
        if cidade_lida and cidade_lida not in g["cidades"]:
            g["cidades"].append(cidade_lida)
        if classificacao == "sujidade":
            g["sujidade"] += 1
            if grau:
                g["graus_sujidade"].append(grau)
        elif classificacao == "vegetacao":
            g["vegetacao"] += 1
            if grau:
                g["graus_vegetacao"].append(grau)
        else:
            g["indefinido"] += 1

    resultados = []
    for usina, g in grupos_por_usina.items():
        # confiança do lote = a mais baixa entre as fotos atribuídas a essa usina (conservador)
        confianca_lote = min(g["confiancas"], key=lambda c: ordem_confianca.get(c, 0)) if g["confiancas"] else "baixa"
        justificativa = justificativa_geral
        if g["cidades"]:
            justificativa = (justificativa + f" · Local lido na foto: {', '.join(g['cidades'])}").strip(" ·")
        resultados.append({
            "usina": usina,
            "cluster": mapa_cluster_usina.get(usina, cluster_padrao or ""),
            "fotos": g["fotos"],
            "confianca_ia": confianca_lote,
            "justificativa_ia": justificativa,
            "qtd_sujidade": g["sujidade"],
            "qtd_vegetacao": g["vegetacao"],
            "qtd_indefinido": g["indefinido"],
            "grau_sujidade_ia": _agregar_grau_zeladoria(g["graus_sujidade"], "L"),
            "grau_vegetacao_ia": _agregar_grau_zeladoria(g["graus_vegetacao"], "R"),
        })
    return resultados


@app.route("/zeladoria-processar-fotos-pendentes", methods=["POST", "GET"])
def zeladoria_processar_fotos_pendentes():
    """Agrupa as fotos cruas ainda não processadas por grupo de WhatsApp
    (tudo que ainda está pendente do mesmo grupo vira 1 ou mais levas) e
    manda cada leva pra classificação por IA (Gemini Vision), gravando um
    resumo em _ZeladoriaFotos. Chamado sob demanda (botão no painel de
    Zeladoria) — não é automático, pra Fred controlar quando gastar cota
    da IA e evitar rodar em cima de webhooks concorrentes.

    Processa só um número limitado de levas por chamada (cada leva também
    limitada em quantidade de fotos) — um volume grande de fotos
    pendentes (ex.: acúmulo de vários dias) levava o Gemini Vision a
    demorar demais numa única requisição e estourava o timeout do
    Gunicorn/Caddy, devolvendo uma página de erro HTML em vez de JSON pro
    frontend. O frontend chama essa rota em rounds sucessivos (mesmo
    padrão do "Atualizar OS") até `lotesRestantes` zerar."""
    if not GEMINI_API_KEY:
        return jsonify({"ok": False, "error": "GEMINI_API_KEY não configurada no servidor"}), 500

    MAX_FOTOS_POR_LOTE = 10
    MAX_LOTES_POR_CHAMADA = 2

    ws_raw = get_zeladoria_fotos_raw_sheet()
    linhas = _gspread_retry(lambda: ws_raw.get_all_values())
    pendentes = []
    for idx, row in enumerate(linhas[1:], start=2):
        if len(row) >= 7 and row[6].strip().lower() != "sim":
            pendentes.append({
                "row": idx, "id": row[0], "grupoId": row[1], "semana": row[2],
                "recebidoEm": row[3], "legenda": row[4], "arquivo": row[5],
            })

    if not pendentes:
        return jsonify({"ok": True, "processados": 0, "lotes": 0, "lotesRestantes": 0}), 200

    # agrupa por grupo (ordem de chegada preservada) e quebra cada grupo
    # em pedaços de no máximo MAX_FOTOS_POR_LOTE fotos, pra manter cada
    # chamada ao Gemini rápida e o payload pequeno
    por_grupo = {}
    for f in pendentes:
        por_grupo.setdefault(f["grupoId"], []).append(f)

    todos_lotes = []  # lista de (grupo_id, [fotos])
    for grupo_id, fotos in por_grupo.items():
        for i in range(0, len(fotos), MAX_FOTOS_POR_LOTE):
            todos_lotes.append((grupo_id, fotos[i:i + MAX_FOTOS_POR_LOTE]))

    lotes_desta_chamada = todos_lotes[:MAX_LOTES_POR_CHAMADA]
    lotes_restantes = max(0, len(todos_lotes) - len(lotes_desta_chamada))

    ws_resumo = get_zeladoria_fotos_sheet()
    processados = 0
    linhas_criadas = 0
    erros = []
    novas_linhas_resumo = []
    atualizacoes_processado = []  # (row, "sim") — marcadas como processado em lote no final
    for grupo_id, fotos in lotes_desta_chamada:
        semana = fotos[0]["semana"]
        try:
            resultados_por_usina = _processar_lote_fotos_zeladoria(grupo_id, fotos)
            agora_str = agora_br().strftime("%d/%m/%Y %H:%M:%S")
            for r in resultados_por_usina:
                arquivos_str = "|".join(f["arquivo"] for f in r["fotos"])
                novo_id = str(uuid.uuid4())[:8]
                novas_linhas_resumo.append([
                    novo_id, semana, grupo_id, r["cluster"],
                    r["usina"], r["confianca_ia"],
                    r["justificativa_ia"], "",  # usinaConfirmada — em branco até Fred confirmar
                    r["qtd_sujidade"], r["qtd_vegetacao"], r["qtd_indefinido"],
                    arquivos_str, "nao", agora_str, agora_str, "",  # descartado
                    r["grau_sujidade_ia"], r["grau_vegetacao_ia"],
                ])
                linhas_criadas += 1
            for f in fotos:
                atualizacoes_processado.append({
                    "range": gspread.utils.rowcol_to_a1(f["row"], 7),
                    "values": [["sim"]],
                })
            processados += len(fotos)
        except Exception as e:
            log.error(f"[zeladoria-processar-fotos] Erro no lote grupo={grupo_id} semana={semana}: {e}")
            erros.append(f"{grupo_id}/{semana}: {e}")

    # escreve tudo de uma vez (1-2 chamadas em vez de uma por usina/foto) —
    # é o maior fator que estava estourando a cota de escrita do Sheets
    if novas_linhas_resumo:
        _gspread_retry(lambda: ws_resumo.append_rows(novas_linhas_resumo, value_input_option="RAW"))
    if atualizacoes_processado:
        _gspread_retry(lambda: ws_raw.batch_update(atualizacoes_processado, value_input_option="RAW"))

    return jsonify({
        "ok": True, "processados": processados, "lotes": linhas_criadas,
        "lotesRestantes": lotes_restantes, "erros": erros,
    }), 200


@app.route("/zeladoria-fotos-status", methods=["GET"])
def zeladoria_fotos_status():
    """Retorna o resumo de fotos de zeladoria da semana pedida (ou da
    semana ISO atual, por padrão), pro painel de Zeladoria mostrar o
    controle de fotos recebidas. Por padrão só mostra o que ainda está
    pendente de revisão — lotes descartados e lotes já CONFIRMADOS (que
    já alimentaram a tabela de graus e viraram só arquivo/histórico) não
    entram, a menos que ?mostrarConfirmadas=1 seja passado."""
    semana = request.args.get("semana") or _semana_iso(agora_br())
    mostrar_confirmadas = request.args.get("mostrarConfirmadas", "").lower() in ("1", "true", "sim")

    ws = get_zeladoria_fotos_sheet()
    linhas = _gspread_retry(lambda: ws.get_all_values())
    resultado = []
    for row in linhas[1:]:
        if len(row) < len(ZELADORIA_FOTOS_HEADERS):
            row = row + [""] * (len(ZELADORIA_FOTOS_HEADERS) - len(row))
        if row[1].strip() != semana:
            continue
        if row[15].strip().lower() == "sim":  # descartado
            continue
        confirmado = row[12].strip().lower() == "sim"
        if confirmado and not mostrar_confirmadas:
            continue
        arquivos = [a for a in row[11].split("|") if a]
        resultado.append({
            "id": row[0], "semana": row[1], "grupoId": row[2], "cluster": row[3],
            "usinaCandidataIA": row[4], "confiancaIA": row[5], "justificativaIA": row[6],
            "usinaConfirmada": row[7],
            "qtdSujidade": row[8], "qtdVegetacao": row[9], "qtdIndefinido": row[10],
            "fotos": [f"/zeladoria_fotos/{a}" for a in arquivos],
            "confirmadoFred": confirmado,
            "criadoEm": row[13], "atualizadoEm": row[14],
            "grauSujidadeIA": row[16], "grauVegetacaoIA": row[17],
        })

    # também informa quantas fotos cruas ainda estão na fila sem processar
    ws_raw = get_zeladoria_fotos_raw_sheet()
    linhas_raw = _gspread_retry(lambda: ws_raw.get_all_values())
    pendentes = sum(1 for row in linhas_raw[1:] if len(row) >= 7 and row[6].strip().lower() != "sim")

    return jsonify({"ok": True, "semana": semana, "lotes": resultado, "fotosPendentesProcessar": pendentes}), 200


@app.route("/zeladoria-fotos-confirmar", methods=["POST"])
def zeladoria_fotos_confirmar():
    """Fred confirma o recebimento de um lote de fotos de zeladoria e,
    se necessário, corrige manualmente a usina atribuída pela IA. Ao
    confirmar: os graus de sujidade/vegetação que a IA leu nas fotos
    alimentam automaticamente o Controle de Sujidade e Vegetação da
    usina (semana atual), e o lote sai da tela principal (arquivado —
    os arquivos continuam guardados no servidor, só não ficam mais na
    fila de revisão)."""
    payload = request.get_json(force=True, silent=True) or {}
    lote_id = (payload.get("id") or "").strip()
    usina_confirmada = (payload.get("usinaConfirmada") or "").strip()
    if not lote_id:
        return jsonify({"ok": False, "error": "id é obrigatório"}), 400

    ws = get_zeladoria_fotos_sheet()
    linhas = _gspread_retry(lambda: ws.get_all_values())
    for idx, row in enumerate(linhas[1:], start=2):
        if row and row[0].strip() == lote_id:
            if len(row) < len(ZELADORIA_FOTOS_HEADERS):
                row = row + [""] * (len(ZELADORIA_FOTOS_HEADERS) - len(row))
            agora_str = agora_br().strftime("%d/%m/%Y %H:%M:%S")
            usina_confirmada_final = usina_confirmada or row[7]
            _gspread_retry(lambda: ws.update(f"H{idx}:O{idx}", [[
                usina_confirmada_final, row[8], row[9], row[10], row[11], "sim", row[13], agora_str,
            ]]))

            usina_final = usina_confirmada or row[4]  # usinaConfirmada (se veio) ou usinaCandidataIA
            semana = row[1]
            grau_sujidade_ia = row[16].strip() if len(row) > 16 else ""
            grau_vegetacao_ia = row[17].strip() if len(row) > 17 else ""
            if usina_final:
                try:
                    if grau_sujidade_ia:
                        _upsert_grau_zeladoria(
                            usina_final, "", "sujidade", grau_sujidade_ia,
                            f"Preenchido automaticamente pelas fotos confirmadas (lote {lote_id})",
                            "IA (fotos confirmadas)", semana,
                        )
                    if grau_vegetacao_ia:
                        _upsert_grau_zeladoria(
                            usina_final, "", "vegetacao", grau_vegetacao_ia,
                            f"Preenchido automaticamente pelas fotos confirmadas (lote {lote_id})",
                            "IA (fotos confirmadas)", semana,
                        )
                except Exception as e:
                    log.error(f"[zeladoria-fotos-confirmar] Falha ao alimentar tabela de graus pra {usina_final}: {e}")

            return jsonify({
                "ok": True,
                "grauSujidadeAplicado": grau_sujidade_ia or None,
                "grauVegetacaoAplicado": grau_vegetacao_ia or None,
            }), 200

    return jsonify({"ok": False, "error": "lote não encontrado"}), 404


@app.route("/zeladoria-fotos-reclassificar-grau", methods=["POST"])
def zeladoria_fotos_reclassificar_grau():
    """Corrige lotes que já tinham sido confirmados ANTES da classificação
    de grau (sujidade/vegetação) existir no sistema — essas levas ficaram
    sem grauSujidadeIA/grauVegetacaoIA e, por já estarem confirmadas,
    somem da tela principal sem nunca terem alimentado o Controle de
    Sujidade e Vegetação. Reroda a IA sobre os arquivos já salvos em
    disco (sem mexer na usina/confirmação, que já estão corretas), grava
    o grau na linha e alimenta a tabela, do mesmo jeito que o fluxo
    normal de confirmação já faz. Processa em rounds limitados (mesmo
    motivo do /zeladoria-processar-fotos-pendentes: evitar timeout)."""
    if not GEMINI_API_KEY:
        return jsonify({"ok": False, "error": "GEMINI_API_KEY não configurada no servidor"}), 500

    payload = request.get_json(force=True, silent=True) or {}
    lote_id_unico = (payload.get("id") or "").strip()
    MAX_LOTES_POR_CHAMADA = 2

    ws = get_zeladoria_fotos_sheet()
    linhas = _gspread_retry(lambda: ws.get_all_values())
    candidatos = []
    for idx, row in enumerate(linhas[1:], start=2):
        if len(row) < len(ZELADORIA_FOTOS_HEADERS):
            row = row + [""] * (len(ZELADORIA_FOTOS_HEADERS) - len(row))
        if row[15].strip().lower() == "sim":  # descartado — ignora
            continue
        if row[12].strip().lower() != "sim":  # só os já confirmados
            continue
        if row[16].strip() or row[17].strip():  # já tem grau — não precisa
            continue
        if lote_id_unico and row[0].strip() != lote_id_unico:
            continue
        candidatos.append((idx, row))

    if not candidatos:
        return jsonify({"ok": True, "processados": 0, "restantes": 0, "erros": []}), 200

    lote_desta_chamada = candidatos[:MAX_LOTES_POR_CHAMADA]
    restantes = max(0, len(candidatos) - len(lote_desta_chamada))

    processados = 0
    erros = []
    for idx, row in lote_desta_chamada:
        lote_id = row[0]
        grupo_id = row[2]
        usina_final = (row[7] or row[4]).strip()  # usinaConfirmada ou usinaCandidataIA
        semana = row[1]
        arquivos = [a for a in row[11].split("|") if a]
        if not usina_final or not arquivos:
            erros.append(f"{lote_id}: sem usina confirmada ou sem arquivos salvos")
            continue

        recebido_em = row[13] or agora_br().strftime("%d/%m/%Y %H:%M:%S")
        fotos_raw = [{"id": lote_id, "legenda": "", "arquivo": a, "recebidoEm": recebido_em} for a in arquivos]
        try:
            resultados_por_usina = _processar_lote_fotos_zeladoria(grupo_id, fotos_raw)
            escolhido = next((r for r in resultados_por_usina if _norm(r["usina"]) == _norm(usina_final)), None)
            if not escolhido and resultados_por_usina:
                escolhido = max(resultados_por_usina, key=lambda r: len(r["fotos"]))
            if not escolhido:
                erros.append(f"{lote_id}: IA não retornou classificação")
                continue

            grau_sujidade_ia = escolhido["grau_sujidade_ia"]
            grau_vegetacao_ia = escolhido["grau_vegetacao_ia"]
            _gspread_retry(lambda: ws.update(f"Q{idx}:R{idx}", [[grau_sujidade_ia, grau_vegetacao_ia]]))

            if grau_sujidade_ia:
                _upsert_grau_zeladoria(
                    usina_final, "", "sujidade", grau_sujidade_ia,
                    f"Preenchido automaticamente (reclassificação — lote {lote_id})",
                    "IA (fotos confirmadas)", semana,
                )
            if grau_vegetacao_ia:
                _upsert_grau_zeladoria(
                    usina_final, "", "vegetacao", grau_vegetacao_ia,
                    f"Preenchido automaticamente (reclassificação — lote {lote_id})",
                    "IA (fotos confirmadas)", semana,
                )
            processados += 1
        except Exception as e:
            log.error(f"[zeladoria-fotos-reclassificar-grau] Erro no lote {lote_id}: {e}")
            erros.append(f"{lote_id}: {e}")

    return jsonify({"ok": True, "processados": processados, "restantes": restantes, "erros": erros}), 200


@app.route("/zeladoria-fotos-descartar", methods=["POST"])
def zeladoria_fotos_descartar():
    """Descarta um lote de fotos capturado erroneamente (ex.: veio de um
    grupo/usina errada, ou não são fotos de zeladoria de verdade). Não
    apaga a linha — só marca como descartado, pra manter histórico —,
    e o lote some da tela (/zeladoria-fotos-status já filtra isso)."""
    payload = request.get_json(force=True, silent=True) or {}
    lote_id = (payload.get("id") or "").strip()
    if not lote_id:
        return jsonify({"ok": False, "error": "id é obrigatório"}), 400

    ws = get_zeladoria_fotos_sheet()
    linhas = _gspread_retry(lambda: ws.get_all_values())
    for idx, row in enumerate(linhas[1:], start=2):
        if row and row[0].strip() == lote_id:
            agora_str = agora_br().strftime("%d/%m/%Y %H:%M:%S")
            _gspread_retry(lambda: ws.update(f"O{idx}:P{idx}", [[agora_str, "sim"]]))  # atualizadoEm, descartado
            return jsonify({"ok": True}), 200

    return jsonify({"ok": False, "error": "lote não encontrado"}), 404



VALORES_VALIDOS_SUJIDADE = {"L", "1", "2", "3", "4", "5"}
VALORES_VALIDOS_VEGETACAO = {"R", "1", "2", "3", "4", "5"}


@app.route("/zeladoria-graus", methods=["GET"])
def zeladoria_graus_listar():
    """Retorna, pra cada usina de Fred (mapeadas em cluster_usina), o
    último grau registrado de sujidade e de vegetação — pro Controle de
    Zeladoria no painel. Escala igual à das planilhas STATUS_CONSOLIDADO."""
    mapa_cluster = _mapa_cluster_usina()

    ws = get_zeladoria_graus_sheet()
    linhas = _gspread_retry(lambda: ws.get_all_values())

    # último registro por (usina, tipo) — a planilha guarda um histórico
    # de todas as quinzenas, aqui só queremos o mais recente de cada
    ultimos = {}
    for row in linhas[1:]:
        if len(row) < len(ZELADORIA_GRAUS_HEADERS):
            row = row + [""] * (len(ZELADORIA_GRAUS_HEADERS) - len(row))
        usina, cliente, tipo, semana, grau, obs, editor, atualizado = row[:8]
        if not usina or not tipo:
            continue
        chave = (usina.strip(), tipo.strip())
        # get_all_values já vem em ordem de inserção — a última linha
        # lida pra essa chave é a mais recente
        ultimos[chave] = {"semana": semana, "grau": grau, "observacoes": obs, "atualizadoEm": atualizado}

    resultado = []
    for usina, cluster in sorted(mapa_cluster.items()):
        sujidade = ultimos.get((usina, "sujidade"), {})
        vegetacao = ultimos.get((usina, "vegetacao"), {})
        resultado.append({
            "usina": usina,
            "cluster": cluster,
            "sujidade": sujidade,
            "vegetacao": vegetacao,
        })

    return jsonify({"ok": True, "semanaAtual": _semana_iso(agora_br()), "usinas": resultado}), 200


def _upsert_grau_zeladoria(usina, cliente, tipo, grau, observacoes, editor, semana=None):
    """Grava (ou atualiza) o grau de sujidade/vegetação de uma usina pra
    uma semana — um registro por (usina, tipo, semana). Reaproveitado
    tanto pelo endpoint manual (Fred editando a tabela direto) quanto
    pelo preenchimento automático quando um lote de fotos é confirmado."""
    semana = semana or _semana_iso(agora_br())
    ws = get_zeladoria_graus_sheet()
    linhas = _gspread_retry(lambda: ws.get_all_values())
    linha_alvo = None
    for idx, row in enumerate(linhas[1:], start=2):
        if len(row) >= 4 and row[0].strip() == usina and row[2].strip() == tipo and row[3].strip() == semana:
            linha_alvo = idx
            break

    agora_str = agora_br().strftime("%d/%m/%Y %H:%M:%S")
    if linha_alvo:
        _gspread_retry(lambda: ws.update(f"A{linha_alvo}:H{linha_alvo}", [[usina, cliente, tipo, semana, grau, observacoes, editor, agora_str]]))
    else:
        _gspread_retry(lambda: ws.append_row([usina, cliente, tipo, semana, grau, observacoes, editor, agora_str]))


@app.route("/zeladoria-grau-atualizar", methods=["POST"])
def zeladoria_grau_atualizar():
    """Registra o grau de sujidade ou vegetação de uma usina na semana
    atual (ou na semana informada). Um registro por (usina, tipo, semana)
    — se já existir, atualiza; senão, cria uma linha nova, preservando o
    histórico das quinzenas anteriores."""
    payload = request.get_json(force=True, silent=True) or {}
    usina = (payload.get("usina") or "").strip()
    tipo = (payload.get("tipo") or "").strip().lower()
    grau = (payload.get("grau") or "").strip().upper()
    observacoes = (payload.get("observacoes") or "").strip()
    semana = (payload.get("semana") or "").strip() or _semana_iso(agora_br())

    if not usina or tipo not in ("sujidade", "vegetacao"):
        return jsonify({"ok": False, "error": "usina e tipo ('sujidade' ou 'vegetacao') são obrigatórios"}), 400

    valores_validos = VALORES_VALIDOS_SUJIDADE if tipo == "sujidade" else VALORES_VALIDOS_VEGETACAO
    if grau and grau not in valores_validos:
        return jsonify({"ok": False, "error": f"grau inválido pra {tipo}. Use: {', '.join(sorted(valores_validos))}"}), 400

    cliente = (payload.get("cliente") or "").strip()
    _upsert_grau_zeladoria(usina, cliente, tipo, grau, observacoes, "Fred", semana)

    return jsonify({"ok": True}), 200


@app.route("/resolver-duplicata-8866", methods=["POST"])
def resolver_duplicata_8866():
    """Uso único: resolve a duplicidade da OS 8866 (atividades #24 e #35
    apontando pra mesma OS). Mantém #24 (vinculada pelo fluxo oficial
    Solicitar OS), cancela #35 (criada manualmente à parte), preservando
    o histórico das duas com uma nota cruzada explicando o motivo."""
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    ws = get_atividades_sheet()
    todos = ws.get_all_values()
    agora = agora_br().strftime('%d/%m/%Y %H:%M')
    resultado = {}
    for row_idx, row in enumerate(todos[1:], start=2):
        if not row or not row[0].strip():
            continue
        id_ativ = row[0].strip()
        if id_ativ == "24":
            nota = f"{agora} - Atividade #35 (duplicata manual da mesma OS) foi cancelada e mesclada aqui."
            hist_atual = row[11] if len(row) > 11 else ""
            ws.update_cell(row_idx, 12, f"{hist_atual}\n{nota}".strip())
            resultado["24"] = "nota adicionada"
        elif id_ativ == "35":
            nota = f"{agora} - Cancelada: duplicata da atividade #24 pra mesma OS (8866). Mantida #24, vinculada pelo fluxo oficial Solicitar OS."
            hist_atual = row[11] if len(row) > 11 else ""
            ws.update_cell(row_idx, 12, f"{hist_atual}\n{nota}".strip())
            ws.update_cell(row_idx, 9, "Cancelado")
            resultado["35"] = "cancelada"

    return jsonify({"ok": True, "resultado": resultado}), 200


def _fracttal_verificar_e_atualizar_uma_os(ws, i, row, numero_os, enviar_notificacao=True):
    """Consulta a Fracttal AO VIVO pra uma única OS (linha i da planilha) e
    atualiza todos os campos derivados (statusOS, percentualOS,
    statusGeralOS, statusTarefaOS, detalhesEquipamentosOS) + aplica a
    correção de status interno via _status_interno_esperado(). Usada tanto
    pelo rodízio automático quanto pela auditoria completa — única função
    que efetivamente fala com a Fracttal pra revalidar uma OS, pra nunca
    ter dois lugares checando/decidindo isso de formas diferentes.

    enviar_notificacao=False quando quem chama está processando um lote
    (rodízio de várias OSs de uma vez) — nesse caso, quem chama deve
    mandar um único push resumido no final, em vez de um por item (evita
    disparar muitas notificações em sequência rápida, o que já fez o
    Chrome marcar o site como "possível spam" — relatado 14/07/2026).

    Retorna um dict com o resumo do que mudou (ou None em caso de erro,
    já logado)."""
    status_interno_atual = row[8].strip()
    status_os_atual = row[14].strip()
    percentual_atual = row[20].strip()
    status_geral_atual = row[21].strip()
    agora_iso = agora_br().strftime("%Y-%m-%dT%H:%M:%S")
    try:
        token = _fracttal_get_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        resp = requests.get(f"{FRACTTAL_API_BASE}/work_orders/{numero_os}", headers=headers, timeout=15)
        resp.raise_for_status()
        tasks = (resp.json().get("data") or [])
    except Exception as e:
        log.error(f"[Fracttal] Erro ao checar/atualizar OS {numero_os}: {e}")
        # marca como verificada MESMO em erro — senão essa OS quebrada
        # (ex.: número inválido, removida da Fracttal, timeout) fica
        # sempre "a mais antiga" e monopoliza a fila de prioridade pra
        # sempre, nunca deixando outras OSs saudáveis serem revisitadas.
        try:
            ws.update_cell(i, ATIV_CAMPO_COL["ultimaVerificacaoOS"], agora_iso)
        except Exception:
            pass
        return None

    try:
        ws.update_cell(i, ATIV_CAMPO_COL["ultimaVerificacaoOS"], agora_iso)
        if not tasks:
            return {"numeroOS": numero_os, "mudou": False, "motivo": "OS sem tarefas na Fracttal"}

        status_novo_raw = str(tasks[0].get("id_status_work_order", "")).strip()
        status_novo = _FRACTTAL_STATUS_OS_MAP.get(status_novo_raw, "")
        percentual_novo = str(_fracttal_percentual_conclusao(tasks))
        status_geral_novo = _fracttal_status_geral(tasks)
        status_tarefa_novo = _fracttal_status_tarefa_agregado(tasks)
        detalhes_novo = _fracttal_detalhes_equipamentos(tasks)

        mudou = False
        if status_novo and status_novo != status_os_atual:
            ws.update_cell(i, ATIV_CAMPO_COL["statusOS"], status_novo)
            mudou = True
        if (percentual_novo != percentual_atual) or (status_geral_novo != status_geral_atual):
            ws.update_cell(i, ATIV_CAMPO_COL["statusTarefaOS"], status_tarefa_novo)
            ws.update(f"U{i}:W{i}", [[percentual_novo, status_geral_novo, detalhes_novo]])
            mudou = True

        hist_atual = row[ATIV_COL_HISTORICO - 1] if len(row) >= ATIV_COL_HISTORICO else ""
        if mudou:
            # Mensagem reescrita (17/07/2026): a versão anterior sempre
            # mostrava "status X → X, 0% → 0%" mesmo quando SÓ a situação
            # geral da tarefa tinha mudado — confuso, parecia que nada
            # tinha acontecido de verdade. Agora só entra na frase o que
            # de fato mudou, cada coisa em sua própria oração.
            partes = []
            if status_novo and status_novo != status_os_atual:
                partes.append(f"status na Fracttal mudou de \"{status_os_atual or '—'}\" para \"{status_novo}\"")
            if percentual_novo != percentual_atual:
                partes.append(f"progresso da tarefa foi de {percentual_atual or '0'}% para {percentual_novo}%")
            if status_geral_novo != status_geral_atual:
                partes.append(f"situação geral da tarefa mudou de \"{status_geral_atual or '—'}\" para \"{status_geral_novo}\"")
            if partes:
                entry = f"{agora_br().strftime('%d/%m/%Y %H:%M')} - " + "; ".join(partes) + "."
                ws.update_cell(i, ATIV_COL_HISTORICO, f"{hist_atual}\n{entry}".strip() if hist_atual else entry)
                hist_atual = f"{hist_atual}\n{entry}".strip() if hist_atual else entry

        # correção de status interno — roda SEMPRE, independente de "mudou"
        # (bug estrutural identificado e corrigido em 12/07/2026: se só
        # rodasse quando outro campo mudasse, um status já errado nunca
        # seria corrigido enquanto a Fracttal não mudasse de novo).
        status_efetivo = status_novo or status_os_atual
        novo_status_interno = _status_interno_esperado(status_efetivo, status_interno_atual)
        if novo_status_interno:
            _gravar_status_interno(ws, i, novo_status_interno)
            if novo_status_interno == "Em Aberto" and status_interno_atual in ("Concluído", "Cancelado"):
                correcao = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - ⚠️ OS reaberta automaticamente: "
                            f"estava marcada como \"{status_interno_atual}\", mas a Fracttal mostra estado "
                            f"\"{status_efetivo or '—'}\" (voltou pra Em Processo/Em Revisão — provavelmente "
                            f"reprovada ou reaberta).")
            else:
                correcao = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - ✅ Status interno corrigido pra "
                            f"\"{novo_status_interno}\" (estado na Fracttal: \"{status_efetivo or '—'}\").")
            ws.update_cell(i, ATIV_COL_HISTORICO, f"{hist_atual}\n{correcao}".strip() if hist_atual else correcao)

        if mudou and enviar_notificacao:
            try:
                usina_row = row[ATIV_CAMPO_COL["usina"] - 1] if len(row) >= ATIV_CAMPO_COL["usina"] else ""
                equipamento_row = row[ATIV_CAMPO_COL["equipamento"] - 1] if len(row) >= ATIV_CAMPO_COL["equipamento"] else ""
                id_atividade = row[0] if row else ""
                enviar_push(
                    titulo=f"🔄 OS {numero_os} — {usina_row or 'Usina não informada'}",
                    corpo=f"{equipamento_row or 'Equipamento não informado'} · {status_geral_novo} — {percentual_novo}% concluído",
                    tipo="fracttal_status",
                    url=f"https://fred-alexandrino.github.io/PAINELDEFALHAS/?atividade={id_atividade}",
                )
            except Exception as e:
                log.error(f"[sync-fracttal] Falha ao enviar push de mudança de status {numero_os}: {e}")

        return {"numeroOS": numero_os, "id": row[0] if row else "", "mudou": mudou,
                "statusOS": status_novo or status_os_atual,
                "percentualOS": percentual_novo, "statusGeralOS": status_geral_novo,
                "statusInternoCorrigido": novo_status_interno}
    except Exception as e:
        log.error(f"[Fracttal] Erro ao checar/atualizar OS {numero_os}: {e}")
        return None


def _auditoria_consistencia_os_core(aplicar=True, limite_atraso_minutos=0, limite_recheck_ao_vivo=35, origem="automática"):
    ws = get_atividades_sheet()
    todos = ws.get_all_values()
    divergencias = []
    desatualizadas = []
    agora = agora_br()
    for i, row in enumerate(todos[1:], start=2):
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        numero_os = row[13].strip()
        if not numero_os:
            continue  # só audita quem está vinculado a uma OS da Fracttal
        status_interno_atual = row[8].strip()
        status_os_atual = row[14].strip()

        # ── Parte 1: a OS está sendo verificada com a frequência que
        # deveria? Isso pega o caso mais grave — uma OS que por algum bug
        # ficou fora do rodízio e nunca mais é revisitada, então nem tem
        # como a consistência interna (parte 2) detectar problema nela,
        # porque o statusOS gravado pode estar simplesmente desatualizado
        # há muito tempo, sem ninguém perceber.
        if status_os_atual not in ("Finalizada", "Cancelada"):
            ultima_verificacao = row[23].strip()
            se_atrasada = True
            if ultima_verificacao:
                try:
                    try:
                        dt_verif = datetime.strptime(ultima_verificacao, "%Y-%m-%dT%H:%M:%S")
                    except ValueError:
                        # o Google Sheets reformata a data ao salvar/ler,
                        # trocando o "T" por espaço — aceita os dois formatos.
                        dt_verif = datetime.strptime(ultima_verificacao, "%Y-%m-%d %H:%M:%S")
                    if agora.tzinfo:
                        dt_verif = dt_verif.replace(tzinfo=agora.tzinfo)
                    minutos_desde = (agora - dt_verif).total_seconds() / 60
                    se_atrasada = minutos_desde > limite_atraso_minutos
                except Exception:
                    se_atrasada = True
            if se_atrasada:
                # BUG CRÍTICO identificado em 16/07/2026: o campo usado pra
                # ordenar (abaixo) usava "ultima_verificacao or 'nunca'" —
                # ou seja, OSs NUNCA verificadas recebiam o texto "nunca"
                # em vez de string vazia. Só que a string "nunca" começa
                # com 'n', que em ordenação alfabética vem DEPOIS de
                # qualquer timestamp (que começa com dígito) — o oposto do
                # pretendido pelo comentário original ("nunca vazio
                # primeiro"). Resultado: toda OS nunca verificada era
                # empurrada pro FIM da fila, e como só as N primeiras (35)
                # são de fato rechecadas ao vivo por rodada, uma OS nova
                # (ex.: 9513) ficava starved indefinidamente sempre que
                # havia 35+ outras OSs com QUALQUER timestamp anterior,
                # por mais antigo que fosse — nunca chegava a vez dela.
                # Corrigido usando uma chave de ordenação separada com o
                # valor cru (string vazia ordena primeiro de verdade),
                # mantendo "nunca" só como texto de exibição.
                desatualizadas.append({"id": row[0], "numeroOS": numero_os,
                                        "ultimaVerificacao": ultima_verificacao or "nunca",
                                        "_sortKey": ultima_verificacao, "linha": i, "row": row})

        # ── Parte 2: o status interno bate com o estado já gravado?
        if not status_os_atual:
            continue  # ainda sem estado conhecido — nada a auditar aqui

        esperado = _status_interno_esperado(status_os_atual, status_interno_atual)
        if esperado:
            divergencias.append({"linha": i, "id": row[0], "numeroOS": numero_os,
                                  "de": status_interno_atual, "para": esperado, "estadoFracttal": status_os_atual})
            if aplicar:
                _gravar_status_interno(ws, i, esperado)
                nota = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - 🔧 Auditoria {origem}: status interno "
                        f"corrigido de \"{status_interno_atual or '—'}\" pra \"{esperado}\" "
                        f"(estado na Fracttal: \"{status_os_atual}\").")
                hist_atual = row[ATIV_COL_HISTORICO - 1] if len(row) >= ATIV_COL_HISTORICO else ""
                ws.update_cell(i, ATIV_COL_HISTORICO, f"{hist_atual}\n{nota}".strip() if hist_atual else nota)

    # ── Parte 3: recheca AO VIVO na Fracttal as OSs mais desatualizadas —
    # isso é o que torna a auditoria de verdade "confiável", não só uma
    # conferência de campos que já podem estar todos errados juntos.
    #
    # MUDANÇA (17/07/2026, pedido do Fred): antes processava só um lote
    # fixo (limite_recheck_ao_vivo, 35) por chamada — significava que,
    # com fila grande (ex.: 114 OSs), uma OS específica podia esperar
    # vários ciclos de 5min pra ser rechecada de novo. Agora processa a
    # fila TODA em sequência dentro da mesma chamada (nº de "rodadas"
    # necessárias pra cobrir tudo, calculado a partir do total ÷ 35 —
    # só que aqui, em vez de rodadas HTTP separadas como o botão manual
    # faz, é o mesmo loop contínuo, sem reabrir conexão a cada 35).
    # Protegido por um orçamento de tempo (não um teto de contagem) pra
    # nunca estourar o timeout do gunicorn (120s) — se a fila for grande
    # demais pra caber no orçamento, processa o que der e para; o resto
    # continua com timestamp antigo, então cai automaticamente no topo
    # da fila (mais antigo primeiro) na PRÓXIMA chamada de 5min, sem
    # precisar de nenhuma lógica extra pra "lembrar onde parou".
    revalidadas_ao_vivo = []
    parou_por_orcamento = False
    if aplicar and desatualizadas:
        desatualizadas.sort(key=lambda d: d["_sortKey"])  # string vazia (nunca verificada) primeiro de verdade
        ORCAMENTO_SEGUNDOS = 60  # reduzido de 90 pra 60 (17/07/2026) — com só 1 worker
                                 # no gunicorn, cada segundo aqui é 1 segundo em que o
                                 # backend inteiro fica sem responder mais nada (frontend
                                 # trava em "Erro ao carregar atividades"). O fix real é
                                 # rodar com 2+ workers (systemd, fora do código) — isso
                                 # aqui é só uma margem extra de segurança complementar.
        inicio_recheck = time.time()
        for d in desatualizadas:
            if time.time() - inicio_recheck > ORCAMENTO_SEGUNDOS:
                parou_por_orcamento = True
                log.warning(f"[Auditoria] Orçamento de {ORCAMENTO_SEGUNDOS}s esgotado — "
                            f"{len(revalidadas_ao_vivo)}/{len(desatualizadas)} revalidadas nesta rodada, "
                            f"restante fica pro próximo ciclo automático (5min).")
                break
            resultado = _fracttal_verificar_e_atualizar_uma_os(ws, d["linha"], d["row"], d["numeroOS"],
                                                                enviar_notificacao=False)
            if resultado:
                revalidadas_ao_vivo.append(resultado)
            time.sleep(0.35)

    # um único push resumido pra tudo que mudou nessa rodada, em vez de um
    # por OS — o rodízio pode processar até 40 de uma vez, e um push por
    # item deixava a notificação "spammy" (o Chrome chegou a marcar o
    # site como "possível spam" por causa disso — relatado 14/07/2026).
    mudaram = [r for r in revalidadas_ao_vivo if r.get("mudou")]
    if mudaram:
        try:
            if len(mudaram) == 1:
                r = mudaram[0]
                enviar_push(
                    titulo=f"🔄 OS {r['numeroOS']} atualizada",
                    corpo=f"{r.get('statusGeralOS','')} — {r.get('percentualOS','0')}% concluído",
                    tipo="fracttal_status",
                    url=f"https://fred-alexandrino.github.io/PAINELDEFALHAS/?atividade={r.get('id','')}",
                )
            else:
                numeros = ", ".join(r["numeroOS"] for r in mudaram[:8])
                enviar_push(
                    titulo=f"🔄 {len(mudaram)} OSs atualizadas",
                    corpo=f"OS: {numeros}{'...' if len(mudaram) > 8 else ''}",
                    tipo="fracttal_status",
                )
        except Exception as e:
            log.error(f"[Auditoria] Falha ao enviar push resumido de status atualizado: {e}")

    for d in desatualizadas:
        d.pop("linha", None)
        d.pop("row", None)
        d.pop("_sortKey", None)

    if divergencias:
        log.warning(f"[Auditoria] {len(divergencias)} divergência(s) de status encontrada(s) "
                    f"(aplicado={aplicar}): {[d['numeroOS'] for d in divergencias]}")
    if desatualizadas:
        log.warning(f"[Auditoria] {len(desatualizadas)} OS(s) sem verificação recente na Fracttal "
                    f"(>{limite_atraso_minutos}min), {len(revalidadas_ao_vivo)} revalidada(s) ao vivo agora: "
                    f"{[d['numeroOS'] for d in desatualizadas]}")

    return {"aplicado": aplicar, "total_divergencias": len(divergencias), "divergencias": divergencias,
            "total_desatualizadas": len(desatualizadas), "desatualizadas": desatualizadas,
            "revalidadas_ao_vivo": revalidadas_ao_vivo, "limite_recheck_ao_vivo": limite_recheck_ao_vivo,
            "parou_por_orcamento_tempo": parou_por_orcamento}


def _extrair_data_fallback_historico(historico, palavras_chave=None):
    """Varre o histórico (texto multi-linha) procurando a última data/hora
    (dd/mm/aaaa hh:mm) associada a uma transição de conclusão. Se
    palavras_chave for dado, prioriza linhas que contenham alguma delas
    (ex.: 'finalizada', 'concluíd', 'normalizad', 'cancelad'); senão usa a
    última data encontrada em qualquer linha. Retorna string
    'dd/mm/aaaa hh:mm:ss' pronta pra gravar, ou None se não achar nada."""
    linhas = (historico or "").strip().split("\n")
    padrao_data = re.compile(r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})")
    candidatas = []
    for linha in linhas:
        m = padrao_data.search(linha)
        if not m:
            continue
        prioridade = 0
        if palavras_chave and any(p in linha.lower() for p in palavras_chave):
            prioridade = 1
        candidatas.append((prioridade, linha, m.group(1)))
    if not candidatas:
        return None
    candidatas.sort(key=lambda t: t[0])  # prioridade 1 por último (a gente pega o último da lista com maior prioridade)
    melhor = [c for c in candidatas if c[0] == 1] or candidatas
    return f"{melhor[-1][2]}:00"


def _validar_integridade_relatorios_core(aplicar=True):
    """AUTOMAÇÃO DE VALIDAÇÃO — roda pra TODOS os clientes de uma vez (não
    é específica de nenhum caso pontual). Varre tanto o Painel de Falhas
    quanto o Painel de Atividades procurando o padrão de bug que fazia
    ocorrências sumirem dos relatórios semanais (13/07/2026): status
    marcado como concluído/cancelado mas sem a data de fechamento
    correspondente gravada — o relatório usa exatamente esse campo pra
    decidir se algo entra ou não.

    Corrige automaticamente usando a última data relevante do histórico
    de cada item (não usa "agora" como data, pra não distorcer em qual
    semana o item realmente foi concluído). Roda 3x/dia junto com a
    auditoria completa — funciona como um "aprovado/reprovado" contínuo
    da integridade dos dados que alimentam todos os relatórios, sem
    precisar esperar alguém notar um relatório com buraco."""
    problemas = {"falhas": [], "atividades": []}

    # ── Painel de Falhas ─────────────────────────────────────────────────
    try:
        ws_falhas = get_sheet()
        todos_falhas = ws_falhas.get_all_values()
        COL_CLIENTE, COL_STATUS, COL_HISTORICO = 1, 8, 11
        COL_DATA_FECHAMENTO = 20
        palavras_falha = ("normalizad", "resolvid", "concluíd", "concluid", "cancelad", "encerrad")
        for i, row in enumerate(todos_falhas[1:], start=2):
            if len(row) <= COL_DATA_FECHAMENTO:
                row = row + [""] * (COL_DATA_FECHAMENTO + 1 - len(row))
            status = row[COL_STATUS].strip().lower()
            concluida = any(x in status for x in ("resolvid", "concluíd", "concluid", "normalizad", "cancelad"))
            data_fechamento = row[COL_DATA_FECHAMENTO].strip()
            if concluida and not data_fechamento:
                fallback = _extrair_data_fallback_historico(row[COL_HISTORICO], palavras_falha)
                item = {"linha": i, "cliente": row[COL_CLIENTE].strip(), "corrigivel": bool(fallback)}
                problemas["falhas"].append(item)
                if aplicar and fallback:
                    ws_falhas.update_cell(i, COL_DATA_FECHAMENTO + 1, fallback)
    except Exception as e:
        log.error(f"[ValidacaoRelatorios] Erro ao varrer Painel de Falhas: {e}")

    # ── Painel de Atividades ─────────────────────────────────────────────
    try:
        ws_ativ = get_atividades_sheet()
        todos_ativ = ws_ativ.get_all_values()
        palavras_ativ = ("finalizada", "concluíd", "concluid", "cancelad")
        for i, row in enumerate(todos_ativ[1:], start=2):
            if len(row) < ATIV_TOTAL_COLUNAS:
                row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
            status = row[8].strip()
            data_conclusao = row[10].strip()
            if status in ("Concluído", "Cancelado") and not data_conclusao:
                fallback = _extrair_data_fallback_historico(row[ATIV_COL_HISTORICO - 1], palavras_ativ)
                item = {"linha": i, "id": row[0].strip(), "cliente": row[1].strip(),
                        "numeroOS": row[13].strip(), "corrigivel": bool(fallback)}
                problemas["atividades"].append(item)
                if aplicar and fallback:
                    ws_ativ.update_cell(i, ATIV_CAMPO_COL["dataConclusao"], fallback)
    except Exception as e:
        log.error(f"[ValidacaoRelatorios] Erro ao varrer Painel de Atividades: {e}")

    total = len(problemas["falhas"]) + len(problemas["atividades"])
    if total > 0:
        clientes_afetados = sorted(set(
            [p["cliente"] for p in problemas["falhas"] if p.get("cliente")] +
            [p["cliente"] for p in problemas["atividades"] if p.get("cliente")]
        ))
        log.warning(f"[ValidacaoRelatorios] {total} problema(s) de integridade encontrado(s) "
                    f"(clientes: {clientes_afetados}, aplicado={aplicar})")
        try:
            enviar_push(
                titulo=f"🔧 Validação de relatórios: {total} corrigido(s)" if aplicar else f"⚠️ Validação de relatórios: {total} problema(s)",
                corpo=f"Clientes afetados: {', '.join(clientes_afetados) or '—'}",
                tipo="validacao_relatorios",
            )
        except Exception as e:
            log.error(f"[ValidacaoRelatorios] Falha ao enviar push: {e}")

    return {"aplicado": aplicar, "total_problemas": total, "detalhes": problemas}


def _auditoria_completa_core(desde_horas_descoberta=24, limite_recheck_ao_vivo=40, origem="automática"):
    """AUDITORIA COMPLETA — varredura de verdade nas usinas/equipes do
    Fred, cobrindo tudo que uma auditoria de verdade precisa cobrir:
      1. DESCOBERTA: busca na Fracttal por OTs novas dentro da janela
         (padrão 24h) que ainda não estão no dashboard — pega OS nova
         que a descoberta rápida de rotina (2h) porventura tenha perdido.
      2. VARREDURA DE STATUS/ESTADO: revalida ao vivo na Fracttal um lote
         das OSs já existentes — detecta não só mudança de percentual,
         mas também cancelamentos e conclusões que tenham escapado.
      3. VALIDAÇÃO DE INTEGRIDADE DE RELATÓRIOS: varre Painel de Falhas +
         Painel de Atividades (todos os clientes) procurando o padrão que
         faz ocorrências sumirem dos relatórios semanais, corrigindo
         automaticamente.
    Roda automaticamente 3x/dia (7h/12h/16h) e sob demanda no botão
    "Auditoria". Mais pesada que a checagem de rotina (frequente, 5 em
    5 min) de propósito — por isso não roda toda hora, só nesses horários."""
    resultado_descoberta, _ = _sync_fracttal_core(desde_horas=desde_horas_descoberta)
    resultado_consistencia = _auditoria_consistencia_os_core(aplicar=True, limite_atraso_minutos=0,
                                                              limite_recheck_ao_vivo=limite_recheck_ao_vivo,
                                                              origem=origem)
    resultado_validacao_relatorios = _validar_integridade_relatorios_core(aplicar=True)
    return {"descoberta": resultado_descoberta, "consistencia": resultado_consistencia,
            "validacao_relatorios": resultado_validacao_relatorios}


def _verificar_e_disparar_auditoria_completa_se_necessario():
    """Só dispara a auditoria completa de verdade se estiver dentro de uma
    das 3 janelas do dia (07:00-07:09, 12:00-12:09, 16:00-16:09, horário
    de Brasília) e ainda não tiver rodado nessa janela hoje — mesmo
    padrão de trava usado pros comunicados, adaptado pra 3 horários."""
    try:
        agora = agora_br()
        janela_atual = None
        for h in (7, 12, 16):
            if agora.hour == h and agora.minute < 10:
                janela_atual = h
                break
        if janela_atual is None:
            return {"disparado": False, "motivo": f"fora das janelas 7h/12h/16h (agora {agora.strftime('%H:%M')})"}

        chave_trava = f"auditoria_completa_em_{janela_atual}h"
        hoje_str = agora.strftime("%Y-%m-%d")
        ja_rodou = _ler_trava(chave_trava)
        if ja_rodou == hoje_str:
            return {"disparado": False, "motivo": f"já rodou hoje na janela das {janela_atual}h"}

        _gravar_trava(chave_trava, hoje_str)
        resultado = _auditoria_completa_core()
        return {"disparado": True, "janela": f"{janela_atual}h", "resultado": resultado}
    except Exception as e:
        log.error(f"[AuditoriaCompleta] Erro na verificação/disparo: {e}")
        return {"disparado": False, "erro": str(e)}


def _verificar_e_disparar_descoberta_rapida_se_necessario(intervalo_minutos=30):
    """DESCOBERTA RÁPIDA — roda automaticamente a cada 30 min via piggyback
    no /sync-fracttal (mesmo gatilho confiável dos 5 min já usado pra
    atualização de status). Sem botão manual — existe só pra reduzir o
    gap de latência entre uma OS nova nascer na Fracttal e ela aparecer
    no dashboard, que antes podia chegar a ~9h (pior caso: OS criada logo
    depois da janela das 16h só entraria às 7h do dia seguinte).

    Deliberadamente LEVE, ao contrário da auditoria completa: só chama
    _sync_fracttal_core (descoberta pura, sem recheck de OSs existentes)
    com janela curta (2h) — não faz a varredura ampla nem a validação de
    integridade de relatórios que a auditoria completa faz. Isso evita
    reintroduzir o risco de 502 que já vimos quando descoberta ampla e
    recheck pesado rodaram juntos no mesmo request.

    Trava por timestamp (não por dia, como as outras) porque precisa
    rodar várias vezes ao dia, não uma vez só por janela."""
    try:
        agora = agora_br()
        chave_trava = "descoberta_rapida_ultima_em"
        ultima_str = _ler_trava(chave_trava)
        if ultima_str:
            try:
                ultima = datetime.strptime(ultima_str, "%Y-%m-%d %H:%M:%S")
                minutos_desde = (agora.replace(tzinfo=None) - ultima).total_seconds() / 60
                if minutos_desde < intervalo_minutos:
                    return {"disparado": False,
                            "motivo": f"rodou há {minutos_desde:.1f}min (< {intervalo_minutos}min); última em {ultima_str}"}
            except ValueError:
                pass  # trava com valor inválido/corrompido — trata como se nunca tivesse rodado

        _gravar_trava(chave_trava, agora.strftime("%Y-%m-%d %H:%M:%S"))
        resultado, status_http = _sync_fracttal_core(desde_horas=2)
        if status_http != 200:
            return {"disparado": True, "erro": resultado}
        return {"disparado": True, "resultado": resultado}
    except Exception as e:
        log.error(f"[DescobertaRapida] Erro na verificação/disparo: {e}")
        return {"disparado": False, "erro": str(e)}


@app.route("/auditoria-consistencia-os", methods=["POST", "GET"])
def auditoria_consistencia_os():
    """Rede de segurança definitiva: varre TODAS as atividades vinculadas
    a uma OS da Fracttal e confere se o status interno bate com o que
    _status_interno_esperado() diz que deveria ser, dado o estado
    (statusOS) atual já registrado — sem precisar chamar a API da
    Fracttal de novo (usa o que já está gravado, então é rápido e barato
    de rodar com frequência). Corrige qualquer divergência encontrada.

    Roda automaticamente a cada 5 min via piggyback no sync-fracttal
    (gatilho confiável), então qualquer inconsistência que escape da
    checagem normal (por bug futuro, edição manual, etc.) se autocorrige
    sozinha em poucos minutos, sem precisar de intervenção manual."""
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    aplicar = request.args.get("apply", "true").lower() != "false"
    resultado = _auditoria_consistencia_os_core(aplicar, origem="manual (diagnóstico)")
    return jsonify({"ok": True, **resultado}), 200


@app.route("/resumo", methods=["GET"])
def resumo_widget():
    """
    Endpoint leve pra consumo por widgets externos (ex.: apps de widget
    Android tipo KWGT/HTTP Request Widget, configurados pelo Fred na tela
    inicial do celular) — só os números-chave, sem os dados completos de
    cada atividade/chamado, pra ser rápido e simples de exibir.

    GET simples, sem secret (só números agregados, nada sensível de cada
    registro individual é exposto aqui).
    """
    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
        status_excluidos = {"concluído", "concluido", "cancelado",
                             "convertida em ocorrência", "convertida em ocorrencia"}
        total = abertas = atrasadas = altas_abertas = concluidas_7d = 0
        hoje = agora_br().date()
        limite7 = hoje - timedelta(days=7)

        for row in todos[1:]:
            if len(row) < len(ATIV_HEADERS_JSON):
                row = row + [""] * (len(ATIV_HEADERS_JSON) - len(row))
            item = dict(zip(ATIV_HEADERS_JSON, row[:len(ATIV_HEADERS_JSON)]))
            if not item.get("id"):
                continue
            total += 1
            concluida = (item.get("status") or "").strip().lower() in status_excluidos
            if not concluida:
                abertas += 1
                prazo_str = (item.get("prazo") or "").strip()
                if prazo_str:
                    try:
                        if datetime.strptime(prazo_str, "%d/%m/%Y").date() < hoje:
                            atrasadas += 1
                    except Exception:
                        pass
                if (item.get("prioridade") or "").strip().lower() == "alta":
                    altas_abertas += 1
            else:
                dataconc = (item.get("dataConclusao") or "").strip().split(" ")[0]
                if dataconc:
                    try:
                        if datetime.strptime(dataconc, "%d/%m/%Y").date() >= limite7:
                            concluidas_7d += 1
                    except Exception:
                        pass

        chamados_total = chamados_abertos = 0
        try:
            ws_ch = get_chamados_fabricante_sheet()
            todos_ch = ws_ch.get_all_values()
            idx_supervisor = CHAMADOS_FABRICANTE_HEADERS.index("Supervisor")
            idx_status = CHAMADOS_FABRICANTE_HEADERS.index("Status")
            for row in todos_ch[1:]:
                if len(row) < len(CHAMADOS_FABRICANTE_HEADERS):
                    row = row + [""] * (len(CHAMADOS_FABRICANTE_HEADERS) - len(row))
                if row[idx_supervisor].strip().lower() != "fred alexandrino":
                    continue
                chamados_total += 1
                st = row[idx_status].strip().lower()
                if not any(k in st for k in ("conclu", "resolv", "fechad", "finaliz")):
                    chamados_abertos += 1
        except Exception:
            pass  # chamados é "bônus" no resumo, não deve derrubar o endpoint todo

        return jsonify({
            "ok": True,
            "atualizado_em": agora_br().strftime("%d/%m/%Y %H:%M"),
            "atividades": {
                "total": total,
                "em_aberto": abertas,
                "atrasadas": atrasadas,
                "prioridade_alta_abertas": altas_abertas,
                "concluidas_7d": concluidas_7d,
            },
            "chamados_fred": {
                "total": chamados_total,
                "em_aberto": chamados_abertos,
            },
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/atividades", methods=["GET"])
def listar_atividades():
    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
        mapa_cluster = _mapa_cluster_usina()
        out = []
        for row in todos[1:]:
            if len(row) < len(ATIV_HEADERS_JSON):
                row = row + [""] * (len(ATIV_HEADERS_JSON) - len(row))
            if not row[0].strip():
                continue
            item = dict(zip(ATIV_HEADERS_JSON, row[:len(ATIV_HEADERS_JSON)]))
            item["cluster"] = mapa_cluster.get(item.get("usina", "").strip(), "")
            out.append(item)
        return jsonify({"ok": True, "atividades": out})
    except Exception as e:
        log.error(f"[Atividades] Erro ao listar: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


def _criar_atividade_interna(cliente, usina="", equipamento="", descricao="", responsavel="",
                              prazo="", prioridade="Média", status="Em Aberto", numeroOS="",
                              editor="dashboard", statusOS="", observacoesOS="", linkOS="",
                              statusTarefaOS="", etiquetasOS="", anotacoesPessoais="",
                              percentualOS="", statusGeralOS="", detalhesEquipamentosOS="",
                              ws=None, todos=None, enviar_notificacao=True):
    """
    Cria uma linha na aba Painel de Atividades. Usada tanto pelo endpoint
    HTTP /nova-atividade quanto pelo sync automático do Fracttal
    (/sync-fracttal, /backfill-fracttal), para evitar duplicar a lógica de
    escrita na planilha.

    Se `ws`/`todos` forem passados (leitura já feita por quem chamou, ex.
    sync em lote), evita reler a planilha inteira a cada chamada.

    enviar_notificacao=False quando quem chama vai criar várias atividades
    de uma vez (ex.: descoberta da Fracttal encontrando N OTs novas na
    mesma rodada) — nesse caso, quem chama deve mandar um único push
    resumido no final, em vez de um por item (evita disparar muitas
    notificações em sequência rápida — o Chrome já marcou o site como
    "possível spam" por causa disso antes, 14/07/2026. RESTAURADO em
    15/07/2026 depois de ter sido perdido numa edição de outra sessão que
    reconstruiu esta função a partir de uma versão mais antiga do arquivo).
    """
    cliente = (cliente or "").strip()
    descricao = (descricao or "").strip()
    if not cliente or not descricao:
        raise ValueError("cliente e descricao são obrigatórios")

    if ws is None:
        ws = get_atividades_sheet()
    if todos is None:
        todos = ws.get_all_values()

    numeroOS = (numeroOS or "").strip()
    if numeroOS:
        for row in todos[1:]:
            if len(row) < 14:
                continue
            numero_os_existente = row[13].strip()
            status_existente = row[8].strip()
            if numero_os_existente == numeroOS and not _is_concluido_atividade(status_existente):
                raise ValueError(f"Já existe uma atividade em aberto (id {row[0]}) pra essa OS ({numeroOS}). "
                                  f"Abra e edite a atividade existente em vez de criar uma nova.")

    _garantir_headers_atividades(ws)

    novo_id = _proximo_id_atividade(todos)
    agora = agora_br().strftime('%d/%m/%Y %H:%M:%S')
    historico_inicial = f"{agora_br().strftime('%d/%m/%Y %H:%M')} - Atividade criada por {_editor_legivel(editor)}."
    data_conclusao_inicial = agora if status in ("Concluído", "Cancelado") else ""

    linha = [novo_id, cliente, usina, equipamento, descricao, responsavel, prazo,
             prioridade, status, agora, data_conclusao_inicial, historico_inicial, editor, numeroOS,
             statusOS, observacoesOS, linkOS, statusTarefaOS, etiquetasOS, anotacoesPessoais,
             percentualOS, statusGeralOS, detalhesEquipamentosOS, "", ""]
    ws.append_row(linha)
    # mantém `todos` coerente para quem estiver criando várias atividades em sequência
    todos.append(linha)
    log.info(f"[atividade] #{novo_id} {cliente}/{usina} — {descricao[:60]} | editor={editor}")

    if enviar_notificacao:
        try:
            enviar_push(
                titulo=f"🆕 Nova atividade" + (f" — OS {numeroOS}" if numeroOS else "") + f" — {usina or cliente}",
                corpo=(f"{equipamento} · " if equipamento else "") +
                      (f"{descricao[:80]}" if descricao else "Atividade criada"),
                tipo="nova_atividade",
                url=f"https://fred-alexandrino.github.io/PAINELDEFALHAS/?atividade={novo_id}",
            )
        except Exception as e:
            log.error(f"[Push] Erro ao notificar nova atividade: {e}")

    return novo_id


@app.route("/nova-atividade", methods=["POST", "OPTIONS"])
def nova_atividade():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "Body inválido"}), 400

    try:
        novo_id = _criar_atividade_interna(
            cliente=body.get("cliente", ""),
            usina=body.get("usina", ""),
            equipamento=body.get("equipamento", ""),
            descricao=body.get("descricao", ""),
            responsavel=body.get("responsavel", ""),
            prazo=body.get("prazo", ""),
            prioridade=body.get("prioridade", "Média").strip() or "Média",
            status=body.get("status", "Em Aberto").strip() or "Em Aberto",
            numeroOS=body.get("numeroOS", ""),
            editor=body.get("editor", "dashboard").strip() or "dashboard",
            statusOS=body.get("statusOS", ""),
            observacoesOS=body.get("observacoesOS", ""),
            linkOS=body.get("linkOS", ""),
            statusTarefaOS=body.get("statusTarefaOS", ""),
            etiquetasOS=body.get("etiquetasOS", ""),
            anotacoesPessoais=body.get("anotacoesPessoais", ""),
            percentualOS=body.get("percentualOS", ""),
            statusGeralOS=body.get("statusGeralOS", ""),
            detalhesEquipamentosOS=body.get("detalhesEquipamentosOS", ""),
        )
        return jsonify({"ok": True, "id": novo_id})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        log.error(f"[Atividades] Erro ao criar: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Integração Fracttal (sync automático de OTs → Painel de Atividades) ───
FRACTTAL_CLIENT_KEY    = os.environ.get("FRACTTAL_CLIENT_KEY", "")
FRACTTAL_CLIENT_SECRET = os.environ.get("FRACTTAL_CLIENT_SECRET", "")
FRACTTAL_TOKEN_URL     = "https://one.fracttal.com/oauth/token"
FRACTTAL_API_BASE      = "https://app.fracttal.com/api"

_fracttal_token_cache = {"access_token": None, "expires_at": 0}


def _fracttal_get_token():
    """Obtém (com cache em memória) um access_token OAuth2 client_credentials da Fracttal."""
    agora = time.time()
    if _fracttal_token_cache["access_token"] and _fracttal_token_cache["expires_at"] > agora + 60:
        return _fracttal_token_cache["access_token"]

    if not FRACTTAL_CLIENT_KEY or not FRACTTAL_CLIENT_SECRET:
        raise RuntimeError("FRACTTAL_CLIENT_KEY / FRACTTAL_CLIENT_SECRET não configurados no Render")

    resp = requests.post(
        FRACTTAL_TOKEN_URL,
        auth=(FRACTTAL_CLIENT_KEY, FRACTTAL_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    _fracttal_token_cache["access_token"] = data["access_token"]
    _fracttal_token_cache["expires_at"] = agora + int(data.get("expires_in", 7200))
    return _fracttal_token_cache["access_token"]


def _fracttal_listar_pagina(since=None, until=None, ot_status=None, start=0, limit=100):
    """
    Consulta uma página de work_orders na Fracttal usando os parâmetros
    OFICIAIS confirmados na documentação (api.fracttal.com/reference):
      since / until   — formato 'YYYY-MM-DDTHH:MM:SS-00:00', filtra por creation_date
      ot_status       — 1: Processo, 2: Revisão, 3: Finalizada, 4: Cancelada
      start / limit   — paginação (limit máximo 100, é o teto da própria Fracttal)

    Retorna (lista_de_ots, total_geral).
    """
    token = _fracttal_get_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {"start": start, "limit": min(limit, 100)}
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    if ot_status:
        params["ot_status"] = ot_status

    resp = requests.get(f"{FRACTTAL_API_BASE}/work_orders", headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    return body.get("data", []) or [], body.get("total", 0)


def _fracttal_listar_ots_recentes(desde_horas=3):
    """Consulta OTs criadas/atualizadas nas últimas `desde_horas` horas (usado pelo cron de 2h em 2h)."""
    since = (datetime.utcnow() - timedelta(hours=desde_horas)).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    ots, _total = _fracttal_listar_pagina(since=since, start=0, limit=100)
    return ots


_FRACTTAL_PRIORIDADE_MAP = {
    "HIGH": "Alta", "ALTA": "Alta",
    "MEDIUM": "Média", "MEDIA": "Média", "MÉDIA": "Média",
    "LOW": "Baixa", "BAIXA": "Baixa",
}

# 1: Processo, 2: Revisão, 3: Finalizada, 4: Cancelada (confirmado na doc oficial da Fracttal)
_FRACTTAL_STATUS_OS_MAP = {
    "1": "Em Processo", "2": "Em Revisão", "3": "Finalizada", "4": "Cancelada",
}


def _status_interno_esperado(estado_fracttal, status_interno_atual):
    """FONTE ÚNICA DE VERDADE pra decidir o status interno (coluna
    "status") a partir do ESTADO real da OS na Fracttal (coluna "statusOS"
    — Em Processo/Em Revisão/Finalizada/Cancelada). Usada tanto na criação
    de uma atividade nova quanto em TODA checagem de rotina subsequente —
    nunca duplicar essa lógica em outro lugar do código.

    Regras (only touches these two transitions, nunca mexe em status
    manuais tipo "Pausado"/"Aguardando Cliente" etc. definidos por
    técnico via WhatsApp):
      1. Estado = Finalizada  → status interno deve ser "Concluído"
         (a menos que já esteja "Cancelado", que é uma conclusão também).
      2. Estado = Cancelada   → status interno deve ser "Cancelado".
      3. Se o status interno atual é "Concluído"/"Cancelado" mas o estado
         NÃO é mais Finalizada/Cancelada (reaberta/reprovada na Fracttal)
         → volta pra "Em Aberto".
      4. Qualquer outro caso (estado ainda em Processo/Revisão e status
         interno não é Concluído/Cancelado) → não mexe, devolve None.

    Retorna o novo valor se precisar corrigir, ou None se já está certo.
    IMPORTANTE: essa função deve rodar em TODA checagem, independente de
    mais alguma coisa ter mudado na mesma passada — é isso que garante
    que o sistema se autocorrige, em vez de só corrigir "de carona" numa
    mudança de outro campo (bug estrutural corrigido em 12/07/2026)."""
    if estado_fracttal == "Finalizada":
        alvo = "Concluído"
    elif estado_fracttal == "Cancelada":
        alvo = "Cancelado"
    elif estado_fracttal in ("Em Processo", "Em Revisão") and status_interno_atual in ("Concluído", "Cancelado"):
        alvo = "Em Aberto"
    else:
        return None
    return alvo if alvo != status_interno_atual else None


def _gravar_status_interno(ws, i, novo_status):
    """FONTE ÚNICA que grava o status interno na planilha — usada nos 3
    pontos do código que podem mudar esse campo (checagem individual,
    auditoria, reabertura). Sempre que o novo status é uma conclusão
    (Concluído/Cancelado), também grava a Data de Conclusão — campo que
    ficava sempre vazio antes (bug identificado em 13/07/2026: relatórios
    semanais usam esse campo pra saber se algo fechou dentro do período,
    e como nunca era preenchido, OSs concluídas sumiam dos relatórios).
    Ao reabrir (volta pra "Em Aberto"), limpa a Data de Conclusão de novo.

    Quando vira "Cancelado", também sincroniza o statusGeralOS pra
    "Cancelada" — senão esse campo (progresso da tarefa) fica com o valor
    de antes do cancelamento pra sempre (a Fracttal não manda mais dado
    novo pra uma OS cancelada), fazendo o badge mostrar algo tipo "Não
    Iniciada" em vez de refletir que foi cancelada (bug identificado em
    14/07/2026)."""
    ws.update_cell(i, ATIV_CAMPO_COL["status"], novo_status)
    if novo_status in ("Concluído", "Cancelado"):
        ws.update_cell(i, ATIV_CAMPO_COL["dataConclusao"], agora_br().strftime("%d/%m/%Y %H:%M:%S"))
    elif novo_status == "Em Aberto":
        ws.update_cell(i, ATIV_CAMPO_COL["dataConclusao"], "")
    if novo_status == "Cancelado":
        ws.update_cell(i, ATIV_CAMPO_COL["statusGeralOS"], "Cancelada")

# A Fracttal tem o add-on "Share TOs" habilitado nesta conta, que gera uma
# URL pública específica por OT via /work_orders_shared_url/{folio}
# (confirmado com um teste real — ver histórico). Isso abre a OT direto,
# sem precisar buscar manualmente na lista. Se a chamada falhar por
# qualquer motivo, cai pro fallback da tela de OTs (fluxo antigo).
FRACTTAL_WEB_BASE = "https://app.fracttal.com/tasks/wo"


def _fracttal_montar_link(ot):
    folio = (ot.get("wo_folio") or "").strip()
    if not folio:
        return ""
    try:
        token = _fracttal_get_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        resp = requests.get(f"{FRACTTAL_API_BASE}/work_orders_shared_url/{folio}",
                             headers=headers, timeout=15)
        resp.raise_for_status()
        dados = (resp.json().get("data") or [])
        if dados and dados[0].get("shared_wo_url"):
            return dados[0]["shared_wo_url"]
    except Exception as e:
        log.error(f"[fracttal] Erro ao buscar shared_wo_url da OT {folio}: {e}")
    return FRACTTAL_WEB_BASE


def _fracttal_formatar_data_br(iso_str):
    """Extrai apenas AAAA-MM-DD do timestamp ISO da Fracttal e devolve dd/mm/aaaa."""
    if not iso_str:
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(iso_str))
    if not m:
        return ""
    ano, mes, dia = m.groups()
    return f"{dia}/{mes}/{ano}"

# ── Cruzamento técnico responsável → usina(s) atendida(s) ─────────────────
# Usado para VALIDAR o match feito pelo nome do ativo (cross-check) e,
# quando o nome do ativo não bate com o catálogo, como fallback — mas só
# quando o técnico atende uma única usina do catálogo (senão é ambíguo e
# a OT vai para revisão manual em vez de arriscar um chute).
TECNICO_USINAS = {
    "rodolfo oliveira":  ["Boa Esperança do Sul I", "Boa Esperança do Sul II", "Ibaté I", "Ibaté II"],
    "andrick gouveia":   ["Boa Esperança do Sul I", "Boa Esperança do Sul II", "Ibaté I", "Ibaté II"],
    "equipe piracicaba": ["Elias Fausto"],
    "deivity saugo":     ["Colíder I", "Colíder II"],
    "deivity jhon cunha saugo": ["Colíder I", "Colíder II"],
    "railson gomes":     ["Crateús"],
    "valmir junior":     ["Nobres"],
    "lucas lima":        ["Nobres"],
    "gabriel oliveira":  ["Nova Xavantina I", "Nova Xavantina II"],
    "eduardo souza":     ["Matão 1", "Matão II - Topázio"],
    "aniel rocha":       ["Araputanga", "Poconé"],
    "adriano moraes":    ["Araputanga", "Poconé"],
    "claudio ferreira":  ["Sítio Bonfim", "ABC Morada Nova", "Sol do Norte I", "Sol do Norte II", "Guajirú"],
    "cláudio ferreira":  ["Sítio Bonfim", "ABC Morada Nova", "Sol do Norte I", "Sol do Norte II", "Guajirú"],
    "isake costa":       ["Sítio Bonfim", "ABC Morada Nova", "Sol do Norte I", "Sol do Norte II", "Guajirú"],
    "daniel de paula":   ["Sete Lagoas"],
}


def _normalizar_tecnico(nome):
    return _norm_usina(nome)  # mesma normalização (sem acento, minúsculo) já usada pra usina


def _extrair_nome_usina_fracttal(texto):
    """
    A Fracttal nomeia o campo groups_1_description no formato
    "Cliente - Nome da Usina - UF" (ex: "Thopen - Boa Esperança do Sul 1 - SP").
    Extrai só a parte do meio (nome da usina) pra comparar com o catálogo,
    removendo o prefixo de cliente e o sufixo de UF quando presentes.
    """
    if not texto:
        return ""
    partes = [p.strip() for p in texto.split(" - ") if p.strip()]
    if len(partes) >= 3:
        return " - ".join(partes[1:-1])
    if len(partes) == 2:
        return partes[1]
    return texto


_STATUS_TAREFA_MAP = {
    "NO_STARTED": "Não Iniciada",
    "IN_PROGRESS": "Em Progresso",
    "PAUSED": "Pausada",
    "DONE": "Concluída",
}


def _fracttal_status_tarefa_label(task_status_raw):
    return _STATUS_TAREFA_MAP.get((task_status_raw or "").strip().upper(), (task_status_raw or "").strip())


def _fracttal_agrupar_por_wo(ots):
    """Agrupa uma lista de linhas (uma por tarefa) da Fracttal pelo wo_folio (a mesma OS)."""
    grupos, ordem = {}, []
    for ot in ots:
        folio = (ot.get("wo_folio") or "").strip()
        if not folio:
            continue
        if folio not in grupos:
            grupos[folio] = []
            ordem.append(folio)
        grupos[folio].append(ot)
    return [(folio, grupos[folio]) for folio in ordem]


_PREVENTIVA_PERIODICIDADE_MAP = {
    "semestral": ("PREVENTIVA SEMESTRAL", "Múltiplos equipamentos (Preventiva Semestral)"),
    "anual": ("PREVENTIVA ANUAL", "Múltiplos equipamentos (Preventiva Anual)"),
    "mensal": ("PREVENTIVA MENSAL", "Múltiplos equipamentos (Preventiva Mensal)"),
}


def _fracttal_detectar_preventiva(tasks, texto_grupo_ativo=""):
    """Detecta se uma OS com múltiplas tarefas é uma manutenção preventiva
    periódica (MPM/MPS/MPA) — usa nomenclatura padronizada ("PREVENTIVA
    MENSAL/SEMESTRAL/ANUAL" e "Múltiplos equipamentos (Preventiva X)") em
    vez de listar cada tarefa individualmente. Detecção por palavra-chave
    OU pela sigla (MPM/MPS/MPA) nas descrições das tarefas — a Fracttal às
    vezes usa só a sigla (ex.: "[Grid Co.] - MPA") sem escrever "preventiva
    anual" por extenso. "Semestral"/"anual" são checados antes de "mensal"
    pra evitar falso-positivo (ex.: um texto que cite os dois por algum
    motivo).

    Retorna (titulo, equipamento) ou (None, None) se não for preventiva
    periódica reconhecida.
    """
    textos = [(t.get("description") or "") for t in tasks]
    textos.append(texto_grupo_ativo or "")
    junto = " ".join(textos).lower()

    if re.search(r"\bmps\b", junto):
        return _PREVENTIVA_PERIODICIDADE_MAP["semestral"]
    if re.search(r"\bmpa\b", junto):
        return _PREVENTIVA_PERIODICIDADE_MAP["anual"]
    if re.search(r"\bmpm\b", junto):
        return _PREVENTIVA_PERIODICIDADE_MAP["mensal"]

    if "preventiv" not in junto:
        return None, None
    if "semestral" in junto:
        return _PREVENTIVA_PERIODICIDADE_MAP["semestral"]
    if "anual" in junto:
        return _PREVENTIVA_PERIODICIDADE_MAP["anual"]
    if "mensal" in junto:
        return _PREVENTIVA_PERIODICIDADE_MAP["mensal"]
    return None, None


def _fracttal_eh_preventiva_mensal(tasks, texto_grupo_ativo=""):
    """Mantido por compatibilidade: só a variante mensal."""
    titulo, _ = _fracttal_detectar_preventiva(tasks, texto_grupo_ativo)
    return titulo == "PREVENTIVA MENSAL"


def _fracttal_descricao_agregada(tasks):
    descs = [(t.get("description") or "").strip() for t in tasks if (t.get("description") or "").strip()]
    if not descs:
        return ""
    if len(tasks) == 1:
        return descs[0]
    m = re.match(r"^(\[[^\]]*\]\s*-\s*[^-]+)\s*-", descs[0])
    if m:
        return m.group(1).strip()
    return descs[0]


def _fracttal_prazo_agregado(tasks):
    """Prazo mais próximo entre todas as tarefas da OS (a que vence primeiro)."""
    datas = []
    for t in tasks:
        bruta = t.get("final_date") or t.get("date_maintenance") or t.get("cal_date_maintenance") or t.get("initial_date")
        if bruta:
            datas.append(str(bruta))
    if not datas:
        return ""
    datas.sort()
    return _fracttal_formatar_data_br(datas[0])


def _fracttal_status_tarefa_agregado(tasks):
    contagem = {}
    for t in tasks:
        label = _fracttal_status_tarefa_label(t.get("task_status"))
        if label:
            contagem[label] = contagem.get(label, 0) + 1
    if not contagem:
        return ""
    if len(tasks) == 1:
        return next(iter(contagem))
    return " | ".join(f"{qtd} {label}" for label, qtd in contagem.items())


def _fracttal_etiquetas_agregadas(tasks):
    vistas, nomes = set(), []
    for t in tasks:
        for lbl in (t.get("labels") or []):
            desc = (lbl.get("description") or "").strip()
            if desc and desc not in vistas:
                vistas.add(desc)
                nomes.append(desc)
    return ", ".join(nomes)


def _fracttal_observacoes_agregadas(tasks):
    vistas, notas = set(), []
    for t in tasks:
        nota = (t.get("task_note") or t.get("note") or "").strip()
        if nota and nota not in vistas:
            vistas.add(nota)
            notas.append(nota)
    return "\n---\n".join(notas)


def _fracttal_historico_detalhe(tasks):
    """Detalhamento por equipamento — só gerado quando a OS tem mais de uma tarefa."""
    if len(tasks) <= 1:
        return ""
    linhas = [f"⚙️ OS com {len(tasks)} itens/equipamentos — detalhamento:"]
    for t in tasks:
        eq = (t.get("items_log_description") or t.get("code") or "?").split("{")[0].strip()
        status = _fracttal_status_tarefa_label(t.get("task_status"))
        prazo = _fracttal_formatar_data_br(t.get("final_date") or t.get("date_maintenance") or "")
        linha = f"• {eq} — {status}"
        if prazo:
            linha += f" (prazo {prazo})"
        linhas.append(linha)
    return "\n".join(linhas)


def _fracttal_percentual_conclusao(tasks):
    total = len(tasks)
    if not total:
        return 0
    valores = []
    for t in tasks:
        cp = t.get("completed_percentage")
        if cp is not None:
            try:
                valores.append(float(cp))
                continue
            except (TypeError, ValueError):
                pass
        valores.append(100.0 if (t.get("task_status") or "").strip().upper() == "DONE" else 0.0)
    return round(sum(valores) / total)


def _fracttal_status_geral(tasks):
    """
    Status agregado da OS inteira em uma das 4 categorias que a Fracttal usa
    na Vista Kanban: Não Iniciada, Em Progresso, Pausada, Concluída.
    """
    total = len(tasks)
    if not total:
        return ""
    concluidas = sum(1 for t in tasks if (t.get("task_status") or "").strip().upper() == "DONE")
    em_progresso = sum(1 for t in tasks if (t.get("task_status") or "").strip().upper() == "IN_PROGRESS")
    pausadas = sum(1 for t in tasks if (t.get("task_status") or "").strip().upper() == "PAUSED")
    if concluidas == total:
        return "Concluída"
    if em_progresso > 0 or concluidas > 0:
        return "Em Progresso"
    if pausadas > 0:
        return "Pausada"
    return "Não Iniciada"


def _fracttal_detalhes_equipamentos(tasks):
    """
    Lista estruturada (JSON) de cada equipamento/tarefa da OS — usada pelo
    drawer/card pra montar uma tabela organizada em vez de só um texto no
    histórico. Cada item: {equipamento, status, prazo}.
    """
    itens = []
    for t in tasks:
        eq = (t.get("items_log_description") or t.get("code") or "?").split("{")[0].strip()
        status = _fracttal_status_tarefa_label(t.get("task_status"))
        prazo = _fracttal_formatar_data_br(t.get("final_date") or t.get("date_maintenance") or "")
        itens.append({"equipamento": eq, "status": status, "prazo": prazo})
    return json.dumps(itens, ensure_ascii=False)


def _fracttal_mapear_grupo(tasks):
    """
    Converte um GRUPO de tarefas (todas da mesma OS, mesmo wo_folio) para
    os campos do Painel de Atividades — uma OS vira UMA atividade, mesmo
    quando tem várias tarefas/equipamentos (ex: preventivas mensais/anuais
    com dezenas de itens). Detalhamento por equipamento vai pro Histórico.

    O cruzamento usina x técnico responsável usa a primeira tarefa como
    representante (grupo/usina e técnico geralmente são os mesmos pra
    todas as tarefas de uma mesma OS).

      1. Nome do ativo bate com o catálogo E técnico é esperado nessa usina
         → segue normal, sem alerta.
      2. Nome do ativo bate, mas o técnico não é dos que atendem essa usina
         → cria mesmo assim (nome do ativo é a fonte mais confiável), mas
           grava um alerta no histórico pra você conferir.
      3. Nome do ativo NÃO bate, mas o técnico atende só 1 usina do catálogo
         → usa a usina do técnico como fallback, com alerta no histórico.
      4. Nome do ativo não bate e o técnico atende mais de uma usina (ou é
         desconhecido) → não dá pra decidir sozinho, vai para revisão manual
         (retorna None com motivo, não cria nada).
    """
    representante = tasks[0]
    texto_grupo = _extrair_nome_usina_fracttal(representante.get("groups_1_description") or "")
    texto_ativo = representante.get("items_log_description") or representante.get("parent_description") or representante.get("item_code") or ""

    usina_por_ativo = canonizar_usina(texto_grupo) or canonizar_usina(texto_ativo)
    texto_usado = texto_grupo or texto_ativo

    tecnico_raw = (representante.get("personnel_description") or representante.get("responsible") or representante.get("created_by") or "").strip()
    tecnico_norm = _normalizar_tecnico(tecnico_raw)
    usinas_do_tecnico = TECNICO_USINAS.get(tecnico_norm, [])

    usina = None
    alerta = None

    if usina_por_ativo:
        usina = usina_por_ativo
        if usinas_do_tecnico and usina not in usinas_do_tecnico:
            alerta = (f"⚠️ Cruzamento: técnico \"{tecnico_raw}\" não está mapeado para {usina} "
                      f"(usinas esperadas dele: {', '.join(usinas_do_tecnico)}). Confira se a usina está certa.")
    elif len(usinas_do_tecnico) == 1:
        usina = usinas_do_tecnico[0]
        alerta = (f"⚠️ Usina inferida pelo técnico responsável (\"{tecnico_raw}\"), pois nem o grupo "
                  f"(\"{texto_grupo}\") nem o ativo (\"{texto_ativo}\") bateram com o catálogo. Confira se está correto.")
    else:
        if usinas_do_tecnico:
            motivo = (f"Grupo/ativo (\"{texto_usado}\") não reconhecido e técnico \"{tecnico_raw}\" atende mais de "
                      f"uma usina ({', '.join(usinas_do_tecnico)}) — não dá pra decidir sozinho.")
        elif tecnico_raw:
            motivo = f"Grupo/ativo (\"{texto_usado}\") não reconhecido e técnico \"{tecnico_raw}\" não está no mapa de usinas."
        else:
            motivo = f"Grupo/ativo (\"{texto_usado}\") não reconhecido e OT sem técnico responsável informado."
        return {"_revisao_manual": True, "motivo": motivo, "wo_folio": representante.get("wo_folio", "?")}

    cliente = inferir_cliente(usina)
    prioridade_raw = (representante.get("priorities_description") or "").strip().upper()
    prioridade = _FRACTTAL_PRIORIDADE_MAP.get(prioridade_raw, "Média")

    status_os_raw = str(representante.get("id_status_work_order", "")).strip()
    status_os = _FRACTTAL_STATUS_OS_MAP.get(status_os_raw, "")

    multiplos = len(tasks) > 1
    _titulo_prev, _equip_prev = _fracttal_detectar_preventiva(tasks, texto_usado) if multiplos else (None, None)
    if _equip_prev:
        equipamento = _equip_prev
    else:
        equipamento = "Múltiplas atividades" if multiplos else (representante.get("code") or texto_ativo or "Múltiplas atividades").strip()

    detalhe_hist = _fracttal_historico_detalhe(tasks)
    if alerta and detalhe_hist:
        alerta = f"{alerta}\n{detalhe_hist}"
    elif detalhe_hist:
        alerta = detalhe_hist

    return {
        "cliente": cliente,
        "usina": usina,
        "equipamento": equipamento,
        "descricao": (_titulo_prev if _titulo_prev
                       else (_fracttal_descricao_agregada(tasks) or f"OT {representante.get('wo_folio', '')} (Fracttal)")),
        "responsavel": tecnico_raw,
        "prazo": _fracttal_prazo_agregado(tasks),
        "prioridade": prioridade,
        # se a OS já nasce Finalizada/Cancelada na Fracttal (ex.: criada e
        # cancelada pelo operador antes do nosso sync sequer vê-la), o
        # status interno tem que refletir isso já na criação — senão essa
        # atividade nunca mais é revisitada pelo rodízio (que pula quem já
        # está Finalizada/Cancelada) e fica presa em "Em Aberto" pra sempre.
        "status": (_status_interno_esperado(status_os, "Em Aberto") or "Em Aberto"),
        "numeroOS": (representante.get("wo_folio") or "").strip(),
        "editor": "fracttal-sync",
        "statusOS": status_os,
        "observacoesOS": _fracttal_observacoes_agregadas(tasks),
        "linkOS": _fracttal_montar_link(representante),
        "statusTarefaOS": _fracttal_status_tarefa_agregado(tasks),
        "etiquetasOS": _fracttal_etiquetas_agregadas(tasks),
        "percentualOS": str(_fracttal_percentual_conclusao(tasks)),
        "statusGeralOS": _fracttal_status_geral(tasks),
        "detalhesEquipamentosOS": _fracttal_detalhes_equipamentos(tasks),
        "_alerta": alerta,
    }


@app.route("/fracttal-raw", methods=["GET"])
def fracttal_raw():
    """
    Endpoint de DIAGNÓSTICO — repassa query params direto pro /work_orders
    da Fracttal e devolve a resposta crua (sem mapear). Usado só pra
    confirmar nomes de parâmetros (status, paginação) antes de rodar
    sincronizações em lote. Protegido pelo mesmo WEBHOOK_SECRET.
    """
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        token = _fracttal_get_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        endpoint = request.args.get("endpoint", "").strip()
        folio = request.args.get("folio", "").strip()
        if endpoint:
            url = f"{FRACTTAL_API_BASE}/{endpoint.lstrip('/')}"
            params = {k: v for k, v in request.args.items() if k not in ("secret", "endpoint")}
        elif folio:
            url = f"{FRACTTAL_API_BASE}/work_orders/{folio}"
            params = {}
        else:
            url = f"{FRACTTAL_API_BASE}/work_orders"
            params = {k: v for k, v in request.args.items() if k != "secret"}
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:3000]
        return jsonify({"ok": True, "status_code": resp.status_code, "url_chamada": resp.url, "body": body})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/backfill-fracttal", methods=["POST", "GET"])
def backfill_fracttal():
    """
    Backfill histórico de OTs da Fracttal pro Painel de Atividades.
    Processa UMA PÁGINA por chamada (start/limit) — pensado pra ser chamado
    repetidas vezes pelo workflow do GitHub Actions, avançando o `start`,
    pra não estourar o timeout de 60s do Render numa carga grande.

    Query params:
      since      (default 2026-03-01T00:00:00-00:00)
      until      (opcional)
      ot_status  (default 1 = Em Processo)
      start      (default 0)
      limit      (default 100, teto de 100 — limite da própria Fracttal)
    """
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    since = request.args.get("since", "2026-03-01T00:00:00-00:00")
    until = request.args.get("until", "") or None
    ot_status = request.args.get("ot_status", "1")
    start = int(request.args.get("start", 0))
    limit = min(int(request.args.get("limit", 100)), 100)

    try:
        ots, total = _fracttal_listar_pagina(since=since, until=until, ot_status=ot_status,
                                              start=start, limit=limit)
    except Exception as e:
        log.error(f"[backfill-fracttal] Erro ao consultar Fracttal (start={start}): {e}")
        return jsonify({"ok": False, "error": str(e), "start": start}), 502

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
        os_existentes = {row[13].strip() for row in todos[1:] if len(row) > 13 and row[13].strip()}
    except Exception as e:
        log.error(f"[backfill-fracttal] Erro ao ler Painel de Atividades: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

    criadas, revisao_manual, erros = [], [], []
    revisao_folios_vistos = set()

    for folio, tasks in _fracttal_agrupar_por_wo(ots):
        if folio in os_existentes:
            continue  # já registrada (nesta página ou em página anterior do mesmo backfill)
        mapeado = _fracttal_mapear_grupo(tasks)
        if not mapeado:
            continue
        if mapeado.get("_revisao_manual"):
            if folio not in revisao_folios_vistos:
                revisao_folios_vistos.add(folio)
                revisao_manual.append({"wo_folio": folio, "motivo": mapeado["motivo"]})
            continue

        alerta = mapeado.pop("_alerta", None)
        mapeado["editor"] = "fracttal-backfill"
        try:
            novo_id = _criar_atividade_interna(ws=ws, todos=todos, enviar_notificacao=False, **mapeado)
            if alerta:
                _aplicar_update_campo_atividade(ws, len(todos), todos[-1], "historico", alerta,
                                                 "fracttal-backfill", append=True)
            criadas.append({"numeroOS": mapeado["numeroOS"], "id": novo_id, "itens": len(tasks)})
            os_existentes.add(mapeado["numeroOS"])
        except Exception as e:
            log.error(f"[backfill-fracttal] Erro ao criar atividade para OT {mapeado.get('numeroOS')}: {e}")
            erros.append(mapeado.get("numeroOS", "?"))

    proximo_start = start + limit
    log.info(f"[backfill-fracttal] start={start} total_geral={total} criadas={len(criadas)} "
             f"revisao_manual={len(revisao_manual)} erros={len(erros)}")
    return jsonify({
        "ok": True,
        "total_geral": total,
        "start": start,
        "limit": limit,
        "processados_nesta_pagina": len(ots),
        "proximo_start": proximo_start,
        "tem_mais": proximo_start < total,
        "criadas": criadas,
        "revisao_manual": revisao_manual,
        "erros": erros,
    })


@app.route("/completar-fracttal-backfill", methods=["POST", "GET"])
def completar_fracttal_backfill():
    """
    Complementa atividades já criadas (tipicamente pelo /backfill-fracttal
    antes da introdução dos campos statusOS/observacoesOS/linkOS/prazo
    corrigido) buscando cada OT individualmente na Fracttal por wo_folio
    (GET /work_orders/{folio}) e preenchendo os campos que faltam.

    Só mexe em linhas com editor == "fracttal-backfill" (ou "fracttal-sync")
    E que ainda não têm statusOS preenchido — não sobrescreve nada que já
    foi completado ou editado manualmente.
    """
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    limit = int(request.args.get("limit", 8))

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
        _garantir_headers_atividades(ws)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    token = _fracttal_get_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    atualizadas, erros, puladas = [], [], []
    processadas = 0

    for i, row in enumerate(todos[1:], start=2):
        if processadas >= limit:
            break
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        editor = row[12].strip()
        numero_os = row[13].strip()
        status_os_atual = row[14].strip()

        if editor not in ("fracttal-backfill", "fracttal-sync") or not numero_os or status_os_atual:
            continue

        processadas += 1
        try:
            resp = requests.get(f"{FRACTTAL_API_BASE}/work_orders/{numero_os}", headers=headers, timeout=20)
            resp.raise_for_status()
            body = resp.json()
            tasks = (body.get("data") or [])
            if not tasks:
                puladas.append({"numeroOS": numero_os, "motivo": "OT não encontrada na Fracttal"})
                continue

            representante = tasks[0]
            status_os_raw = str(representante.get("id_status_work_order", "")).strip()
            status_os = _FRACTTAL_STATUS_OS_MAP.get(status_os_raw, "")
            observacoes = _fracttal_observacoes_agregadas(tasks)
            link = _fracttal_montar_link(representante)
            prazo_novo = _fracttal_prazo_agregado(tasks)
            status_tarefa = _fracttal_status_tarefa_agregado(tasks)
            etiquetas = _fracttal_etiquetas_agregadas(tasks)

            # escreve os campos novos numa única chamada (evita estourar cota de escrita)
            ws.update(f"O{i}:S{i}", [[status_os, observacoes, link, status_tarefa, etiquetas]])
            prazo_atual = row[6].strip()
            if prazo_novo and prazo_novo != prazo_atual:
                ws.update_cell(i, 7, prazo_novo)
            if len(tasks) > 1:
                _, _equip_prev_check = _fracttal_detectar_preventiva(tasks)
                _equip_esperado = _equip_prev_check or "Múltiplas atividades"
            else:
                _equip_esperado = None
            if _equip_esperado and row[3].strip() != _equip_esperado:
                ws.update_cell(i, 4, _equip_esperado)
                detalhe = _fracttal_historico_detalhe(tasks)
                if detalhe:
                    hist_atual = row[ATIV_COL_HISTORICO - 1] if len(row) >= ATIV_COL_HISTORICO else ""
                    ws.update_cell(i, ATIV_COL_HISTORICO, f"{hist_atual}\n{detalhe}".strip() if hist_atual else detalhe)

            atualizadas.append({"linha": i, "numeroOS": numero_os, "statusOS": status_os, "itens": len(tasks)})
            time.sleep(1.2)  # respeita a cota de escrita por minuto do Google Sheets
        except Exception as e:
            log.error(f"[completar-fracttal-backfill] Erro na OT {numero_os}: {e}")
            erros.append({"numeroOS": numero_os, "erro": str(e)})

    log.info(f"[completar-fracttal-backfill] atualizadas={len(atualizadas)} puladas={len(puladas)} erros={len(erros)}")
    return jsonify({"ok": True, "atualizadas": atualizadas, "puladas": puladas, "erros": erros,
                     "processadas_nesta_chamada": processadas, "limit": limit})


@app.route("/corrigir-descricoes-multiplas", methods=["POST", "GET"])
def corrigir_descricoes_multiplas():
    """
    Corrige atividades multi-tarefa antigas (criadas antes da mudança de
    texto "Múltiplos equipamentos" -> "Múltiplas atividades" e do corte da
    descrição pro prefixo só): reconsulta a OS na Fracttal e reescreve
    equipamento + descrição no padrão atual.
    """
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    limit = int(request.args.get("limit", 8))

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    token = _fracttal_get_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    corrigidas, erros = [], []
    processadas = 0

    for i, row in enumerate(todos[1:], start=2):
        if processadas >= limit:
            break
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        equipamento_atual = row[3].strip()
        descricao_atual = row[4].strip()
        numero_os = row[13].strip()

        precisa = (equipamento_atual == "Múltiplos equipamentos") or (
            equipamento_atual == "Múltiplas atividades" and re.search(r"Múltiplas atividades \(\d+ itens\)", descricao_atual)
        )
        if not precisa or not numero_os:
            continue

        processadas += 1
        try:
            resp = requests.get(f"{FRACTTAL_API_BASE}/work_orders/{numero_os}", headers=headers, timeout=20)
            resp.raise_for_status()
            tasks = (resp.json().get("data") or [])
            if not tasks:
                continue
            nova_descricao = _fracttal_descricao_agregada(tasks)
            ws.update(f"D{i}:E{i}", [["Múltiplas atividades", nova_descricao]])
            corrigidas.append({"linha": i, "numeroOS": numero_os, "descricao": nova_descricao})
            time.sleep(1.0)
        except Exception as e:
            log.error(f"[corrigir-descricoes-multiplas] Erro na OT {numero_os}: {e}")
            erros.append({"numeroOS": numero_os, "erro": str(e)})

    log.info(f"[corrigir-descricoes-multiplas] corrigidas={len(corrigidas)} erros={len(erros)}")
    return jsonify({"ok": True, "corrigidas": corrigidas, "erros": erros,
                     "processadas_nesta_chamada": processadas, "limit": limit})


@app.route("/normalizar-usinas-clientes", methods=["POST", "GET"])
def normalizar_usinas_clientes():
    """
    Varre TODAS as atividades (independente de origem/editor) e corrige
    usina/cliente pra forma canônica do catálogo sempre que divergirem —
    resolve nomenclaturas duplicadas (ex: "Sete Lagoas 2" vs "Sete Lagoas")
    que escaparam da canonização por terem sido criadas manualmente antes
    da integração Fracttal existir.
    """
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    corrigidas = []
    for i, row in enumerate(todos[1:], start=2):
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        usina_atual = row[2].strip()
        cliente_atual = row[1].strip()
        if not usina_atual or usina_atual in ("Geral", "Administrativo"):
            continue

        canonica = canonizar_usina(usina_atual)
        if not canonica:
            continue  # não reconhecida — não mexe (pode ser usina legítima fora do catálogo atual)

        cliente_correto = inferir_cliente(canonica) or cliente_atual
        if canonica != usina_atual or cliente_correto != cliente_atual:
            ws.update(f"B{i}:C{i}", [[cliente_correto, canonica]])
            corrigidas.append({"linha": i, "usina_antes": usina_atual, "usina_depois": canonica,
                                "cliente_antes": cliente_atual, "cliente_depois": cliente_correto})
            time.sleep(0.8)

    log.info(f"[normalizar-usinas-clientes] corrigidas={len(corrigidas)}")
    return jsonify({"ok": True, "corrigidas": corrigidas})


@app.route("/completar-campos-v3-fracttal", methods=["POST", "GET"])
def completar_campos_v3_fracttal():
    """
    Preenche percentualOS/statusGeralOS/detalhesEquipamentosOS em atividades
    da Fracttal que já foram completadas pelas versões anteriores (já têm
    statusTarefaOS) mas ainda não têm esses 3 campos mais novos.
    """
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    limit = int(request.args.get("limit", 6))

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
        _garantir_headers_atividades(ws)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    token = _fracttal_get_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    atualizadas, erros = [], []
    processadas = 0

    for i, row in enumerate(todos[1:], start=2):
        if processadas >= limit:
            break
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        editor = row[12].strip()
        numero_os = row[13].strip()
        percentual_atual = row[20].strip()

        if editor not in ("fracttal-backfill", "fracttal-sync", "claude-chat") or not numero_os:
            continue
        if percentual_atual:
            continue

        processadas += 1
        try:
            resp = requests.get(f"{FRACTTAL_API_BASE}/work_orders/{numero_os}", headers=headers, timeout=20)
            resp.raise_for_status()
            tasks = (resp.json().get("data") or [])
            if not tasks:
                continue

            percentual = str(_fracttal_percentual_conclusao(tasks))
            status_geral = _fracttal_status_geral(tasks)
            detalhes = _fracttal_detalhes_equipamentos(tasks)
            ws.update(f"U{i}:W{i}", [[percentual, status_geral, detalhes]])

            atualizadas.append({"linha": i, "numeroOS": numero_os, "percentualOS": percentual,
                                 "statusGeralOS": status_geral})
            time.sleep(1.2)
        except Exception as e:
            log.error(f"[completar-campos-v3-fracttal] Erro na OT {numero_os}: {e}")
            erros.append({"numeroOS": numero_os, "erro": str(e)})

    log.info(f"[completar-campos-v3-fracttal] atualizadas={len(atualizadas)} erros={len(erros)}")
    return jsonify({"ok": True, "atualizadas": atualizadas, "erros": erros,
                     "processadas_nesta_chamada": processadas, "limit": limit})


@app.route("/completar-campos-v2-fracttal", methods=["POST", "GET"])
def completar_campos_v2_fracttal():
    """
    Reprocessa atividades da Fracttal que já têm statusOS preenchido (então
    o /completar-fracttal-backfill as ignora) mas ainda não têm os campos
    mais novos: statusTarefaOS, etiquetasOS, e a correção de "Múltiplos
    equipamentos" quando a OS tem mais de uma tarefa. Existe só pra
    completar o que ficou faltando em atividades criadas antes desses
    campos existirem.
    """
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    limit = int(request.args.get("limit", 6))

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
        _garantir_headers_atividades(ws)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    token = _fracttal_get_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    atualizadas, erros = [], []
    processadas = 0

    for i, row in enumerate(todos[1:], start=2):
        if processadas >= limit:
            break
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        editor = row[12].strip()
        numero_os = row[13].strip()
        status_os_atual = row[14].strip()
        status_tarefa_atual = row[17].strip()

        if editor not in ("fracttal-backfill", "fracttal-sync", "claude-chat") or not numero_os:
            continue
        if not status_os_atual or status_tarefa_atual:
            continue  # ou ainda não foi completado pelo v1, ou já tem os campos novos

        processadas += 1
        try:
            resp = requests.get(f"{FRACTTAL_API_BASE}/work_orders/{numero_os}", headers=headers, timeout=20)
            resp.raise_for_status()
            tasks = (resp.json().get("data") or [])
            if not tasks:
                continue

            status_tarefa = _fracttal_status_tarefa_agregado(tasks)
            etiquetas = _fracttal_etiquetas_agregadas(tasks)
            ws.update(f"R{i}:S{i}", [[status_tarefa, etiquetas]])

            if len(tasks) > 1:
                _, _equip_prev_check = _fracttal_detectar_preventiva(tasks)
                _equip_esperado = _equip_prev_check or "Múltiplas atividades"
            else:
                _equip_esperado = None
            if _equip_esperado and row[3].strip() != _equip_esperado:
                ws.update_cell(i, 4, _equip_esperado)
                detalhe = _fracttal_historico_detalhe(tasks)
                if detalhe:
                    hist_atual = row[ATIV_COL_HISTORICO - 1] if len(row) >= ATIV_COL_HISTORICO else ""
                    ws.update_cell(i, ATIV_COL_HISTORICO, f"{hist_atual}\n{detalhe}".strip() if hist_atual else detalhe)

            atualizadas.append({"linha": i, "numeroOS": numero_os, "itens": len(tasks),
                                 "statusTarefaOS": status_tarefa})
            time.sleep(1.2)
        except Exception as e:
            log.error(f"[completar-campos-v2-fracttal] Erro na OT {numero_os}: {e}")
            erros.append({"numeroOS": numero_os, "erro": str(e)})

    log.info(f"[completar-campos-v2-fracttal] atualizadas={len(atualizadas)} erros={len(erros)}")
    return jsonify({"ok": True, "atualizadas": atualizadas, "erros": erros,
                     "processadas_nesta_chamada": processadas, "limit": limit})


@app.route("/atualizar-links-fracttal", methods=["POST", "GET"])
def atualizar_links_fracttal():
    """
    Reprocessa SÓ o campo linkOS de atividades vindas da Fracttal, usando
    a URL pública por OT (add-on Share TOs). Ignora se já tem statusOS
    preenchido (diferente do /completar-fracttal-backfill) — existe
    justamente pra corrigir links antigos que ficaram com o fallback
    genérico (tela de OTs) em vez do link direto.
    """
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    limit = int(request.args.get("limit", 8))

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
        _garantir_headers_atividades(ws)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    atualizadas, erros = [], []
    processadas = 0

    for i, row in enumerate(todos[1:], start=2):
        if processadas >= limit:
            break
        if len(row) < 17:
            row = row + [""] * (17 - len(row))
        editor = row[12].strip()
        numero_os = row[13].strip()
        link_atual = row[16].strip()

        if editor not in ("fracttal-backfill", "fracttal-sync") or not numero_os:
            continue
        if link_atual and link_atual != FRACTTAL_WEB_BASE:
            continue  # já tem link direto, não mexe

        processadas += 1
        try:
            link_novo = _fracttal_montar_link({"wo_folio": numero_os})
            if link_novo and link_novo != link_atual:
                ws.update_cell(i, 17, link_novo)
                atualizadas.append({"linha": i, "numeroOS": numero_os, "link": link_novo})
            time.sleep(1.2)
        except Exception as e:
            log.error(f"[atualizar-links-fracttal] Erro na OT {numero_os}: {e}")
            erros.append({"numeroOS": numero_os, "erro": str(e)})

    log.info(f"[atualizar-links-fracttal] atualizadas={len(atualizadas)} erros={len(erros)}")
    return jsonify({"ok": True, "atualizadas": atualizadas, "erros": erros,
                     "processadas_nesta_chamada": processadas, "limit": limit})


def _sync_fracttal_core(desde_horas=8):
    """
    Núcleo da DESCOBERTA: busca OTs recentes na Fracttal e cria atividades
    novas pra qualquer uma que ainda não esteja no Painel de Atividades.
    Só isso — não recheca status de OSs já existentes (isso é
    responsabilidade exclusiva da auditoria, _auditoria_consistencia_os_core,
    que faz esse trabalho de forma mais completa e sem duplicar lógica).

    Separar descoberta de "manter status em dia" é proposital: descobrir
    OS nova não é urgente (uma janela de horas é tranquila), enquanto
    manter o status atualizado precisa rodar com frequência — juntar os
    dois na mesma chamada só deixava tudo mais pesado e lento sem
    necessidade.
    """
    try:
        ots = _fracttal_listar_ots_recentes(desde_horas=desde_horas)
    except Exception as e:
        log.error(f"[sync-fracttal] Erro ao consultar Fracttal: {e}")
        return {"ok": False, "error": str(e)}, 502

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
        _garantir_headers_atividades(ws)
        os_existentes = {row[13].strip() for row in todos[1:] if len(row) > 13 and row[13].strip()}
    except Exception as e:
        log.error(f"[sync-fracttal] Erro ao ler Painel de Atividades: {e}")
        return {"ok": False, "error": str(e)}, 500

    criadas, revisao_manual, erros = [], [], []
    revisao_folios_vistos = set()

    for folio, tasks in _fracttal_agrupar_por_wo(ots):
        if folio in os_existentes:
            continue  # já registrada — evita duplicata (mesma OT, outra linha de tarefa/componente)
        mapeado = _fracttal_mapear_grupo(tasks)
        if not mapeado:
            continue  # OT de outro cliente/supervisor, totalmente fora de escopo
        if mapeado.get("_revisao_manual"):
            if folio not in revisao_folios_vistos:
                revisao_folios_vistos.add(folio)
                revisao_manual.append({"wo_folio": folio, "motivo": mapeado["motivo"]})
            continue

        alerta = mapeado.pop("_alerta", None)
        try:
            novo_id = _criar_atividade_interna(ws=ws, todos=todos, enviar_notificacao=False, **mapeado)
            if alerta:
                _aplicar_update_campo_atividade(ws, len(todos), todos[-1], "historico", alerta,
                                                 "fracttal-sync", append=True)
            criadas.append({"numeroOS": mapeado["numeroOS"], "id": novo_id, "itens": len(tasks), "alerta": alerta,
                             "usina": mapeado["usina"], "cliente": mapeado["cliente"]})
            os_existentes.add(mapeado["numeroOS"])
        except Exception as e:
            log.error(f"[sync-fracttal] Erro ao criar atividade para OT {mapeado.get('numeroOS')}: {e}")
            erros.append(mapeado.get("numeroOS", "?"))

    # um único push resumido pra todas as OSs novas descobertas nessa
    # rodada — restaurado 15/07/2026 (tinha sido perdido numa reconstrução
    # desta função por outra sessão, reintroduzindo notificação em
    # duplicidade/spam por item que já tinha sido corrigido antes).
    if criadas:
        try:
            if len(criadas) == 1:
                c = criadas[0]
                enviar_push(
                    titulo=f"🆕 Nova OS Fracttal — {c['numeroOS']} — {c['usina']}",
                    corpo=f"{c['cliente']}",
                    tipo="fracttal_nova_os",
                    url=f"https://fred-alexandrino.github.io/PAINELDEFALHAS/?atividade={c['id']}",
                )
            else:
                usinas_resumo = ", ".join(sorted(set(c["usina"] for c in criadas))[:5])
                enviar_push(
                    titulo=f"🆕 {len(criadas)} novas OSs na Fracttal",
                    corpo=f"Usinas: {usinas_resumo}{'...' if len(set(c['usina'] for c in criadas)) > 5 else ''}",
                    tipo="fracttal_nova_os",
                    url="https://fred-alexandrino.github.io/PAINELDEFALHAS/",
                )
        except Exception as e:
            log.error(f"[sync-fracttal] Falha ao enviar push resumido de novas OSs: {e}")

    log.info(f"[sync-fracttal] criadas={len(criadas)} revisao_manual={len(revisao_manual)} erros={len(erros)}")
    return {"ok": True, "criadas": criadas, "revisao_manual": revisao_manual, "erros": erros}, 200



# ── Correção retroativa de fuso horário (uso único) ─────────────────────
# Antes do deploy que introduziu agora_br(), todo timestamp gravado no
# Painel de Atividades usava datetime.now() puro do servidor (UTC), mas era
# exibido como se já fosse horário de Brasília (GMT-3) — ficando 3h
# adiantado. Este endpoint corrige retroativamente as colunas afetadas
# (dataCriacao, dataConclusao, historico, ultimaVerificacaoOS) só nas
# entradas anteriores ao corte (momento em que o deploy corrigido entrou
# no ar). Entradas iguais/depois do corte já estão corretas e são
# ignoradas. Protegido por WEBHOOK_SECRET; sempre roda em modo simulação
# (dry_run=true) por padrão — precisa de ?apply=true explícito pra gravar.
_HOJE_DEPLOY = datetime(2026, 7, 8).date()
# O deploy da correção foi ao ar por volta de 21:05-21:09 UTC (= 18:05-18:09
# em Brasília, mesmo instante real). Uma entrada ANTIGA (com bug) grava o
# relógio UTC bruto como se fosse local — então seu valor literal só pode
# ir até, no máximo, o instante em que o código antigo parou de rodar
# (~21:15 UTC). Uma entrada NOVA (já corrigida) grava o horário real de
# Brasília — então seu valor literal só pode começar a partir do instante
# em que o deploy entrou no ar (~18:05 BR). Como os dois usam o mesmo
# "relógio de parede" pra escrever a célula, a faixa [18:05, 21:15] no dia
# do deploy é onde as duas interpretações se sobrepõem e não dá pra
# decidir com segurança só pelo valor — por isso fica de fora da correção
# automática e é sinalizada pra revisão manual.
_JANELA_INICIO = datetime(2026, 7, 8, 18, 5, 0).time()
_JANELA_FIM = datetime(2026, 7, 8, 21, 15, 0).time()

_HIST_LINHA_RE = re.compile(r'^(\d{2}/\d{2}/\d{4}) (\d{2}:\d{2})(:\d{2})? - ')


def _classificar_ts_fuso(ts_str, fmt):
    """Classifica um timestamp gravado antes da correção de fuso horário.

    Regra extra: uma entrada NOVA (corrigida) nunca pode ter horário no
    futuro em relação a "agora" (horário real de Brasília no momento da
    checagem) — se tiver, só pode ser uma entrada ANTIGA (valor bruto de
    UTC, que naturalmente "parece" mais tarde). Isso desambiguiza a maior
    parte da janela conforme o tempo passa.

    Retorna ('antigo', novo_valor) | ('ambiguo', ts_str) | ('atual', ts_str) | ('invalido', ts_str)
    """
    try:
        dt = datetime.strptime(ts_str, fmt)
    except Exception:
        return "invalido", ts_str

    if dt.date() < _HOJE_DEPLOY:
        return "antigo", (dt - timedelta(hours=3)).strftime(fmt)

    if dt.date() == _HOJE_DEPLOY:
        if dt.time() < _JANELA_INICIO:
            return "antigo", (dt - timedelta(hours=3)).strftime(fmt)
        agora_time = agora_br().time()
        if dt.time() > agora_time:
            # horário "no futuro" em relação a agora só é possível se for
            # valor bruto de UTC (entrada antiga) — uma entrada nova jamais
            # gravaria um horário à frente do relógio real de Brasília.
            return "antigo", (dt - timedelta(hours=3)).strftime(fmt)
        if dt.time() <= _JANELA_FIM:
            return "ambiguo", ts_str
        return "atual", ts_str

    return "atual", ts_str


@app.route("/travar-fuso-retroativo", methods=["POST", "GET"])
def travar_fuso_retroativo():
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    _gravar_trava("fuso_retroativo_concluido", "true")
    return jsonify({"ok": True, "trava": "ativada"}), 200


@app.route("/fix-pontual-9173-9154", methods=["POST", "GET"])
def fix_pontual_9173_9154():
    """Endpoint de uso único: reescreve o historico limpo e correto das OSs
    9173 e 9154, que ficaram com uma linha de log duplicada/suja por causa
    de uma tentativa de correção manual que esbarrou num bug pré-existente
    do endpoint /atualizar-campo-atividade (ele reloga a alteração usando
    dados em cache antigos ao invés de sobrescrever de forma limpa)."""
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    correcoes = {
        "9173": "08/07/2026 15:55 - Atividade criada por fracttal-sync.",
        "9154": ("08/07/2026 15:55 - Atividade criada por fracttal-sync.\n"
                 "08/07/2026 17:53 - Status na OS (Fracttal) atualizado: "
                 "\"Em Processo\" → \"Em Revisão\", 0% → 100% (Concluída)."),
    }

    ws = get_atividades_sheet()
    todos = ws.get_all_values()
    batch_updates = []
    for i, row in enumerate(todos[1:], start=2):
        if len(row) < 14:
            continue
        numero_os = row[13].strip()
        if numero_os in correcoes:
            batch_updates.append({
                "range": gspread.utils.rowcol_to_a1(i, 10),
                "values": [["08/07/2026 15:55:00"]],
            })
            batch_updates.append({
                "range": gspread.utils.rowcol_to_a1(i, 12),
                "values": [[correcoes[numero_os]]],
            })

    if batch_updates:
        ws.batch_update(batch_updates, value_input_option="RAW")

    return jsonify({"ok": True, "celulas_corrigidas": len(batch_updates)}), 200


@app.route("/corrigir-fuso-retroativo", methods=["POST", "GET"])
def corrigir_fuso_retroativo():
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    aplicar = request.args.get("apply", "false").lower() == "true"
    forcar_raw = request.args.get("forcar", "").strip()
    forcar_set = {x.strip() for x in forcar_raw.split(",") if x.strip()} if forcar_raw else set()
    ignorar_trava = request.args.get("ignorar_trava", "false").lower() == "true"

    if aplicar and not ignorar_trava:
        try:
            if _ler_trava("fuso_retroativo_concluido") == "true":
                return jsonify({
                    "ok": False,
                    "error": ("Correção retroativa já foi concluída em 2026-07-08 e está travada "
                              "pra evitar redescontar horas já corrigidas. Use ?ignorar_trava=true "
                              "só se tiver certeza absoluta do que está fazendo.")
                }), 409
        except Exception:
            pass  # se a aba de config falhar por algum motivo, não bloqueia a leitura normal (dry-run)

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
        _garantir_headers_atividades(ws)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    alteracoes = []
    ambiguos = []
    batch_updates = []
    for i, row in enumerate(todos[1:], start=2):
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        numero_os = row[13].strip()
        id_atividade = row[0].strip()
        updates = {}
        ambiguos_linha = []
        forcar_linha = bool(forcar_set) and (numero_os in forcar_set or id_atividade in forcar_set)

        def _checar(campo_nome, valor, fmt, col):
            if not valor:
                return
            estado, novo = _classificar_ts_fuso(valor, fmt)
            if estado == "ambiguo" and forcar_linha:
                dt_forcado = datetime.strptime(valor, fmt)
                estado, novo = "antigo", (dt_forcado - timedelta(hours=3)).strftime(fmt)
            if estado == "antigo":
                updates[col] = novo
            elif estado == "ambiguo":
                ambiguos_linha.append({"campo": campo_nome, "valor": valor})

        _checar("dataCriacao", row[9].strip(), '%d/%m/%Y %H:%M:%S', 10)
        _checar("dataConclusao", row[10].strip(), '%d/%m/%Y %H:%M:%S', 11)
        _checar("ultimaVerificacaoOS", row[23].strip(), '%Y-%m-%dT%H:%M:%S', 24)

        # historico (col 12) — multilinha, cada linha pode começar com timestamp
        hist = row[11]
        if hist:
            linhas_novas = []
            hist_mudou = False
            for linha_h in hist.split("\n"):
                m = _HIST_LINHA_RE.match(linha_h)
                if m:
                    data_str, hora_str, seg = m.group(1), m.group(2), m.group(3) or ""
                    ts_str = f"{data_str} {hora_str}{seg}"
                    fmt = '%d/%m/%Y %H:%M:%S' if seg else '%d/%m/%Y %H:%M'
                    estado, novo_ts = _classificar_ts_fuso(ts_str, fmt)
                    if estado == "ambiguo" and forcar_linha:
                        dt_forcado = datetime.strptime(ts_str, fmt)
                        estado, novo_ts = "antigo", (dt_forcado - timedelta(hours=3)).strftime(fmt)
                    if estado == "antigo":
                        linha_h = novo_ts + linha_h[len(ts_str):]
                        hist_mudou = True
                    elif estado == "ambiguo":
                        ambiguos_linha.append({"campo": "historico", "valor": ts_str, "linha_texto": linha_h[:80]})
                linhas_novas.append(linha_h)
            if hist_mudou:
                updates[12] = "\n".join(linhas_novas)

        if updates:
            alteracoes.append({"linha": i, "id": id_atividade, "numeroOS": numero_os,
                                "colunas_alteradas": list(updates.keys())})
            if aplicar:
                for col, novo_val in updates.items():
                    batch_updates.append({
                        "range": gspread.utils.rowcol_to_a1(i, col),
                        "values": [[novo_val]],
                    })
        if ambiguos_linha:
            ambiguos.append({"linha": i, "id": id_atividade, "numeroOS": numero_os, "campos": ambiguos_linha})

    if aplicar and batch_updates:
        # grava tudo em poucas chamadas (lotes de 200 células) em vez de uma
        # chamada por célula — evita estourar o timeout do Gunicorn (60s)
        TAMANHO_LOTE = 200
        for k in range(0, len(batch_updates), TAMANHO_LOTE):
            ws.batch_update(batch_updates[k:k + TAMANHO_LOTE], value_input_option="RAW")

    return jsonify({"ok": True, "aplicado": aplicar, "linhas_afetadas": len(alteracoes),
                     "detalhes": alteracoes, "ambiguos": ambiguos}), 200


def _get_config_sheet():
    """Aba minúscula usada só pra guardar flags de controle (ex.: travas de
    operações de uso único). Cria a aba se ainda não existir."""
    sh = get_atividades_sheet().spreadsheet
    try:
        return sh.worksheet("_Sistema")
    except gspread.exceptions.WorksheetNotFound:
        ws_cfg = sh.add_worksheet(title="_Sistema", rows=20, cols=4)
        ws_cfg.update("A1", [["chave", "valor"]])
        return ws_cfg


def _ler_trava(chave):
    ws_cfg = _get_config_sheet()
    valores = ws_cfg.get_all_values()
    for row in valores[1:]:
        if row and row[0].strip() == chave:
            return row[1].strip() if len(row) > 1 else ""
    return ""


def _gravar_trava(chave, valor):
    ws_cfg = _get_config_sheet()
    valores = ws_cfg.get_all_values()
    for i, row in enumerate(valores[1:], start=2):
        if row and row[0].strip() == chave:
            ws_cfg.update_cell(i, 2, valor)
            return
    ws_cfg.append_row([chave, valor])


# ══════════════════════════════════════════════════════════════════════
# COMPROMISSOS (Boletim de Medição, Relatório de Performance, Relatório
# PCM) — checklist de prazos recorrentes por cliente/usina, com engine
# de cálculo de dia útil/dia fixo e alertas push automáticos.
# ══════════════════════════════════════════════════════════════════════

COMPROMISSO_ETAPAS = {
    "BM": ["Envio do BM", "Aprovação do Cliente", "Emissão da NF"],
    "RelatorioPerformance": ["Envio do Relatório de Performance"],
    "RelatorioPCM": ["Envio do Relatório de Manutenção (PCM)"],
}

COMPROMISSO_LABEL = {
    "BM": "Boletim de Medição",
    "RelatorioPerformance": "Relatório de Performance",
    "RelatorioPCM": "Relatório de Manutenção (PCM)",
}


def _feriados_nacionais_brasil(ano):
    """Feriados nacionais fixos + móveis (baseados na Páscoa, algoritmo
    de Gauss). Não cobre feriados estaduais/municipais — só o suficiente
    pra não antecipar prazo em cima de feriado nacional por engano."""
    a = ano
    # Páscoa (algoritmo de Meeus/Jones/Butcher)
    y = a
    g = y % 19
    c = y // 100
    h = (c - c // 4 - (8 * c + 13) // 25 + 19 * g + 15) % 30
    i = h - (h // 28) * (1 - (h // 28) * (29 // (h + 1)) * ((21 - g) // 11))
    j = (y + y // 4 + i + 2 - c + c // 4) % 7
    l = i - j
    mes = 3 + (l + 40) // 44
    dia = l + 28 - 31 * (mes // 4)
    pascoa = datetime(y, mes, dia)

    fixos = [
        datetime(a, 1, 1), datetime(a, 4, 21), datetime(a, 5, 1),
        datetime(a, 9, 7), datetime(a, 10, 12), datetime(a, 11, 2),
        datetime(a, 11, 15), datetime(a, 12, 25),
    ]
    moveis = [
        pascoa - timedelta(days=47),  # carnaval segunda
        pascoa - timedelta(days=46),  # carnaval terça
        pascoa - timedelta(days=2),   # sexta-feira santa
        pascoa + timedelta(days=60),  # corpus christi
    ]
    return {d.date() for d in (fixos + moveis)}


def _e_dia_util(dt):
    return dt.weekday() < 5 and dt.date() not in _feriados_nacionais_brasil(dt.year)


def _enesimo_dia_util(ano, mes, n):
    dt = datetime(ano, mes, 1)
    contados = 0
    while True:
        if _e_dia_util(dt):
            contados += 1
            if contados == n:
                return dt
        dt += timedelta(days=1)
        if dt.month != mes:  # segurança: não vaza pro mês seguinte
            return dt - timedelta(days=1)


def _ultimo_dia_do_mes(ano, mes):
    if mes == 12:
        prox = datetime(ano + 1, 1, 1)
    else:
        prox = datetime(ano, mes + 1, 1)
    return prox - timedelta(days=1)


def _ultimo_dia_util(ano, mes):
    dt = _ultimo_dia_do_mes(ano, mes)
    while not _e_dia_util(dt):
        dt -= timedelta(days=1)
    return dt


def _dia_fixo_com_antecipacao(ano, mes, dia):
    ultimo = _ultimo_dia_do_mes(ano, mes).day
    dt = datetime(ano, mes, min(dia, ultimo))
    while not _e_dia_util(dt):
        dt -= timedelta(days=1)
    return dt


def _calcular_prazo_compromisso(regra_tipo, regra_valor, ano, mes):
    regra_valor = int(regra_valor)
    if regra_tipo == "nDiaUtil":
        return _enesimo_dia_util(ano, mes, regra_valor)
    if regra_tipo == "diaFixo":
        return _dia_fixo_com_antecipacao(ano, mes, regra_valor)
    if regra_tipo == "diaAoUltimoUtil":
        return _ultimo_dia_util(ano, mes)
    raise ValueError(f"regra_tipo desconhecido: {regra_tipo}")


def _get_compromissos_regras_sheet():
    sh = get_atividades_sheet().spreadsheet
    try:
        return sh.worksheet("_ComprometimentosRegras")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="_ComprometimentosRegras", rows=50, cols=8)
        ws.update("A1", [["ID", "Tipo", "Cliente", "Usina", "RegraTipo", "RegraValor", "Ativo"]])
        # Seed inicial — só os clientes já mapeados no painel, regra de BM
        # tirada do calendário de emissão enviado pelo Fred (13/07/2026).
        seed = [
            ["1", "BM", "RENOGRID", "", "diaAoUltimoUtil", "30", "TRUE"],
            ["2", "BM", "THOPEN", "", "diaFixo", "15", "TRUE"],
            ["3", "BM", "2C Energia", "", "nDiaUtil", "5", "TRUE"],
            ["4", "BM", "GD Energy", "", "nDiaUtil", "5", "TRUE"],
            ["5", "BM", "Alves Lima", "", "nDiaUtil", "5", "TRUE"],
        ]
        ws.append_rows(seed)
        return ws


def _get_compromissos_sheet():
    sh = get_atividades_sheet().spreadsheet
    try:
        return sh.worksheet("Compromissos")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="Compromissos", rows=200, cols=12)
        ws.update("A1", [["ID", "Tipo", "Cliente", "Usina", "Competencia", "DataLimite",
                           "Etapas", "EtapasConcluidas", "Status", "DataCriacao",
                           "DataConclusao", "Historico"]])
        return ws


def _proximo_id_compromisso(todos):
    maior = 0
    for row in todos[1:]:
        if row and row[0].strip().isdigit():
            maior = max(maior, int(row[0].strip()))
    return str(maior + 1)


def _status_compromisso(etapas_concluidas, data_limite, hoje):
    concluidas = [e for e in etapas_concluidas if e]
    if len(concluidas) == len(etapas_concluidas):
        return "Concluído"
    if hoje.date() > data_limite.date() and not etapas_concluidas[0]:
        return "Atrasado"
    if concluidas:
        return "Em Andamento"
    return "Pendente"


def _gerar_compromissos_mes_atual_se_necessario():
    """Versão econômica de _gerar_compromissos_mes_atual(): só faz a
    varredura pesada (2 leituras completas de planilha) 1x por competência,
    usando a trava em _Sistema (leitura pequena e barata) pra decidir se
    vale a pena. Sem isso, todo GET /compromissos gastava 2 leituras
    completas — e como o frontend recarregava a lista a cada clique de
    checkbox, isso estourou a cota de leitura do Google Sheets (429) e
    derrubou o resto do painel junto (13/07/2026)."""
    agora = agora_br()
    competencia = agora.strftime("%m/%Y")
    ja_gerado = _ler_trava("compromissos_gerados_em")
    if ja_gerado == competencia:
        return []
    criados = _gerar_compromissos_mes_atual()
    _gravar_trava("compromissos_gerados_em", competencia)
    return criados


def _gerar_compromissos_mes_atual():
    """Idempotente: cria o card do mês corrente pra cada regra ativa que
    ainda não tenha um card gerado nessa competência. Fecha sozinho o
    ciclo anterior (o card antigo simplesmente fica com seu status real —
    Concluído ou Atrasado — e um novo é aberto pra competência atual)."""
    ws_regras = _get_compromissos_regras_sheet()
    regras = ws_regras.get_all_values()[1:]

    ws_comp = _get_compromissos_sheet()
    todos = ws_comp.get_all_values()
    existentes = {(r[1], r[2], r[3], r[4]) for r in todos[1:] if len(r) >= 5}

    agora = agora_br()
    competencia = agora.strftime("%m/%Y")
    criados = []

    for r in regras:
        if len(r) < 7 or r[6].strip().upper() != "TRUE":
            continue
        _id, tipo, cliente, usina, regra_tipo, regra_valor = r[0], r[1], r[2], r[3], r[4], r[5]
        chave = (tipo, cliente, usina, competencia)
        if chave in existentes:
            continue
        try:
            prazo = _calcular_prazo_compromisso(regra_tipo, regra_valor, agora.year, agora.month)
        except Exception as e:
            log.error(f"[Compromissos] Erro ao calcular prazo pra regra {_id}: {e}")
            continue

        etapas = COMPROMISSO_ETAPAS.get(tipo, ["Envio"])
        novo_id = _proximo_id_compromisso(todos)
        linha = [novo_id, tipo, cliente, usina, competencia, prazo.strftime("%d/%m/%Y"),
                  json.dumps(etapas, ensure_ascii=False), json.dumps([""] * len(etapas)),
                  "Pendente", agora.strftime("%d/%m/%Y %H:%M:%S"), "",
                  f"{agora.strftime('%d/%m/%Y %H:%M')} - Card criado automaticamente pra competência {competencia}."]
        ws_comp.append_row(linha)
        todos.append(linha)
        existentes.add(chave)
        criados.append({"id": novo_id, "tipo": tipo, "cliente": cliente, "competencia": competencia,
                         "dataLimite": prazo.strftime("%d/%m/%Y")})

    return criados


def _listar_compromissos_core():
    _gerar_compromissos_mes_atual_se_necessario()
    ws = _get_compromissos_sheet()
    todos = ws.get_all_values()
    agora = agora_br()
    resultado = []
    for row in todos[1:]:
        if len(row) < 12 or not row[0].strip():
            continue
        try:
            data_limite = datetime.strptime(row[5].strip(), "%d/%m/%Y")
        except Exception:
            continue
        etapas = json.loads(row[6]) if row[6] else []
        etapas_concluidas = json.loads(row[7]) if row[7] else []
        status_calc = _status_compromisso(etapas_concluidas, data_limite, agora)
        dias_restantes = (data_limite.date() - agora.date()).days
        resultado.append({
            "id": row[0], "tipo": row[1], "tipoLabel": COMPROMISSO_LABEL.get(row[1], row[1]),
            "cliente": row[2], "usina": row[3], "competencia": row[4],
            "dataLimite": row[5], "diasRestantes": dias_restantes,
            "etapas": etapas, "etapasConcluidas": etapas_concluidas,
            "status": status_calc, "dataConclusao": row[10],
        })
    # Mais urgente primeiro: atrasado > vence antes > já concluído por último
    ordem_status = {"Atrasado": 0, "Pendente": 1, "Em Andamento": 1, "Concluído": 2}
    resultado.sort(key=lambda c: (ordem_status.get(c["status"], 1), c["diasRestantes"]))
    return resultado


@app.route("/compromissos", methods=["GET"])
def listar_compromissos():
    try:
        return jsonify({"ok": True, "compromissos": _listar_compromissos_core()}), 200
    except Exception as e:
        log.error(f"[Compromissos] Erro ao listar: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/compromissos/marcar-etapa", methods=["POST"])
def marcar_etapa_compromisso():
    try:
        body = request.get_json(force=True) or {}
        comp_id = str(body.get("id", "")).strip()
        etapa_index = int(body.get("etapaIndex", -1))
        concluida = bool(body.get("concluida", True))
        editor = body.get("editor", "desconhecido")
        if not comp_id or etapa_index < 0:
            return jsonify({"ok": False, "error": "id e etapaIndex são obrigatórios"}), 400

        ws = _get_compromissos_sheet()
        todos = ws.get_all_values()
        linha_idx, linha = None, None
        for i, row in enumerate(todos[1:], start=2):
            if row and row[0].strip() == comp_id:
                linha_idx, linha = i, row
                break
        if not linha_idx:
            return jsonify({"ok": False, "error": f"compromisso {comp_id} não encontrado"}), 404

        etapas = json.loads(linha[6]) if linha[6] else []
        etapas_concluidas = json.loads(linha[7]) if len(linha) > 7 and linha[7] else [""] * len(etapas)
        if etapa_index >= len(etapas):
            return jsonify({"ok": False, "error": "etapaIndex fora do intervalo"}), 400

        agora = agora_br()
        etapas_concluidas[etapa_index] = agora.strftime("%d/%m/%Y %H:%M") if concluida else ""

        try:
            data_limite = datetime.strptime(linha[5].strip(), "%d/%m/%Y")
        except Exception:
            data_limite = agora
        novo_status = _status_compromisso(etapas_concluidas, data_limite, agora)

        data_conclusao = agora.strftime("%d/%m/%Y %H:%M:%S") if novo_status == "Concluído" else ""

        nome_etapa = etapas[etapa_index]
        acao = "concluída" if concluida else "reaberta"
        entry = f"{agora.strftime('%d/%m/%Y %H:%M')} - Etapa \"{nome_etapa}\" {acao} por {editor}."
        hist_atual = linha[11] if len(linha) > 11 else ""
        novo_hist = f"{hist_atual}\n{entry}".strip() if hist_atual else entry

        ws.update(f"H{linha_idx}:L{linha_idx}", [[
            json.dumps(etapas_concluidas, ensure_ascii=False), novo_status,
            linha[9] if len(linha) > 9 else agora.strftime("%d/%m/%Y %H:%M:%S"),
            data_conclusao, novo_hist,
        ]])

        return jsonify({"ok": True, "id": comp_id, "status": novo_status,
                         "etapasConcluidas": etapas_concluidas}), 200
    except Exception as e:
        log.error(f"[Compromissos] Erro ao marcar etapa: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


def _verificar_compromissos_se_necessario():
    """Piggyback no /sync-fracttal: roda 1x por dia na janela 07:00-08:30
    (mesma janela alargada dos comunicados, mesmo motivo — cold-start do
    Render podia consumir a janela de 9 minutos inteira em dias ruins).
    Gera os cards do mês corrente e dispara push pra compromissos
    vencendo em 3/1/0 dias ou já atrasados."""
    try:
        agora = agora_br()
        hoje_str = agora.strftime("%Y-%m-%d")
        if not (agora.hour == 7 or (agora.hour == 8 and agora.minute <= 30)):
            return {"disparado": False, "motivo": f"fora da janela (agora {agora.strftime('%H:%M')})"}
        ja_feito = _ler_trava("compromissos_verificados_em")
        if ja_feito == hoje_str:
            return {"disparado": False, "motivo": "já verificado hoje"}
        _gravar_trava("compromissos_verificados_em", hoje_str)

        criados = _gerar_compromissos_mes_atual()
        compromissos = _listar_compromissos_core()
        alertados = []
        for c in compromissos:
            if c["status"] == "Concluído":
                continue
            dias = c["diasRestantes"]
            if c["status"] == "Atrasado":
                enviar_push(
                    titulo=f"🔴 {c['tipoLabel']} atrasado — {c['cliente']}",
                    corpo=f"Competência {c['competencia']} — prazo era {c['dataLimite']}. Etapa pendente: {c['etapas'][0]}.",
                    tipo="compromisso_atrasado",
                    url="https://fred-alexandrino.github.io/PAINELDEFALHAS/",
                )
                alertados.append(c["id"])
            elif dias in (0, 1, 3):
                prazo_txt = "vence hoje" if dias == 0 else f"faltam {dias} dia(s)"
                enviar_push(
                    titulo=f"📋 {c['tipoLabel']} — {c['cliente']}",
                    corpo=f"Competência {c['competencia']}: {prazo_txt} ({c['dataLimite']}).",
                    tipo="compromisso_alerta",
                    url="https://fred-alexandrino.github.io/PAINELDEFALHAS/",
                )
                alertados.append(c["id"])
        return {"disparado": True, "cardsCriados": criados, "alertados": alertados}
    except Exception as e:
        log.error(f"[Compromissos] Erro na verificação diária: {e}")
        return {"disparado": False, "erro": str(e)}


@app.route("/corrigir-nomenclatura-preventiva", methods=["POST", "GET"])
def corrigir_nomenclatura_preventiva():
    """Correção retroativa de uso único: atividades multi-tarefa criadas
    antes da padronização MPM/MPS/MPA ainda têm o texto bruto da Fracttal
    como descrição (ex.: "[Grid Co.] - MPM") em vez de "PREVENTIVA MENSAL"
    / "Múltiplos equipamentos (Preventiva Mensal)". Esse endpoint varre e
    corrige as já existentes; daqui pra frente isso já é automático."""
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    aplicar = request.args.get("apply", "false").lower() == "true"

    _MULTI_EQUIP_MARCADORES = ("Múltiplas atividades", "Múltiplos equipamentos")

    ws = get_atividades_sheet()
    todos = ws.get_all_values()
    corrigidas = []
    batch_updates = []
    for i, row in enumerate(todos[1:], start=2):
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        numero_os = row[13].strip()
        equipamento_atual = row[3].strip()
        descricao_atual = row[4].strip()
        if not numero_os or not equipamento_atual.startswith(_MULTI_EQUIP_MARCADORES):
            continue
        titulo, equip_novo = _fracttal_detectar_preventiva([{"description": descricao_atual}])
        if not titulo:
            continue
        if descricao_atual == titulo and equipamento_atual == equip_novo:
            continue  # já está correto
        corrigidas.append({"linha": i, "numeroOS": numero_os,
                            "de": {"descricao": descricao_atual, "equipamento": equipamento_atual},
                            "para": {"descricao": titulo, "equipamento": equip_novo}})
        if aplicar:
            batch_updates.append({"range": gspread.utils.rowcol_to_a1(i, 4), "values": [[equip_novo]]})
            batch_updates.append({"range": gspread.utils.rowcol_to_a1(i, 5), "values": [[titulo]]})

    if aplicar and batch_updates:
        TAMANHO_LOTE = 200
        for k in range(0, len(batch_updates), TAMANHO_LOTE):
            ws.batch_update(batch_updates[k:k + TAMANHO_LOTE], value_input_option="RAW")

    return jsonify({"ok": True, "aplicado": aplicar, "total": len(corrigidas), "corrigidas": corrigidas}), 200


@app.route("/corrigir-estado-revisao", methods=["POST", "GET"])
def corrigir_estado_revisao():
    """Correção retroativa de uso único: reabre atividades marcadas como
    'Concluído' internamente cujo statusOS na Fracttal ainda é 'Em Revisão'
    (não 'Finalizada'). Bug anterior tratava Em Revisão como conclusão."""
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    aplicar = request.args.get("apply", "false").lower() == "true"

    ws = get_atividades_sheet()
    todos = ws.get_all_values()
    corrigidas = []
    for i, row in enumerate(todos[1:], start=2):
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        numero_os = row[13].strip()
        status_interno = row[8].strip()
        status_os = row[14].strip()
        if numero_os and status_interno == "Concluído" and status_os == "Em Revisão":
            corrigidas.append({"linha": i, "numeroOS": numero_os})
            if aplicar:
                _gravar_status_interno(ws, i, "Em Aberto")
                entry = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - ⚠️ Correção retroativa: OS reaberta — "
                         f"estava marcada como concluída, mas o estado na Fracttal é \"Em Revisão\", "
                         f"não \"Finalizada\" (bug de interpretação de estado corrigido).")
                hist_atual = row[ATIV_COL_HISTORICO - 1] if len(row) >= ATIV_COL_HISTORICO else ""
                ws.update_cell(i, ATIV_COL_HISTORICO, f"{hist_atual}\n{entry}".strip() if hist_atual else entry)
                time.sleep(0.3)

    return jsonify({"ok": True, "aplicado": aplicar, "total": len(corrigidas), "corrigidas": corrigidas}), 200


@app.route("/config-ler", methods=["GET"])
def config_ler():
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    ws_cfg = _get_config_sheet()
    valores = ws_cfg.get_all_values()
    pares = {row[0]: (row[1] if len(row) > 1 else "") for row in valores[1:] if row and row[0].strip()}
    return jsonify({"ok": True, "pares": pares}), 200


@app.route("/config-set-lote", methods=["POST"])
def config_set_lote():
    """Grava múltiplos pares chave/valor na aba _Sistema de uma vez, numa
    única leitura + uma única escrita em lote (evita estourar a cota da
    API do Google Sheets, que é o que acontecia gravando um por um)."""
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    dados = request.get_json(force=True, silent=True) or {}
    pares = dados.get("pares", {})
    if not pares:
        return jsonify({"ok": True, "gravados": []}), 200

    ws_cfg = _get_config_sheet()
    valores = ws_cfg.get_all_values()
    linha_existente = {row[0].strip(): i for i, row in enumerate(valores[1:], start=2) if row}

    batch_updates = []
    novas_linhas = []
    for chave, valor in pares.items():
        if chave in linha_existente:
            batch_updates.append({
                "range": gspread.utils.rowcol_to_a1(linha_existente[chave], 2),
                "values": [[valor]],
            })
        else:
            novas_linhas.append([chave, valor])

    if batch_updates:
        ws_cfg.batch_update(batch_updates, value_input_option="RAW")
    if novas_linhas:
        ws_cfg.append_rows(novas_linhas, value_input_option="RAW")

    return jsonify({"ok": True, "gravados": list(pares.keys())}), 200


# ── Comunicados diários automáticos (WhatsApp) ──────────────────────────
# Mapeamento usina → grupo do WhatsApp fica na aba "_Sistema", chaves no
# formato "grupo_usina:<Nome da Usina>" = "<id>@g.us". Fred edita essa
# aba diretamente na planilha pra adicionar/trocar grupos, sem precisar de
# um novo deploy. Usinas sem grupo configurado são simplesmente ignoradas
# (não dá erro, só não recebem comunicado).

_mapa_grupo_usina_cache = {"dados": None, "expira_em": 0}


def _mapa_grupo_usina():
    """Cache curto (30s) porque essa função é chamada MUITAS vezes em
    sequência durante o processamento de fotos de zeladoria (uma vez por
    lote) — sem cache, isso sozinho já contribuía bastante pra estourar a
    cota de leitura do Google Sheets. _Sistema muda raramente, então 30s
    de defasagem não é problema."""
    agora_ts = time.time()
    if _mapa_grupo_usina_cache["dados"] is not None and agora_ts < _mapa_grupo_usina_cache["expira_em"]:
        return _mapa_grupo_usina_cache["dados"]
    ws_cfg = _get_config_sheet()
    valores = _gspread_retry(lambda: ws_cfg.get_all_values())
    mapa = {}
    for row in valores[1:]:
        if row and row[0].strip().startswith("grupo_usina:"):
            usina = row[0].strip()[len("grupo_usina:"):].strip()
            grupo_id = row[1].strip() if len(row) > 1 else ""
            if usina and grupo_id:
                mapa[usina] = grupo_id
    _mapa_grupo_usina_cache["dados"] = mapa
    _mapa_grupo_usina_cache["expira_em"] = agora_ts + 30
    return mapa


def _nome_amigavel_grupo(grupo_id):
    """Resolve um ID bruto de grupo do WhatsApp (ex.: '120363...@g.us')
    pro nome da(s) usina(s) que esse grupo atende, usando o mapeamento já
    existente — em vez de expor o número do grupo cru no histórico."""
    try:
        mapa = _mapa_grupo_usina()  # usina -> grupo_id
        usinas = sorted({u for u, g in mapa.items() if g == grupo_id})
        if usinas:
            return " / ".join(usinas[:3]) + (" e outras" if len(usinas) > 3 else "")
    except Exception:
        pass
    return None


def _editor_legivel(editor):
    """Traduz identificadores internos de 'quem fez a alteração' pra texto
    apresentável (inclusive pro cliente ver) — nunca mostra ID de grupo do
    WhatsApp, nome de rotina interna (ex.: 'fracttal-sync'), etc. direto no
    histórico. Adicionado em 17/07/2026 a pedido do Fred, depois de reparar
    que o histórico mostrava coisas como 'tecnico:120363...' pro cliente."""
    editor = (editor or "").strip()
    if not editor:
        return "sistema"
    if editor.startswith("tecnico:"):
        grupo_id = editor[len("tecnico:"):]
        nome = _nome_amigavel_grupo(grupo_id)
        return f"técnico de campo ({nome})" if nome else "técnico de campo (via WhatsApp)"
    mapa = {
        "fracttal-sync": "sincronização automática com a Fracttal",
        "fracttal-backfill": "sincronização automática com a Fracttal",
        "claude-chat": "assistente (Claude)",
        "reprogramacao-ia": "reprogramação sugerida por IA",
    }
    return mapa.get(editor, editor)


_MIGRAR_HIST_PADRAO_CRIACAO = re.compile(
    r'^(?P<data>\d{2}/\d{2}/\d{4} \d{2}:\d{2}) - Atividade criada por (fracttal-sync|fracttal-backfill)\.$')
_MIGRAR_HIST_PADRAO_VISUALIZADO = re.compile(
    r'^(?P<data>\d{2}/\d{2}/\d{4} \d{2}:\d{2}) - visualizado alterado de "(?P<de>.*?)" para "(?P<para>.*?)" por (?P<editor>.*?)\.$')
_MIGRAR_HIST_PADRAO_TECNICO_DESC = re.compile(
    r'^(?P<data>\d{2}/\d{2}/\d{4} \d{2}:\d{2}) - tecnico:(?P<grupo>\d+): (?P<texto>.*)$')
_MIGRAR_HIST_PADRAO_TECNICO_STATUS = re.compile(
    r'^(?P<data>\d{2}/\d{2}/\d{4} \d{2}:\d{2}) - tecnico:(?P<grupo>\d+) reportou status "(?P<status>.*?)" '
    r'pelo WhatsApp — verificando direto na Fracttal \(o status real vem de lá, não da mensagem\)\.$')
_MIGRAR_HIST_PADRAO_STATUS_OS = re.compile(
    r'^(?P<data>\d{2}/\d{2}/\d{4} \d{2}:\d{2}) - Status na OS \(Fracttal\) atualizado: '
    r'"(?P<de>.*?)" → "(?P<para>.*?)", (?P<pde>\d+)% → (?P<ppara>\d+)% \((?P<geral>.*?)\)\.$')
_MIGRAR_HIST_PADRAO_GENERICO_TECNICO = re.compile(r'^(?P<prefixo>.*) por tecnico:(?P<grupo>\d+)\.$')


def _migrar_linha_historico(linha):
    """Reescreve uma única linha de histórico (formato antigo) pro formato
    novo, mais legível. Devolve a linha original sem alterar se nenhum dos
    padrões conhecidos bater — nunca inventa nem apaga informação."""
    m = _MIGRAR_HIST_PADRAO_CRIACAO.match(linha)
    if m:
        return f'{m["data"]} - Atividade criada por sincronização automática com a Fracttal.'

    m = _MIGRAR_HIST_PADRAO_VISUALIZADO.match(linha)
    if m and m["para"].strip().lower() == "sim":
        return f'{m["data"]} - Marcado como visualizado ({m["editor"]}).'

    m = _MIGRAR_HIST_PADRAO_TECNICO_DESC.match(linha)
    if m:
        nome = _nome_amigavel_grupo(m["grupo"]) or "via WhatsApp"
        return f'{m["data"]} - técnico de campo ({nome}): {m["texto"]}'

    m = _MIGRAR_HIST_PADRAO_TECNICO_STATUS.match(linha)
    if m:
        nome = _nome_amigavel_grupo(m["grupo"]) or "via WhatsApp"
        return (f'{m["data"]} - técnico de campo ({nome}) reportou status "{m["status"]}" '
                f'pelo WhatsApp (confirmado em seguida direto com a Fracttal).')

    m = _MIGRAR_HIST_PADRAO_STATUS_OS.match(linha)
    if m:
        partes = []
        if m["de"] != m["para"]:
            partes.append(f'status na Fracttal mudou de "{m["de"]}" para "{m["para"]}"')
        if m["pde"] != m["ppara"]:
            partes.append(f'progresso da tarefa foi de {m["pde"]}% para {m["ppara"]}%')
        if not partes:
            # nem status nem percentual mudaram no texto antigo — o único
            # jeito de "mudou" ter sido true na época é a situação geral da
            # tarefa ter mudado; não temos o valor "de" no texto antigo
            # (só foi gravado o "para"), então afirmamos só o que sabemos
            # de verdade, sem inventar uma transição que não temos como
            # confirmar.
            partes.append(f'situação geral da tarefa: "{m["geral"]}"')
        return f'{m["data"]} - ' + "; ".join(partes) + "."

    # Padrão genérico: PEGA QUALQUER linha que termine em "por tecnico:ID."
    # (ex.: "Responsável alterado de X para Y por tecnico:123."), não só os
    # formatos específicos já tratados acima — cobre qualquer campo editado
    # por um técnico via WhatsApp, sem precisar prever cada rótulo de campo.
    m = _MIGRAR_HIST_PADRAO_GENERICO_TECNICO.match(linha)
    if m:
        nome = _nome_amigavel_grupo(m["grupo"]) or "via WhatsApp"
        return f'{m["prefixo"]} por técnico de campo ({nome}).'

    return linha


@app.route("/migrar-historico-legivel", methods=["POST", "OPTIONS"])
def migrar_historico_legivel():
    """
    Reescreve retroativamente o texto já salvo no histórico de TODAS as
    atividades, aplicando os mesmos formatos mais legíveis usados a partir
    de 17/07/2026 (sem ID de grupo do WhatsApp cru, sem nome de rotina
    interna tipo 'fracttal-sync', sem 'X → X' quando nada mudou de verdade).

    Por padrão roda em modo TESTE (aplicar=false): não grava nada, só
    devolve quantas linhas/atividades seriam alteradas e uma amostra, pra
    conferir antes de aplicar de verdade.

    Corpo esperado (opcional): {"aplicar": true}
    """
    if request.method == "OPTIONS":
        return ("", 204)
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = request.get_json(force=True, silent=True) or {}
    aplicar = bool(body.get("aplicar", False))

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()

        atualizacoes = []  # {"range": f"L{i}", "values": [[novo_historico]]}
        amostra = []
        atividades_afetadas = 0
        linhas_afetadas = 0

        for i, row in enumerate(todos[1:], start=2):
            hist_col = ATIV_COL_HISTORICO - 1
            if len(row) <= hist_col or not row[hist_col].strip():
                continue
            hist_original = row[hist_col]
            linhas_originais = hist_original.split("\n")
            linhas_novas = [_migrar_linha_historico(l) for l in linhas_originais]
            if linhas_novas == linhas_originais:
                continue

            atividades_afetadas += 1
            n_mudou = sum(1 for a, b in zip(linhas_originais, linhas_novas) if a != b)
            linhas_afetadas += n_mudou
            hist_novo = "\n".join(linhas_novas)

            if len(amostra) < 5:
                amostra.append({
                    "id": row[0] if row else "?",
                    "antes": [l for l, n in zip(linhas_originais, linhas_novas) if l != n][:3],
                    "depois": [n for l, n in zip(linhas_originais, linhas_novas) if l != n][:3],
                })

            if aplicar:
                col_letra = chr(64 + ATIV_COL_HISTORICO) if ATIV_COL_HISTORICO <= 26 else "AA"
                atualizacoes.append({"range": f"{col_letra}{i}", "values": [[hist_novo]]})

        if aplicar and atualizacoes:
            ws.batch_update(atualizacoes)

        return jsonify({
            "ok": True,
            "aplicado": aplicar,
            "atividades_afetadas": atividades_afetadas,
            "linhas_afetadas": linhas_afetadas,
            "amostra": amostra,
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500



_mapa_cluster_usina_cache = {"dados": None, "expira_em": 0}


def _mapa_cluster_usina():
    """Mapeia usina -> código de cluster/equipe regional (ex.: 'SP Centro
    01'), configurado na aba _Sistema como 'cluster_usina:<Usina>'.
    Cache curto (30s) pelo mesmo motivo do _mapa_grupo_usina."""
    agora_ts = time.time()
    if _mapa_cluster_usina_cache["dados"] is not None and agora_ts < _mapa_cluster_usina_cache["expira_em"]:
        return _mapa_cluster_usina_cache["dados"]
    ws_cfg = _get_config_sheet()
    valores = _gspread_retry(lambda: ws_cfg.get_all_values())
    mapa = {}
    for row in valores[1:]:
        if row and row[0].strip().startswith("cluster_usina:"):
            usina = row[0].strip()[len("cluster_usina:"):].strip()
            cluster = row[1].strip() if len(row) > 1 else ""
            if usina and cluster:
                mapa[usina] = cluster
    _mapa_cluster_usina_cache["dados"] = mapa
    _mapa_cluster_usina_cache["expira_em"] = agora_ts + 30
    return mapa


def _montar_texto_comunicado_usina(usina, atividades):
    def dias_atraso(prazo):
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", prazo or "")
        if not m:
            return None
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return (agora_br().date() - datetime(y, mth, d).date()).days

    atividades = sorted(atividades, key=lambda a: dias_atraso(a.get("prazo", "")) or -999, reverse=True)
    hoje_str = agora_br().strftime("%d/%m/%Y")
    txt = f"📋 *Atividades em aberto — {usina}*\n📅 {hoje_str}\n\n"
    txt += f"Existem *{len(atividades)} atividade{'s' if len(atividades) != 1 else ''}* pendente{'s' if len(atividades) != 1 else ''}:\n\n"
    for i, a in enumerate(atividades, start=1):
        dias = dias_atraso(a.get("prazo", ""))
        atrasada = dias is not None and dias > 0
        numero_os = a.get("numeroOS", "")
        txt += f"{i}. {'🔴' if atrasada else '🟢'} {'OS ' + numero_os + ' — ' if numero_os else ''}{a.get('equipamento', '')}\n"
        txt += f"   {a.get('descricao', '')}\n"
        if a.get("prazo"):
            txt += f"   📅 Prazo: {a['prazo']}" + (f" (atrasada há {dias} dia{'s' if dias > 1 else ''})" if atrasada else "") + "\n"
        txt += "\n"
    txt += "Por favor, atualizem o andamento dessas atividades o quanto antes. Qualquer dificuldade, me avisem."
    return txt


@app.route("/verificar-e-enviar-comunicados", methods=["POST", "GET"])
def _verificar_e_disparar_comunicados_se_necessario():
    """DESATIVADA a pedido do Fred em 15/07/2026 — ver detalhes no
    corpo da função. Mantida com esse nome (em vez de apagada) porque
    está diretamente exposta como rota pública (/verificar-e-enviar-
    comunicados) — se algum monitor externo (UptimeRobot ou outro) ainda
    estiver batendo nela numa agenda própria, precisa continuar
    respondendo 200 sem disparar nada, em vez de dar erro.

    Histórico do problema: o piggyback dentro de /sync-fracttal já tinha
    sido desligado (ligava aqui indiretamente), mas essa função também
    é chamada DIRETO por essa rota própria — que outra sessão/monitor
    pode estar acionando de forma independente, a cada poucos minutos,
    sem eu saber. Isso explicava comunicados continuando a sair sozinhos
    mesmo depois do primeiro desligamento. Agora a desativação está na
    fonte única (aqui dentro), então funciona não importa quem chame."""
    return {"disparado": False, "motivo": "disparo automático desativado — use o botão Comunicados no painel"}


def _verificar_e_disparar_comunicados_se_necessario_DESATIVADA_ORIGINAL():
    """Lógica compartilhada: só dispara o envio de verdade se for dia útil,
    estiver na janela 07:00-08:30 (BRT) e ainda não tiver sido enviado
    hoje. Retorna um dict com o resultado (nunca levanta exceção pro
    chamador, pra nunca quebrar quem estiver piggybackando nela).

    Janela alargada de 9 minutos pra 90 minutos em 15/07/2026: um dia em
    que o Render aparentemente estava com cold-start bem lento resultou
    em 502 em TODAS as tentativas dentro da janela original (confirmado
    manualmente: 1ª tentativa deu 502, 2ª — minutos depois — funcionou).
    Como o ciclo de 5 em 5 min só tinha ~2 chances dentro de 9 minutos,
    um dia ruim de Render bastava pra perder o comunicado inteiro. Com
    90 minutos de janela, sobra bastante margem pro serviço esquentar
    sozinho sem precisar de intervenção manual."""
    try:
        agora = agora_br()
        hoje_str = agora.strftime("%Y-%m-%d")

        if agora.weekday() >= 5:  # sábado=5, domingo=6
            return {"disparado": False, "motivo": "fim de semana"}
        if not (agora.hour == 7 or (agora.hour == 8 and agora.minute <= 30)):
            return {"disparado": False, "motivo": f"fora da janela (agora {agora.strftime('%H:%M')})"}

        ja_enviado = _ler_trava("comunicados_enviados_em")
        if ja_enviado == hoje_str:
            return {"disparado": False, "motivo": "já enviado hoje"}

        # a trava só é gravada DEPOIS de confirmar que o envio foi tentado
        # de verdade — antes ela era gravada logo antes de chamar a função
        # de envio, então uma falha (ex.: WhatsApp reconectando naquele
        # minuto) deixava o dia "marcado como enviado" sem nada ter saído,
        # bloqueando qualquer nova tentativa até o dia seguinte (bug
        # identificado em 14/07/2026, depois de um dia sem comunicado).
        resultado = _enviar_comunicados_diarios_core()
        if resultado.get("ok", True) is False and not resultado.get("enviados"):
            log.error(f"[ComunicadosDiarios] Envio falhou, trava NÃO gravada (tenta de novo no próximo ciclo): {resultado}")
            try:
                enviar_push(
                    titulo="⚠️ Comunicados de hoje não saíram",
                    corpo=f"Falha no envio às {agora.strftime('%H:%M')}: {resultado.get('error', 'motivo desconhecido')}. Tentará de novo em 5 min.",
                    tipo="comunicados_falha",
                )
            except Exception:
                pass
            return {"disparado": False, "motivo": "falha no envio, tentará de novo no próximo ciclo", "resultado": resultado}

        _gravar_trava("comunicados_enviados_em", hoje_str)
        return {"disparado": True, "resultado": resultado}
    except Exception as e:
        log.error(f"[ComunicadosDiarios] Erro na verificação/disparo: {e}")
        return {"disparado": False, "erro": str(e)}


def verificar_e_enviar_comunicados():
    """Ponto de entrada seguro pra ser chamado com frequência (ex.: a cada
    5 min via UptimeRobot) — só dispara o envio de verdade se:
      1. for dia útil (seg-sex) e estiver dentro da janela 07:00-07:09 (BRT)
      2. ainda não tiver sido enviado hoje (trava em _Sistema)
    Isso substitui o cron do GitHub Actions como gatilho principal, porque
    ele atrasa de forma imprevisível (chegou a disparar 7h30 depois do
    horário configurado). Fora da janela ou já enviado hoje, retorna sem
    fazer nada (barato, seguro de chamar repetidamente)."""
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    r = _verificar_e_disparar_comunicados_se_necessario()
    return jsonify({"ok": True, **r}), 200


@app.route("/enviar-comunicados-diarios", methods=["POST", "GET"])
def enviar_comunicados_diarios():
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    return jsonify(_enviar_comunicados_diarios_core()), 200


def _enviar_comunicados_diarios_core():
    if not WPP_SERVER_URL:
        return {"ok": False, "error": "WPP_SERVER_URL não configurado"}

    mapa_grupos = _mapa_grupo_usina()
    if not mapa_grupos:
        return {"ok": False, "error": ("Nenhum grupo configurado. Adicione linhas na aba "
                "_Sistema no formato \"grupo_usina:<Usina>\" = \"<id>@g.us\".")}

    ws = get_atividades_sheet()
    todos = ws.get_all_values()

    # ── Revalida AO VIVO na Fracttal cada candidata antes de decidir se
    # entra no comunicado — o dado gravado (statusOS) pode estar
    # ligeiramente desatualizado se essa OS específica ainda não tiver
    # caído no rodízio automático desde que o técnico a moveu pra
    # "Em Verificação" na Fracttal. Como o comunicado só roda 1x/dia,
    # vale o custo de checar direto na fonte pra garantir que nenhuma OS
    # já resolvida pelo técnico seja cobrada de novo (bug relatado pelos
    # técnicos em 14/07/2026 — "Em Verificação" sendo enviada mesmo
    # assim, por causa de dado desatualizado no momento do envio).
    candidatas_recheck = []
    for i, row in enumerate(todos[1:], start=2):
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        if not row[0].strip():
            continue
        status = row[8].strip()
        if _is_concluido_atividade(status):
            continue
        numero_os = row[13].strip()
        status_os = row[14].strip()
        if numero_os and status_os != "Em Processo":
            # regra simples e direta: só OS com estado "Em Processo" na
            # Fracttal entra no comunicado — qualquer outra coisa (Em
            # Revisão, Finalizada, Cancelada, vazio, ou qualquer estado
            # futuro que a Fracttal venha a usar) fica de fora por padrão,
            # em vez de tentar prever e listar cada estado que deveria
            # excluir (regra anterior, mais frágil — deixava passar coisa
            # nova que não estivesse na lista). Ajustado 15/07/2026.
            continue
        etiquetas = row[ATIV_CAMPO_COL["etiquetasOS"] - 1].strip().upper()
        if "PERFORMANCE" in etiquetas:
            # etiquetada na Fracttal como tarefa de análise de performance —
            # normalmente atribuída a um analista, não ao técnico de campo.
            # Mandar isso pro grupo da equipe só confunde quem recebe (não
            # é responsabilidade deles) — identificado 14/07/2026 com a OS
            # 8025 (Boa Esperança do Sul I), etiquetada PERFORMANCE e
            # atribuída a um analista, mas enviada ao grupo de campo.
            continue
        if numero_os:
            candidatas_recheck.append((i, row, numero_os))

    LIMITE_RECHECK_COMUNICADOS = 20  # trava de segurança de tempo — essa função
    # roda no mesmo ciclo que outras checagens (auditoria), então não pode
    # crescer sem limite. Prioriza as mais desatualizadas primeiro.
    candidatas_recheck.sort(key=lambda t: t[1][ATIV_CAMPO_COL["ultimaVerificacaoOS"] - 1] or "")
    candidatas_recheck = candidatas_recheck[:LIMITE_RECHECK_COMUNICADOS]

    for i, row, numero_os in candidatas_recheck:
        _fracttal_verificar_e_atualizar_uma_os(ws, i, row, numero_os, enviar_notificacao=False)
        time.sleep(0.35)

    # rebusca do zero — garante que a seleção final usa o dado que acabou
    # de ser gravado pela revalidação acima, sem depender de referências
    # de lista em memória (que podem se desconectar quando uma linha
    # precisa de padding).
    todos = ws.get_all_values()

    por_usina = {}
    for row in todos[1:]:
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        if not row[0].strip():
            continue
        status = row[8].strip()
        if _is_concluido_atividade(status):
            continue
        status_os = row[14].strip()
        numero_os = row[13].strip()
        if numero_os and status_os != "Em Processo":
            # mesma regra positiva do primeiro loop: só "Em Processo" entra.
            continue
        etiquetas = row[ATIV_CAMPO_COL["etiquetasOS"] - 1].strip().upper()
        if "PERFORMANCE" in etiquetas:
            continue
        usina = row[2].strip()
        if not usina:
            continue
        d = {
            "usina": usina,
            "equipamento": row[3].strip(),
            "descricao": row[4].strip(),
            "prazo": row[6].strip(),
            "numeroOS": numero_os,
        }
        por_usina.setdefault(usina, []).append(d)

    dry_run = request.args.get("dry_run", "false").lower() == "true"

    enviados, pulados, erros = [], [], []
    for usina, grupo_id in mapa_grupos.items():
        atividades = por_usina.get(usina, [])
        if not atividades:
            pulados.append({"usina": usina, "motivo": "sem atividades em aberto"})
            continue
        texto = _montar_texto_comunicado_usina(usina, atividades)

        if dry_run:
            enviados.append({"usina": usina, "grupo": grupo_id, "atividades": len(atividades), "texto": texto})
            continue

        try:
            r = requests.post(
                f"{WPP_SERVER_URL}/api/enviar-mensagem",
                json={"grupoId": grupo_id, "texto": texto},
                headers={"X-Webhook-Secret": WEBHOOK_SECRET} if WEBHOOK_SECRET else {},
                timeout=20,
            )
            if r.ok and r.json().get("ok"):
                enviados.append({"usina": usina, "grupo": grupo_id, "atividades": len(atividades)})
            else:
                erros.append({"usina": usina, "erro": r.text[:200]})
        except Exception as e:
            erros.append({"usina": usina, "erro": str(e)})

    log.info(f"[ComunicadosDiarios] dry_run={dry_run} enviados={len(enviados)} pulados={len(pulados)} erros={len(erros)}")
    return {"ok": True, "dry_run": dry_run, "enviados": enviados, "pulados": pulados, "erros": erros}


# ── Reversão de excesso (recuperação de uso único) ──────────────────────
# O endpoint acima foi rodado 3x por engano em 2026-07-08 antes de ter uma
# trava, e cada rodada redescontou -3h de linhas que já estavam corretas
# (bug de idempotência: a classificação não sabia diferenciar "ainda não
# corrigido" de "já corrigido"). Este endpoint soma de volta +6h nas linhas
# afetadas (2 descontos extras) pra restaurar o valor correto de um único
# desconto. As OSs em _EXCLUIR_REVERSAO tiveram um histórico de correção
# diferente (parcial/manual) e são tratadas à parte, não por aqui.
_EXCLUIR_REVERSAO = {"9173", "9154"}


@app.route("/reverter-excesso-fuso", methods=["POST", "GET"])
def reverter_excesso_fuso():
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    aplicar = request.args.get("apply", "false").lower() == "true"

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
        _garantir_headers_atividades(ws)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    def _precisa_reverter(ts_str, fmt):
        try:
            dt = datetime.strptime(ts_str, fmt)
        except Exception:
            return None
        if dt.date() < _HOJE_DEPLOY:
            return (dt + timedelta(hours=6)).strftime(fmt)
        if dt.date() == _HOJE_DEPLOY and dt.time() < _JANELA_INICIO:
            return (dt + timedelta(hours=6)).strftime(fmt)
        return None

    alteracoes = []
    batch_updates = []
    for i, row in enumerate(todos[1:], start=2):
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        numero_os = row[13].strip()
        id_atividade = row[0].strip()
        if numero_os in _EXCLUIR_REVERSAO:
            continue
        updates = {}

        for campo_col, fmt in ((9, '%d/%m/%Y %H:%M:%S'), (10, '%d/%m/%Y %H:%M:%S')):
            val = row[campo_col].strip()
            if val:
                novo = _precisa_reverter(val, fmt)
                if novo:
                    updates[campo_col + 1] = novo

        hist = row[11]
        if hist:
            linhas_novas = []
            hist_mudou = False
            for linha_h in hist.split("\n"):
                m = _HIST_LINHA_RE.match(linha_h)
                if m:
                    data_str, hora_str, seg = m.group(1), m.group(2), m.group(3) or ""
                    ts_str = f"{data_str} {hora_str}{seg}"
                    fmt = '%d/%m/%Y %H:%M:%S' if seg else '%d/%m/%Y %H:%M'
                    novo_ts = _precisa_reverter(ts_str, fmt)
                    if novo_ts:
                        linha_h = novo_ts + linha_h[len(ts_str):]
                        hist_mudou = True
                linhas_novas.append(linha_h)
            if hist_mudou:
                updates[12] = "\n".join(linhas_novas)

        val = row[23].strip()
        if val:
            novo = _precisa_reverter(val, '%Y-%m-%dT%H:%M:%S')
            if novo:
                updates[24] = novo

        if updates:
            alteracoes.append({"linha": i, "id": id_atividade, "numeroOS": numero_os,
                                "colunas_alteradas": list(updates.keys())})
            if aplicar:
                for col, novo_val in updates.items():
                    batch_updates.append({
                        "range": gspread.utils.rowcol_to_a1(i, col),
                        "values": [[novo_val]],
                    })

    if aplicar and batch_updates:
        TAMANHO_LOTE = 200
        for k in range(0, len(batch_updates), TAMANHO_LOTE):
            ws.batch_update(batch_updates[k:k + TAMANHO_LOTE], value_input_option="RAW")

    return jsonify({"ok": True, "aplicado": aplicar, "linhas_afetadas": len(alteracoes),
                     "detalhes": alteracoes, "excluidas": list(_EXCLUIR_REVERSAO)}), 200


@app.route("/sync-fracttal", methods=["POST", "GET"])
def sync_fracttal():
    """
    Gatilho automático confiável (chamado a cada 5 min via UptimeRobot).
    Faz quatro coisas com cadências diferentes, de propósito:
      1. VARREDURA DE STATUS/ESTADO das OSs já no dashboard — roda em
         TODA chamada (5 em 5 min), porque isso precisa ficar em dia com
         frequência (é o que o botão "Atualizar OS" também faz sob demanda).
      2. AUDITORIA COMPLETA (descoberta ampla de 24h + varredura ampla,
         incluindo detectar cancelamentos/conclusões, + validação de
         integridade de relatórios) — só roda de fato nas janelas das
         7h/12h/16h (throttle via _Sistema), porque é mais pesada e não
         precisa de frequência maior que isso.
      3. DESCOBERTA RÁPIDA (só descoberta, janela curta de 2h, sem recheck
         amplo) — roda a cada 30 min (throttle por timestamp via _Sistema),
         pra reduzir o gap de latência entre a criação de uma OS nova na
         Fracttal e ela aparecer no dashboard, sem esperar a próxima
         janela fixa de auditoria completa.
      4. Comunicados diários das 7h (piggyback, gatilho confiável).
    """
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = {"ok": True}

    try:
        body["atualizacao_status"] = _auditoria_consistencia_os_core(aplicar=True)
    except Exception as e:
        log.error(f"[Atualizacao] Erro no piggyback: {e}")
        body["atualizacao_status"] = {"erro": str(e)}

    # DESATIVADO a pedido do Fred em 15/07/2026: o disparo automático não
    # estava rodando de forma confiável às 7h (mesmo com a janela alargada
    # pra 90min) e, quando ele intervinha manualmente pra investigar, às
    # vezes resultava em envio em duplicidade (até 3x o mesmo comunicado
    # pras mesmas equipes) — prejudicando a credibilidade da ferramenta.
    # O botão "Comunicados" no painel continua funcionando normalmente,
    # sob demanda — só o gatilho automático (piggyback no /sync-fracttal)
    # foi desligado.
    body["comunicados_check"] = {"disparado": False, "motivo": "disparo automático desativado — use o botão Comunicados"}

    try:
        body["auditoria_completa_check"] = _verificar_e_disparar_auditoria_completa_se_necessario()
    except Exception as e:
        log.error(f"[AuditoriaCompleta] Erro no piggyback: {e}")
        body["auditoria_completa_check"] = {"erro": str(e)}

    try:
        body["descoberta_rapida_check"] = _verificar_e_disparar_descoberta_rapida_se_necessario()
    except Exception as e:
        log.error(f"[DescobertaRapida] Erro no piggyback: {e}")
        body["descoberta_rapida_check"] = {"erro": str(e)}

    try:
        body["compromissos_check"] = _verificar_compromissos_se_necessario()
    except Exception as e:
        log.error(f"[Compromissos] Erro no piggyback: {e}")
        body["compromissos_check"] = {"erro": str(e)}

    return jsonify(body), 200


@app.route("/alertar-wpp-status", methods=["POST"])
def alertar_wpp_status():
    """
    Chamado pela ponte do WhatsApp (server.js) sempre que a conexão cai ou
    precisa de novo QR code. Dispara push imediato pro celular do Fred —
    sem isso, uma queda de sessão só é percebida dias depois (mensagens
    de ocorrência chegam nos grupos mas não são capturadas enquanto a
    sessão estiver caída, e não ficam "na fila" esperando reconexão).
    """
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    status = body.get("status", "desconhecido")
    detalhe = body.get("detalhe", "")
    try:
        if status == "aguardando_qr":
            enviar_push(
                titulo="⚠️ WhatsApp precisa de novo QR Code",
                corpo="A automação de ocorrências está sem conexão — mensagens de rondas não estão sendo capturadas até reconectar. Acesse /qr na ponte pra escanear.",
                tipo="wpp_desconectado",
            )
        elif status == "desconectado":
            enviar_push(
                titulo="⚠️ WhatsApp desconectado",
                corpo=f"Conexão caiu ({detalhe}). Tentando reconectar automaticamente.",
                tipo="wpp_desconectado",
            )
        elif status == "reconectado":
            enviar_push(
                titulo="✅ WhatsApp reconectado",
                corpo="A automação de ocorrências voltou a capturar mensagens normalmente.",
                tipo="wpp_reconectado",
            )
        elif status == "falha_encaminhamento":
            # A conexão com o WhatsApp em si pode estar normal, mas a
            # ponte não conseguiu repassar essa mensagem específica pro
            # backend depois de 3 tentativas — sem esse alerta, isso
            # ficava só no console, invisível, e a ocorrência real se
            # perdia por dias sem ninguém saber (identificado 13/07/2026).
            enviar_push(
                titulo="⚠️ Mensagem de ronda não foi registrada",
                corpo=f"Falha ao gravar após 3 tentativas: {detalhe[:150]}",
                tipo="wpp_falha_encaminhamento",
            )
        return jsonify({"ok": True}), 200
    except Exception as e:
        log.error(f"[AlertaWPP] Erro ao enviar push: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500



@app.route("/desligamento-manual", methods=["GET"])
def listar_desligamento_manual():
    """Lista todos os overrides manuais de classificação de desligamento
    (Falhas e Atividades juntos) — usado pelo frontend pra sobrepor a
    detecção automática por palavra-chave quando o Fred marcar manualmente
    que algo É ou NÃO É um desligamento de usina de verdade."""
    try:
        ws = get_desligamento_manual_sheet()
        todos = ws.get_all_values()
        itens = []
        for row in todos[1:]:
            if len(row) < 3 or not row[0].strip():
                continue
            itens.append({
                "origem": row[0].strip(), "id": row[1].strip(), "valor": row[2].strip(),
                "editor": row[3].strip() if len(row) > 3 else "",
                "atualizadoEm": row[4].strip() if len(row) > 4 else "",
            })
        return jsonify({"ok": True, "itens": itens}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/marcar-desligamento-manual", methods=["POST", "OPTIONS"])
def marcar_desligamento_manual():
    """Grava (ou limpa) a classificação manual de desligamento pra uma
    ocorrência/atividade específica. valor: "sim" (força tratar como
    desligamento), "nao" (força tratar como NÃO desligamento, mesmo que a
    detecção automática por palavra-chave tivesse batido), ou "" (remove o
    override, volta a valer só a detecção automática)."""
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(force=True, silent=True) or {}
    origem = str(body.get("origem", "")).strip()
    item_id = str(body.get("id", "")).strip()
    valor = str(body.get("valor", "")).strip().lower()
    editor = str(body.get("editor", "dashboard")).strip()
    if origem not in ("falha", "atividade") or not item_id:
        return jsonify({"ok": False, "error": "origem (falha|atividade) e id são obrigatórios"}), 400
    if valor not in ("sim", "nao", ""):
        return jsonify({"ok": False, "error": "valor deve ser 'sim', 'nao' ou vazio"}), 400

    try:
        ws = get_desligamento_manual_sheet()
        todos = ws.get_all_values()
        linha_existente = None
        for i, row in enumerate(todos[1:], start=2):
            if len(row) >= 2 and row[0].strip() == origem and row[1].strip() == item_id:
                linha_existente = i
                break
        agora = agora_br().strftime("%d/%m/%Y %H:%M:%S")
        if valor == "":
            if linha_existente:
                ws.delete_rows(linha_existente)
            return jsonify({"ok": True, "removido": bool(linha_existente)}), 200
        if linha_existente:
            ws.update(f"A{linha_existente}:E{linha_existente}", [[origem, item_id, valor, editor, agora]])
        else:
            ws.append_row([origem, item_id, valor, editor, agora])
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/sincronizar-chamados", methods=["POST", "OPTIONS"])
def sincronizar_chamados():
    """
    Recebe dados da tabela de chamados de fabricante (hoje: exportação
    manual da planilha do SharePoint enviada pelo Fred; futuramente,
    talvez um fluxo automático). Faz upsert por uma CHAVE COMPOSTA
    (Ticket/RMA + Ativo + Identificação do Equipamento + Data da
    ocorrência), não só pelo Ticket/RMA sozinho.

    Por quê: na planilha real, valores de Ticket/RMA como "00" são
    usados como placeholder em dezenas de chamados completamente
    diferentes (usinas/equipamentos diferentes) até o número real ser
    aberto — e alguns tickets legítimos (ex.: "00574/26") cobrem
    várias peças de equipamento diferentes na mesma usina/data. Usar só
    o Ticket/RMA como chave colapsaria esses grupos numa linha só,
    apagando dados de verdade. A combinação dos 4 campos já foi
    validada como livre de colisão na importação inicial de 553
    registros reais (17/07/2026).

    Aceita tanto uma linha única (objeto) quanto várias de uma vez
    (lista de objetos).

    Escreve tudo em LOTE — no máximo duas chamadas à API do Google
    Sheets no total (uma pra criar todas as linhas novas, outra pra
    atualizar todas as existentes), não importa se são 5 ou 5.000
    linhas recebidas — evita estourar a cota de escrita da API do
    Google (~60/min).
    """
    if request.method == "OPTIONS":
        return ("", 204)
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"ok": False, "error": "corpo da requisição precisa ser JSON"}), 400
    linhas = body if isinstance(body, list) else [body]

    idx_ticket = CHAMADOS_FABRICANTE_HEADERS.index("Ticket/RMA")
    idx_ativo = CHAMADOS_FABRICANTE_HEADERS.index("Ativo")
    idx_equip = CHAMADOS_FABRICANTE_HEADERS.index("Identificação do Equipamento")
    idx_data_ocorrencia = CHAMADOS_FABRICANTE_HEADERS.index("Data da ocorrência")
    n_cols = len(CHAMADOS_FABRICANTE_HEADERS)
    colunas_letra_fim = chr(64 + n_cols) if n_cols <= 26 else "Z"

    def _chave(row_vals):
        return (row_vals[idx_ticket], row_vals[idx_ativo], row_vals[idx_equip], row_vals[idx_data_ocorrencia])

    try:
        ws = get_chamados_fabricante_sheet()
        todos = ws.get_all_values()

        por_chave = {}
        for i, row in enumerate(todos[1:], start=2):
            if len(row) < n_cols:
                row = row + [""] * (n_cols - len(row))
            por_chave[_chave(row)] = i

        atualizadas_map = {}
        criadas_map = {}
        erros = []

        for linha_recebida in linhas:
            if not isinstance(linha_recebida, dict):
                erros.append("item não é um objeto JSON válido")
                continue

            def _buscar_campo(nome_coluna):
                if nome_coluna in linha_recebida:
                    return str(linha_recebida[nome_coluna] or "").strip()
                alvo_norm = nome_coluna.lower().strip()
                for k, v in linha_recebida.items():
                    if k.lower().strip() == alvo_norm:
                        return str(v or "").strip()
                return ""

            nova_linha = [_buscar_campo(h) for h in CHAMADOS_FABRICANTE_HEADERS]
            chave = _chave(nova_linha)

            linha_existente = por_chave.get(chave)
            if linha_existente:
                atualizadas_map[linha_existente] = nova_linha
            else:
                criadas_map[chave] = nova_linha  # última ocorrência no batch vence, se repetir

        if atualizadas_map:
            ws.batch_update([
                {"range": f"A{linha}:{colunas_letra_fim}{linha}", "values": [valores]}
                for linha, valores in atualizadas_map.items()
            ])

        if criadas_map:
            ws.append_rows(list(criadas_map.values()))

        return jsonify({"ok": True, "criadas": len(criadas_map), "atualizadas": len(atualizadas_map),
                         "erros": erros}), 200
    except Exception as e:
        log.error(f"[ChamadosFabricante] Erro ao sincronizar: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


def _mapa_notas_chamados():
    """Notas que o Fred escreve no popup de detalhe do painel de Chamados
    — ficam só na aba _Sistema (chave 'nota_chamado:<ticket>|<ufv>|<equip>'),
    NUNCA na aba ChamadosFabricante, pra sobreviver a reimportações
    futuras da planilha do SharePoint sem serem apagadas."""
    ws_cfg = _get_config_sheet()
    valores = ws_cfg.get_all_values()
    mapa = {}
    for row in valores[1:]:
        if row and row[0].strip().startswith("nota_chamado:"):
            chave = row[0].strip()[len("nota_chamado:"):]
            mapa[chave] = row[1].strip() if len(row) > 1 else ""
    return mapa


@app.route("/atualizar-observacao-chamado", methods=["POST", "OPTIONS"])
def atualizar_observacao_chamado():
    """
    Salva a nota que o Fred escreve no popup de detalhe de um chamado —
    fica só na aba _Sistema (dashboard), NUNCA na aba ChamadosFabricante,
    pra não ser apagada quando a planilha do SharePoint for reimportada
    de novo no futuro.

    Corpo esperado: {"ticket": "...", "ufv": "...", "equipamento": "...",
    "novaObservacao": "..."}
    """
    if request.method == "OPTIONS":
        return ("", 204)
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = request.get_json(force=True, silent=True) or {}
    ticket = (body.get("ticket") or "").strip()
    ufv = (body.get("ufv") or "").strip()
    equipamento = (body.get("equipamento") or "").strip()
    nova_obs = body.get("novaObservacao") or ""
    chave = f"nota_chamado:{ticket}|{ufv}|{equipamento}"

    try:
        ws_cfg = _get_config_sheet()
        valores = ws_cfg.get_all_values()
        linha_existente = None
        for i, row in enumerate(valores[1:], start=2):
            if row and row[0].strip() == chave:
                linha_existente = i
                break
        if linha_existente:
            ws_cfg.update(f"B{linha_existente}", [[nova_obs]])
        else:
            ws_cfg.append_row([chave, nova_obs])
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/corrigir-tickets-empilhados", methods=["POST", "OPTIONS"])
def corrigir_tickets_empilhados():
    """
    Correção em lote (17/07/2026): na planilha original do SharePoint,
    várias linhas tinham MÚLTIPLOS tickets de fabricante empilhados
    numa célula só (ex.: "Ticket Novo: 15817311\\n15751652\\n15651216"),
    e vieram assim pro import inicial. Esse endpoint localiza cada linha
    pelo valor ANTIGO (bruto, empilhado) de Ticket/RMA + UFV, troca o
    Ticket/RMA pelo primeiro/mais recente já limpo (sem o rótulo tipo
    "Ticket Novo:"), e acrescenta os tickets mais antigos como nota no
    campo Observações (sem apagar o que já tinha lá).

    Corpo esperado: {"correcoes": [{"ticketAntigo": "...", "ufv": "...",
    "novoTicket": "...", "notaObservacao": "..."}, ...]}
    """
    if request.method == "OPTIONS":
        return ("", 204)
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = request.get_json(force=True, silent=True) or {}
    correcoes = body.get("correcoes", [])
    if not correcoes:
        return jsonify({"ok": False, "error": "nenhuma correção informada"}), 400

    idx_ticket = CHAMADOS_FABRICANTE_HEADERS.index("Ticket/RMA")
    idx_ufv = CHAMADOS_FABRICANTE_HEADERS.index("UFV")
    idx_obs = CHAMADOS_FABRICANTE_HEADERS.index("Observações")
    n_cols = len(CHAMADOS_FABRICANTE_HEADERS)
    col_letra_ticket = chr(65 + idx_ticket)
    col_letra_obs = chr(65 + idx_obs)

    try:
        ws = get_chamados_fabricante_sheet()
        todos = ws.get_all_values()

        atualizacoes, nao_encontradas = [], []
        for c in correcoes:
            ticket_antigo = (c.get("ticketAntigo") or "").strip()
            ufv = (c.get("ufv") or "").strip()
            novo_ticket = c.get("novoTicket") or ""
            nota = c.get("notaObservacao") or ""
            achou = False
            for i, row in enumerate(todos[1:], start=2):
                if len(row) < n_cols:
                    row = row + [""] * (n_cols - len(row))
                if row[idx_ticket].strip() == ticket_antigo and row[idx_ufv].strip() == ufv:
                    obs_atual = row[idx_obs].strip()
                    obs_nova = f"{obs_atual}\n{nota}".strip() if obs_atual and nota else (nota or obs_atual)
                    atualizacoes.append({"range": f"{col_letra_ticket}{i}", "values": [[novo_ticket]]})
                    if nota:
                        atualizacoes.append({"range": f"{col_letra_obs}{i}", "values": [[obs_nova]]})
                    achou = True
                    break
            if not achou:
                nao_encontradas.append({"ticketAntigo": ticket_antigo, "ufv": ufv})

        if atualizacoes:
            ws.batch_update(atualizacoes)

        return jsonify({"ok": True, "corrigidas": len(correcoes) - len(nao_encontradas),
                         "nao_encontradas": nao_encontradas}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/corrigir-ativo-chamado", methods=["POST", "OPTIONS"])
def corrigir_ativo_chamado():
    """
    Correção pontual: atualiza SÓ a coluna "Ativo" de uma ou mais linhas
    já existentes na aba ChamadosFabricante, localizadas por Ticket/RMA
    + UFV (não usa a chave composta normal do /sincronizar-chamados,
    porque nesse caso o próprio "Ativo" é o campo que está sendo
    corrigido — geralmente linhas que vieram com Ativo vazio por causa
    de célula mesclada no Excel original).

    Corpo esperado: {"correcoes": [{"ticket": "...", "ufv": "...", "novoAtivo": "..."}, ...]}
    """
    if request.method == "OPTIONS":
        return ("", 204)
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = request.get_json(force=True, silent=True) or {}
    correcoes = body.get("correcoes", [])
    if not correcoes:
        return jsonify({"ok": False, "error": "nenhuma correção informada"}), 400

    idx_ticket = CHAMADOS_FABRICANTE_HEADERS.index("Ticket/RMA")
    idx_ufv = CHAMADOS_FABRICANTE_HEADERS.index("UFV")
    n_cols = len(CHAMADOS_FABRICANTE_HEADERS)

    try:
        ws = get_chamados_fabricante_sheet()
        todos = ws.get_all_values()

        atualizacoes, nao_encontradas = [], []
        for c in correcoes:
            ticket, ufv, novo_ativo = (c.get("ticket") or "").strip(), (c.get("ufv") or "").strip(), c.get("novoAtivo") or ""
            achou = False
            for i, row in enumerate(todos[1:], start=2):
                if len(row) < n_cols:
                    row = row + [""] * (n_cols - len(row))
                if row[idx_ticket].strip() == ticket and row[idx_ufv].strip() == ufv:
                    atualizacoes.append({"range": f"A{i}", "values": [[novo_ativo]]})
                    achou = True
                    break
            if not achou:
                nao_encontradas.append({"ticket": ticket, "ufv": ufv})

        if atualizacoes:
            ws.batch_update(atualizacoes)

        return jsonify({"ok": True, "corrigidas": len(atualizacoes), "nao_encontradas": nao_encontradas}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _chamados_fabricante_itens():
    """Lê a aba ChamadosFabricante inteira + mescla as notas do dashboard
    (aba _Sistema). Reaproveitada tanto pelo endpoint GET /chamados-fabricante
    quanto pela geração do relatório semanal — uma única fonte de verdade."""
    ws = get_chamados_fabricante_sheet()
    todos = ws.get_all_values()
    notas = _mapa_notas_chamados()
    itens = []
    for row in todos[1:]:
        if len(row) < len(CHAMADOS_FABRICANTE_HEADERS):
            row = row + [""] * (len(CHAMADOS_FABRICANTE_HEADERS) - len(row))
        # linha em branco de verdade = TODAS as células vazias, não só a
        # primeira coluna (Ativo pode legitimamente vir vazio — ex.:
        # célula mesclada no Excel original — enquanto o resto da linha
        # tem dados reais; checar só row[0] descartava chamados válidos)
        if not any(cell.strip() for cell in row):
            continue
        item = dict(zip(CHAMADOS_FABRICANTE_HEADERS, row[:len(CHAMADOS_FABRICANTE_HEADERS)]))
        chave_nota = f"{item.get('Ticket/RMA','')}|{item.get('UFV','')}|{item.get('Identificação do Equipamento','')}"
        item["NotaDashboard"] = notas.get(chave_nota, "")
        itens.append(item)
    return itens


@app.route("/chamados-fabricante", methods=["GET"])
def listar_chamados_fabricante():
    """Devolve a tabela de chamados de fabricante inteira, pro frontend
    exibir no Painel de Chamados sem precisar de nenhuma cópia manual."""
    try:
        itens = _chamados_fabricante_itens()
        return jsonify({"ok": True, "itens": itens, "total": len(itens)}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/verificar-uma-os", methods=["POST", "OPTIONS"])
def verificar_uma_os():
    """
    Endpoint PÚBLICO (sem secret) pra forçar a checagem AO VIVO de uma
    única OS específica, furando a fila de prioridade do rodízio — útil
    pra quando o técnico acabou de concluir algo e não dá pra esperar a
    vez dela na fila (que pode ter dezenas de outras OSs mais "antigas"
    na frente, mesmo que essa seja a mais importante agora).
    """
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(force=True, silent=True) or {}
    id_atividade = str(body.get("id") or "").strip()
    numero_os = str(body.get("numeroOS") or "").strip()
    if not id_atividade and not numero_os:
        return jsonify({"ok": False, "error": "informe id ou numeroOS"}), 400

    ws = get_atividades_sheet()
    todos = ws.get_all_values()
    for i, row in enumerate(todos[1:], start=2):
        if len(row) < ATIV_TOTAL_COLUNAS:
            row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
        if (id_atividade and row[0].strip() == id_atividade) or (numero_os and row[13].strip() == numero_os):
            resultado = _fracttal_verificar_e_atualizar_uma_os(ws, i, row, row[13].strip())
            if resultado is None:
                return jsonify({"ok": False, "error": "falha ao consultar a Fracttal — ver logs"}), 502
            return jsonify({"ok": True, **resultado}), 200

    return jsonify({"ok": False, "error": "atividade não encontrada"}), 404


@app.route("/atualizar-os-agora", methods=["POST", "OPTIONS"])
def atualizar_os_agora():
    """
    Endpoint PÚBLICO (sem secret) pro botão "Atualizar OS" do dashboard —
    faz uma varredura de status/estado nas OSs que JÁ estão no dashboard
    (revalida ao vivo na Fracttal, corrige status interno se precisar).
    NÃO busca OS nova — isso é o botão "Auditoria", separado.

    limite_atraso_minutos=0: um clique manual é um pedido explícito de
    dado fresco AGORA — não faz sentido aplicar o filtro de "só recheca
    se já passou de 45min" (que existe pra poupar chamadas no ciclo
    automático). Sem esse filtro, cada clique sempre processa as 35 OSs
    genuinamente mais antigas da fila, garantindo que repetir o clique
    avança de verdade pela fila inteira (bug identificado em 13/07/2026:
    uma OS checada há pouco tempo — ex.: técnico concluiu logo depois da
    última verificação — ficava presa fora da lista de elegíveis pra
    sempre, não importava quantos cliques).
    """
    if request.method == "OPTIONS":
        return ("", 204)
    resultado = _auditoria_consistencia_os_core(aplicar=True, limite_atraso_minutos=0, origem="manual (botão Atualizar OS)")
    return jsonify({"ok": True, **resultado}), 200


@app.route("/rodar-auditoria-agora", methods=["POST", "OPTIONS"])
def rodar_auditoria_agora():
    """
    Endpoint PÚBLICO (sem secret) pro botão "Auditoria" do dashboard —
    varredura COMPLETA nas usinas/equipes: busca OS nova na Fracttal
    (descoberta) e revalida ao vivo um lote amplo das já existentes,
    detectando não só mudança de percentual mas também cancelamentos e
    conclusões que possam ter passado batido. Mais pesada de propósito —
    o "Atualizar OS" (mais rápido) cuida da atualização frequente.
    """
    if request.method == "OPTIONS":
        return ("", 204)
    resultado = _auditoria_completa_core(origem="manual (botão Auditoria)")
    return jsonify({"ok": True, **resultado}), 200


@app.route("/validar-integridade-relatorios", methods=["POST", "GET"])
def validar_integridade_relatorios():
    """
    Roda a validação de integridade dos relatórios (Painel de Falhas +
    Painel de Atividades, TODOS os clientes) sob demanda — mesma lógica
    que já roda automaticamente 3x/dia dentro da auditoria completa.
    Útil pra rodar manualmente logo antes de gerar um relatório, com
    confiança de que os dados estão íntegros.
    """
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    aplicar = request.args.get("apply", "true").lower() != "false"
    resultado = _validar_integridade_relatorios_core(aplicar=aplicar)
    return jsonify({"ok": True, **resultado}), 200


ATIV_CAMPO_LABEL = {
    "cliente": "Cliente", "usina": "Usina", "equipamento": "Equipamento", "descricao": "Descrição",
    "responsavel": "Responsável", "prazo": "Prazo", "prioridade": "Prioridade", "status": "Status",
    "numeroOS": "Nº OS",
}
ATIV_COL_HISTORICO = ATIV_CAMPO_COL["historico"]


@app.route("/atualizar-campo-atividade", methods=["POST", "OPTIONS"])
def atualizar_campo_atividade():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "Body inválido"}), 400

    atividade_id = str(body.get("id", "")).strip()
    field  = body.get("field", "").strip()
    value  = body.get("value", "")
    append = bool(body.get("append", False))
    editor = body.get("editor", "dashboard").strip()

    if not atividade_id or field not in ATIV_CAMPO_COL:
        return jsonify({"ok": False, "error": "id ou campo inválido"}), 400

    try:
        ws = get_atividades_sheet()
        _garantir_headers_atividades(ws)
        todos = ws.get_all_values()
        linha_idx = None
        linha_atual = None
        for i, row in enumerate(todos[1:], start=2):
            if row and str(row[0]).strip() == atividade_id:
                linha_idx = i
                linha_atual = row
                break
        if not linha_idx:
            return jsonify({"ok": False, "error": "atividade não encontrada"}), 404

        col = ATIV_CAMPO_COL[field]

        if field == "historico" and append:
            atual = linha_atual[ATIV_COL_HISTORICO - 1] if len(linha_atual) >= ATIV_COL_HISTORICO else ""
            novo = f"{atual}\n{value}".strip() if atual else value
            ws.update_cell(linha_idx, col, novo)
        else:
            valor_antigo = linha_atual[col - 1] if len(linha_atual) >= col else ""
            ws.update_cell(linha_idx, col, value)

            # Registra automaticamente a alteração no histórico cronológico
            if str(valor_antigo).strip() != str(value).strip():
                if field == "visualizado":
                    # Mensagem dedicada (17/07/2026): o formato genérico
                    # "visualizado alterado de '—' para 'sim'" confundia o
                    # Fred, parecendo uma edição de dado real em vez do que
                    # realmente é — só o rastreio de "já vi essa atividade"
                    # usado pro badge de não-lido.
                    entry = f"{agora_br().strftime('%d/%m/%Y %H:%M')} - Marcado como visualizado ({_editor_legivel(editor)})."
                else:
                    label = ATIV_CAMPO_LABEL.get(field, field)
                    entry = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - {label} alterado "
                             f"de \"{valor_antigo or '—'}\" para \"{value}\" por {_editor_legivel(editor)}.")
                hist_atual = linha_atual[ATIV_COL_HISTORICO - 1] if len(linha_atual) >= ATIV_COL_HISTORICO else ""
                novo_hist = f"{hist_atual}\n{entry}".strip() if hist_atual else entry
                ws.update_cell(linha_idx, ATIV_COL_HISTORICO, novo_hist)

            if field == "status" and _is_concluido_atividade(value):
                ws.update_cell(linha_idx, 11, agora_br().strftime('%d/%m/%Y %H:%M:%S'))  # DataConclusao

        return jsonify({"ok": True})
    except Exception as e:
        log.error(f"[Atividades] Erro ao atualizar campo: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


RE_ATUALIZACAO_ATIV = re.compile(r"ATUALIZA[CÇ][AÃ]O\s+(?:DE\s+)?(OS|ATIVIDADE)", re.IGNORECASE)

def separar_blocos_atividade(texto):
    """
    Divide uma mensagem que contenha múltiplas atualizações de OS/atividade
    em blocos individuais, um por ocorrência do título "ATUALIZACAO OS/ATIVIDADE".
    Mesmo padrão de separar_blocos() usado nas ocorrências.
    """
    partes = re.split(
        r"(?=(?:^|\n)\s*ATUALIZA[CÇ][AÃ]O\s+(?:DE\s+)?(?:OS|ATIVIDADE))",
        texto, flags=re.MULTILINE | re.IGNORECASE
    )
    blocos = [p.strip() for p in partes if p.strip()]
    return blocos if blocos else [texto]


def eh_atualizacao_atividade(texto):
    return bool(RE_ATUALIZACAO_ATIV.search(texto))


def _extrair_campo_ativ(texto, nome_regex):
    # Bullet (·, *, -, •) é opcional; separador aceita ":" ou "-"/"–"; âncora por linha
    # evita capturar texto de outros campos.
    padrao = rf"^\s*[·*\-•]?\s*(?:{nome_regex})\s*[:\-–]\s*(.+)$"
    m = re.search(padrao, texto, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""


_STATUS_ATIV_MAP = {
    "concluido": "Concluído", "concluído": "Concluído", "concluida": "Concluído", "concluída": "Concluído",
    "finalizado": "Concluído", "finalizada": "Concluído", "resolvido": "Concluído", "resolvida": "Concluído",
    "feito": "Concluído", "ok": "Concluído",
    "em andamento": "Em Andamento", "andamento": "Em Andamento", "em execucao": "Em Andamento",
    "em execução": "Em Andamento", "executando": "Em Andamento",
    "aguardando": "Aguardando", "pendente": "Aguardando",
    "aguardando peca": "Aguardando", "aguardando peça": "Aguardando",
}

_OS_FIELD_REGEX = r"(?:N[ºo°]?\s*|N[uú]mero\s*(?:da\s*)?)?OS|Ordem\s*(?:de\s*)?Servi[cç]o"
_DESCRICAO_FIELD_REGEX = r"Descri[cç][aã]o|Obs(?:erva[cç][aã]o)?|A[cç][aã]o(?:\s+Realizada)?|Servi[cç]o\s+Realizado"
_RESPONSAVEL_FIELD_REGEX = r"Respons[aá]vel|T[eé]cnico"
_STATUS_FIELD_REGEX = r"Status|Situa[cç][aã]o"


def parse_atualizacao_atividade(texto):
    id_val = _extrair_campo_ativ(texto, "ID")
    os_val = _extrair_campo_ativ(texto, _OS_FIELD_REGEX)
    status_bruto = _extrair_campo_ativ(texto, _STATUS_FIELD_REGEX)
    status_norm = _STATUS_ATIV_MAP.get(status_bruto.strip().lower(), status_bruto.strip()) if status_bruto else ""
    return {
        "id_ou_os":    os_val or id_val,
        "status":      status_norm,
        "descricao":   _extrair_campo_ativ(texto, _DESCRICAO_FIELD_REGEX),
        "responsavel": _extrair_campo_ativ(texto, _RESPONSAVEL_FIELD_REGEX),
    }


def buscar_atividade_por_id_ou_os(todos, id_ou_os):
    alvo = str(id_ou_os).strip().lstrip("0") or "0"
    for i, row in enumerate(todos[1:], start=2):
        if not row or not row[0].strip():
            continue
        row_id = str(row[0]).strip().lstrip("0") or "0"
        row_os = str(row[13]).strip().lstrip("0") if len(row) > 13 else ""
        row_os = row_os or "0"
        if (alvo != "0" and alvo == row_os) or alvo == row_id:
            return i, row
    return None


def _aplicar_update_campo_atividade(ws, linha_idx, linha_atual, field, value, editor, append=False):
    col = ATIV_CAMPO_COL[field]
    if field == "historico" and append:
        atual = linha_atual[ATIV_COL_HISTORICO - 1] if len(linha_atual) >= ATIV_COL_HISTORICO else ""
        novo = f"{atual}\n{value}".strip() if atual else value
        ws.update_cell(linha_idx, col, novo)
        return
    valor_antigo = linha_atual[col - 1] if len(linha_atual) >= col else ""
    ws.update_cell(linha_idx, col, value)
    if str(valor_antigo).strip() != str(value).strip():
        if field == "visualizado":
            entry = f"{agora_br().strftime('%d/%m/%Y %H:%M')} - Marcado como visualizado ({_editor_legivel(editor)})."
        else:
            label = ATIV_CAMPO_LABEL.get(field, field)
            entry = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - {label} alterado "
                     f"de \"{valor_antigo or '—'}\" para \"{value}\" por {_editor_legivel(editor)}.")
        hist_atual = linha_atual[ATIV_COL_HISTORICO - 1] if len(linha_atual) >= ATIV_COL_HISTORICO else ""
        novo_hist = f"{hist_atual}\n{entry}".strip() if hist_atual else entry
        ws.update_cell(linha_idx, ATIV_COL_HISTORICO, novo_hist)
    if field == "status" and _is_concluido_atividade(value):
        ws.update_cell(linha_idx, 11, agora_br().strftime('%d/%m/%Y %H:%M:%S'))


def _processar_um_bloco_atividade(texto, editor="tecnico-whatsapp"):
    dados = parse_atualizacao_atividade(texto)
    if not dados["id_ou_os"]:
        try:
            enviar_push(
                titulo="⚠️ Atualização de OS sem Nº OS/ID",
                corpo=f"Mensagem recebida de {editor}, mas não foi possível identificar o campo Nº OS ou ID. Confira o formato da mensagem.",
                tipo="geral",
            )
        except Exception as e:
            log.error(f"[Atividades WhatsApp] Falha ao enviar push de erro: {e}")
        return {"ok": False, "motivo": "sem ID ou Nº OS na mensagem"}

    ws = get_atividades_sheet()
    todos = ws.get_all_values()
    encontrada = buscar_atividade_por_id_ou_os(todos, dados["id_ou_os"])
    if not encontrada:
        try:
            enviar_push(
                titulo="⚠️ Atualização de OS não vinculada",
                corpo=f"Técnico ({editor}) informou Nº OS/ID \"{dados['id_ou_os']}\" mas nenhuma atividade correspondente foi encontrada no painel.",
                tipo="geral",
            )
        except Exception as e:
            log.error(f"[Atividades WhatsApp] Falha ao enviar push de erro: {e}")
        return {"ok": False, "motivo": f"atividade {dados['id_ou_os']} não encontrada"}

    linha_idx, linha_atual = encontrada

    if dados["responsavel"]:
        _aplicar_update_campo_atividade(ws, linha_idx, linha_atual, "responsavel", dados["responsavel"], editor)
        todos = ws.get_all_values(); linha_atual = todos[linha_idx - 1]

    if dados["descricao"]:
        entry = f"{agora_br().strftime('%d/%m/%Y %H:%M')} - {_editor_legivel(editor)}: {dados['descricao']}"
        _aplicar_update_campo_atividade(ws, linha_idx, linha_atual, "historico", entry, editor, append=True)
        todos = ws.get_all_values(); linha_atual = todos[linha_idx - 1]

    if dados["status"]:
        numero_os_linha = linha_atual[13].strip() if len(linha_atual) > 13 else ""
        if numero_os_linha:
            # OS vinculada à Fracttal: o status NUNCA é escrito a partir do
            # texto da mensagem — só a Fracttal (via API e as automações
            # já existentes: rodízio, auditoria, descoberta) decide o
            # status real. A mensagem do técnico só serve de GATILHO pra
            # checar a Fracttal imediatamente, sem esperar a próxima
            # rodada de auditoria. Antes disso, o texto do WhatsApp
            # escrevia o status direto, sem nenhuma validação — foi
            # exatamente isso que causou a OS 8867 aparecer como
            # "Concluída" no painel enquanto a Fracttal ainda mostrava
            # "Em Processo" (relatado pelo Fred em 15/07/2026, que pediu
            # essa mudança de arquitetura em vez de só reconciliar depois).
            entry = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - {_editor_legivel(editor)} reportou status "
                     f"\"{dados['status']}\" pelo WhatsApp (confirmado em seguida direto com a Fracttal).")
            _aplicar_update_campo_atividade(ws, linha_idx, linha_atual, "historico", entry, editor, append=True)
            try:
                todos_frescos = ws.get_all_values()
                linha_fresca = todos_frescos[linha_idx - 1]
                _fracttal_verificar_e_atualizar_uma_os(ws, linha_idx, linha_fresca, numero_os_linha,
                                                        enviar_notificacao=False)
            except Exception as e:
                log.error(f"[Atividades WhatsApp] Falha ao checar a Fracttal pra OS {numero_os_linha}: {e}")
        else:
            # atividade manual, sem vínculo com nenhuma OS da Fracttal —
            # não existe outra fonte de verdade pra ela, então o status
            # informado pelo técnico continua sendo aceito diretamente.
            _aplicar_update_campo_atividade(ws, linha_idx, linha_atual, "status", dados["status"], editor)

    return {"ok": True, "id": linha_atual[0]}


def processar_atualizacao_atividade(texto, editor="tecnico-whatsapp"):
    blocos = separar_blocos_atividade(texto)
    resultados = [_processar_um_bloco_atividade(bloco, editor) for bloco in blocos]
    return {
        "ok": any(r.get("ok") for r in resultados),
        "total_blocos": len(resultados),
        "resultados": resultados,
    }


@app.route("/corrigir-prioridade-atividades", methods=["GET"])
def corrigir_prioridade_atividades():
    """
    Rota de manutenção pontual: corrige linhas antigas do Painel de Atividades
    cuja celula de Prioridade foi sobrescrita com um valor de Status (bug do
    mapeamento de colunas anterior a correcao). So mexe em linhas onde o valor
    atual de Prioridade nao e Alta/Media/Baixa - ou seja, so nas corrompidas.
    """
    VALORES_VALIDOS_PRIORIDADE = {"alta", "media", "média", "baixa"}
    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
        corrigidas = []
        for i, row in enumerate(todos[1:], start=2):
            if not row or not row[0].strip():
                continue
            prioridade_atual = row[7].strip() if len(row) > 7 else ""
            if prioridade_atual.lower() not in VALORES_VALIDOS_PRIORIDADE:
                ws.update_cell(i, 8, "Alta")  # coluna H = Prioridade
                hist_atual = row[11] if len(row) > 11 else ""
                entry = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - Prioridade corrigida de "
                         f"\"{prioridade_atual or '—'}\" para \"Alta\" (correcao de dado legado) por sistema.")
                novo_hist = f"{hist_atual}\n{entry}".strip() if hist_atual else entry
                ws.update_cell(i, 12, novo_hist)  # coluna L = Historico
                corrigidas.append({"linha": i, "id": row[0], "de": prioridade_atual, "para": "Alta"})
        return jsonify({"ok": True, "corrigidas": corrigidas, "total": len(corrigidas)})
    except Exception as e:
        log.error(f"[corrigir-prioridade-atividades] Erro: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/converter-atividade-em-ocorrencia", methods=["POST", "OPTIONS"])
def converter_atividade_em_ocorrencia():
    """
    Converte uma Atividade em uma Ocorrência: cria uma nova linha no Painel de
    Falhas com os dados da atividade (incluindo o histórico cronológico
    transferido), e marca a atividade original como "Convertida em Ocorrência".
    """
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "Body inválido"}), 400

    atividade_id = str(body.get("id", "")).strip()
    editor = body.get("editor", "dashboard").strip()
    if not atividade_id:
        return jsonify({"ok": False, "error": "id é obrigatório"}), 400

    try:
        ws_ativ = get_atividades_sheet()
        todos_ativ = ws_ativ.get_all_values()
        linha_idx = None
        linha_atual = None
        for i, row in enumerate(todos_ativ[1:], start=2):
            if row and str(row[0]).strip() == atividade_id:
                linha_idx = i
                linha_atual = row
                break
        if not linha_idx:
            return jsonify({"ok": False, "error": "atividade não encontrada"}), 404

        # linha_atual: [ID, Cliente, Usina, Equipamento, Descricao, Responsavel, Prazo,
        #               Prioridade, Status, DataCriacao, DataConclusao, Historico, Editor]
        cliente     = linha_atual[1] if len(linha_atual) > 1 else ""
        usina       = linha_atual[2] if len(linha_atual) > 2 else ""
        equipamento = linha_atual[3] if len(linha_atual) > 3 else ""
        descricao   = linha_atual[4] if len(linha_atual) > 4 else ""
        responsavel = linha_atual[5] if len(linha_atual) > 5 else ""
        prazo       = linha_atual[6] if len(linha_atual) > 6 else ""
        status_ativ = linha_atual[8] if len(linha_atual) > 8 else ""
        historico_ativ = linha_atual[11] if len(linha_atual) > 11 else ""
        numero_os_ativ = linha_atual[13] if len(linha_atual) > 13 else ""

        if not equipamento:
            equipamento = "Não informado"

        nota_conversao = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - Convertida do Painel de "
                           f"Atividades (Atividade #{atividade_id}) por {_editor_legivel(editor)}.")
        historico_ocorrencia = nota_conversao
        if historico_ativ:
            historico_ocorrencia += "\n" + historico_ativ

        status_ocorrencia = status_ativ if status_ativ and status_ativ.lower() not in (
            "concluído", "concluido", "convertida em ocorrência") else "Em Aberto"

        ws_falhas = get_sheet()
        todos_falhas = carregar_planilha(ws_falhas)
        novo_id_ocorrencia = proximo_id(todos_falhas)

        dados = {
            "cliente":      cliente,
            "usina":        usina,
            "equipamento":  equipamento,
            "falha":        descricao,
            "causa":        "",
            "equip_impact": equipamento,
            "acao":         f"Responsável original: {responsavel}." if responsavel else "",
            "status":       status_ocorrencia,
            "os":           numero_os_ativ,
            "historico":    historico_ocorrencia,
        }
        gravar_nova_ocorrencia(ws_falhas, todos_falhas, dados)

        # Marca a atividade original como convertida e registra no histórico dela
        ws_ativ.update_cell(linha_idx, 9, "Convertida em Ocorrência")  # coluna I = Status
        entry = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - Convertida em ocorrência "
                 f"#{novo_id_ocorrencia} por {_editor_legivel(editor)}.")
        novo_hist_ativ = f"{historico_ativ}\n{entry}".strip() if historico_ativ else entry
        ws_ativ.update_cell(linha_idx, 12, novo_hist_ativ)  # coluna L = Historico

        log.info(f"[converter-atividade] Atividade #{atividade_id} -> Ocorrência #{novo_id_ocorrencia}")
        return jsonify({"ok": True, "novaOcorrenciaId": novo_id_ocorrencia})
    except Exception as e:
        log.error(f"[converter-atividade-em-ocorrencia] Erro: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Geração de texto de OS via Gemini (gratuito), com fallback local ───────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Chave separada (projeto próprio no Google AI Studio) usada só em
# diagnósticos/testes ao vivo, pra nunca disputar cota com o uso real do
# Fred. Só entra em ação quando explicitamente pedido (?diagnostico=true
# ou header X-Usar-Chave-Teste), nunca no fluxo normal do dashboard.
GEMINI_API_KEY_TESTE = os.environ.get("GEMINI_API_KEY_TESTE", "")
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def _montar_prompt_os(d):
    return f"""Aja como um Engenheiro e Especialista em Operação e Manutenção (O&M), com foco em Usinas Solares Fotovoltaicas, sistemas elétricos, mecânicos e atividades de facilities (limpeza, conservação, manutenções civis).

Sua tarefa é redigir Ordens de Serviço (OS) baseadas na solicitação abaixo. Transforme a solicitação em um texto objetivo, profissional, técnico e estritamente padronizado.

REGRA DE SEPARAÇÃO EM MÚLTIPLAS OS (MUITO IMPORTANTE, leia antes de tudo):
A solicitação abaixo pode descrever mais de uma frente de trabalho de uma vez (texto colado direto de anotações de campo, por exemplo). Você deve dividir em OSs SEPARADAS sempre que identificar:
- **Usinas diferentes** — cada usina distinta vira sua própria OS.
- **Equipamentos/sistemas diferentes** dentro da mesma usina — ex.: inversor e tracker são frentes diferentes, viram OSs separadas; trafo e string também.
EXCEÇÃO — CÂMERAS/CFTV: todo o conteúdo relacionado a câmeras/CFTV (reposicionamento, foco, teste, instalação, mesmo que em mais de uma usina ou câmera) fica **numa única OS só**, mesmo cruzando usinas — o sistema de CFTV é tratado como uma frente de trabalho só, não se separa por usina.
Se a solicitação já for sobre uma coisa só (uma usina, um equipamento, ou só câmeras), gere apenas UMA OS normalmente.

REGRAS DE FORMATAÇÃO (OBRIGATÓRIO) — aplique a cada OS individualmente:
- Esqueça introduções, conclusões, saudações, tabelas, ou seções como "Objetivo", "Descrição", "Responsáveis" ou "Evidências".
- Cada OS deve conter APENAS o "Título" e os "Comentários". Siga este modelo exato:

Título: [Nome curto e direto da atividade]
Comentários:

1. [Passo 1 do procedimento]
2. [Passo 2 do procedimento]
3. [Passo 3...]

REGRAS DE ESCRITA E VOCABULÁRIO:
- O texto deve ser curto, claro e voltado para a execução operacional em campo ou inserção em sistema de OS.
- Não invente informações ou equipamentos que não foram solicitados, mas garanta que o passo a passo faça sentido técnico.
- Integre orientações de segurança (EPIs, desenergização, sinalização) diretamente nos passos da atividade.
- Não repita a mesma ideia em mais de um item.
- Para atividades de acompanhamento/fiscalização, inicie os passos com verbos como: Acompanhar, verificar, conferir, registrar, avaliar, validar.
- Para atividades de execução direta, inicie os passos com verbos como: Realizar, executar, corrigir, ajustar, efetuar, acessar, inspecionar.

REGRA ESPECÍFICA DA GRID CO. (OBRIGATÓRIA, além das regras acima):
- Só inclua um passo pedindo autorização do COS (centro de operações) se a atividade envolver desligamento de inversor, desligamento da usina inteira, ou trabalho em SKID ou na Cabine de Medição Primária. Nesses casos, inclua um item pedindo autorização do COS antes da intervenção.
- Em qualquer outro caso, termine com um item dizendo que a atividade não envolve manobra elétrica e não é necessário acionar o COS.

EXEMPLOS DO PADRÃO ESPERADO (cada um é o conteúdo de UMA OS):

Exemplo 1 (Atividade de Execução/Facilities)
Título: Limpeza de caixa d'água
Comentários:

1. Fechar o registro de entrada de água (boia) com antecedência e isolar a área de acesso.
2. Esvaziar a caixa até que reste apenas cerca de um palmo de água no fundo.
3. Esfregar as paredes e o fundo utilizando escovas macias e exclusivas para este fim, sem uso de produtos químicos não homologados.
4. Esvaziar a água suja, realizar o enxágue das paredes, reabrir o registro de entrada e fechar a tampa de forma hermética.

Exemplo 2 (Atividade de Diagnóstico/Elétrica)
Título: Inversor com aparente limitação de potência
Comentários:

1. Acessar o sistema de monitoramento (supervisório) para verificar alarmes ativos, histórico de geração e indicação de derating.
2. Realizar inspeção visual no inversor em campo, checando o funcionamento dos ventiladores e desobstrução das grades de ventilação.
3. Inspecionar as medições de tensão e corrente nas entradas CC com alicate amperímetro para garantir que a queda de potência não seja causada por falha nos módulos ou sujeira.

Exemplo 3 (Atividade de Acompanhamento)
Título: Acompanhamento de roçagem
Comentários:

1. Acompanhar a execução da roçagem na área designada, confirmando a delimitação do espaço.
2. Verificar a sinalização e o uso correto de EPIs pela equipe terceira durante toda a atividade.
3. Conferir se o serviço foi realizado conforme o planejamento, garantindo a integridade dos cabos e estruturas próximas.
4. Registrar o andamento com evidências fotográficas e anotar eventuais pendências para correção.

Exemplo 4 (Atividade de Ajuste — CFTV, várias câmeras/usinas ficam JUNTAS numa OS só)
Título: Reposicionamento de câmeras de CFTV
Comentários:

1. Verificar a posição atual de cada câmera e o campo de visão afetado.
2. Realizar o reposicionamento físico conforme a necessidade operacional, ajustando inclinação e direcionamento.
3. Validar a visualização da imagem no sistema central de monitoramento para confirmar a cobertura desejada.
4. Registrar a atividade e as evidências de antes e depois da intervenção.

Exemplo 5 (Visita Técnica Semanal — PADRÃO FIXO da Grid Co., use exatamente este texto sempre que a solicitação pedir "visita técnica semanal", "ronda semanal" ou equivalente, sem alterar os passos, só adaptando se algo específico for pedido a mais)
Título: Visita Técnica Semanal
Comentários:

1. Realizar inspeção visual da vegetação na área da usina, avaliando a necessidade de roçagem e proximidade com os módulos e equipamentos.
2. Inspecionar a sujidade dos módulos fotovoltaicos, registrando o nível de acúmulo e a necessidade de limpeza.
3. Verificar as condições gerais da usina, incluindo vias de acesso, drenagem e integridade das estruturas.
4. Conferir o cercamento perimetral, identificando pontos de vulnerabilidade ou danos.
5. Inspecionar visualmente os inversores, verificando a limpeza externa, funcionamento dos ventiladores e ausência de alarmes no display.
6. Coletar os dados de geração de cada inversor (dados de geração diária, de todos os dias deste mês).
7. Acessar o sistema de CFTV para verificar o funcionamento das câmeras, qualidade das imagens e cobertura das áreas.
8. Registrar todas as observações e evidências fotográficas para cada item inspecionado.
9. A atividade não envolve manobra elétrica e não é necessário acionar o COS.

Aplique exclusivamente este padrão. Não invente números de ticket, causas, nomes ou dados que não foram informados abaixo. Não repita a mesma OS mais de uma vez.

FORMATO DE SAÍDA (OBRIGATÓRIO): responda APENAS com um JSON válido (sem markdown, sem crase, sem texto antes ou depois), no formato:
{{"textos": ["Título: ...\\nComentários:\\n\\n1. ...\\n2. ...", "Título: ...\\nComentários:\\n\\n1. ..."]}}
Cada item da lista é o texto completo de uma OS, no padrão exato descrito acima. Se só houver uma frente de trabalho, a lista tem um item só.

Dados da solicitação:
- Cliente: {d.get("cliente") or "não informado"}
- Usina: {d.get("usina") or "não informado"}
- Equipamento: {d.get("equipamento") or "não informado"}
- Falha/Descrição: {d.get("falha") or "não informado"}
- Causa: {d.get("causa") or "não informado"}
- Ação já realizada: {d.get("acao") or "não informado"}
- Histórico: {d.get("historico") or "não informado"}
- Responsável: {d.get("responsavel") or "não informado"}"""


def _indice_dia_util(data_str, hoje):
    """Converte uma data (dd/mm/aaaa) em 'quantos dias úteis a partir de
    amanhã' ela representa (0 = primeiro dia útil disponível). Ignora fins
    de semana na contagem. Retorna None se a data já passou ou é hoje."""
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", data_str)
    if not m:
        return None
    dt = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    d = hoje.replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(d, datetime) and d.tzinfo:
        dt = dt.replace(tzinfo=d.tzinfo)
    if dt <= d:
        return None
    idx = -1
    cursor = d + timedelta(days=1)
    while cursor <= dt:
        if cursor.weekday() < 5:
            idx += 1
        cursor += timedelta(days=1)
    return idx if idx >= 0 else None


def _dia_util_por_indice(idx, hoje):
    """Inverso de _indice_dia_util: dado um índice (0 = primeiro dia útil
    disponível), devolve a data (dd/mm/aaaa) correspondente."""
    d = hoje.replace(hour=0, minute=0, second=0, microsecond=0)
    cursor = d + timedelta(days=1)
    contador = -1
    while True:
        if cursor.weekday() < 5:
            contador += 1
            if contador == idx:
                return cursor.strftime("%d/%m/%Y")
        cursor += timedelta(days=1)


def _comprimir_agenda_reprogramacao(sugestao, hoje):
    """Garantia extra e determinística, além do prompt: se a IA, mesmo com
    a lista de dias úteis explícita, deixar o primeiro dia disponível sem
    uso (ex.: começar só na terça quando segunda estava livre), desloca
    TODA a agenda sugerida pra trás em dias úteis, preservando a ordem e
    os turnos, até o dia mais cedo usado virar o primeiro dia disponível.
    Roda in-place no dict."""
    itens = sugestao.get("reprogramacoes", [])
    indices = []
    for item in itens:
        idx = _indice_dia_util((item.get("dataSugerida") or "").strip(), hoje)
        item["_idx_dia_util"] = idx
        if idx is not None:
            indices.append(idx)
    if not indices:
        return
    deslocamento = min(indices)
    if deslocamento <= 0:
        for item in itens:
            item.pop("_idx_dia_util", None)
        return
    for item in itens:
        idx = item.pop("_idx_dia_util", None)
        if idx is not None:
            item["dataSugerida"] = _dia_util_por_indice(idx - deslocamento, hoje)


def _corrigir_fins_de_semana(sugestao):
    """Garantia extra além do prompt: se a IA, mesmo assim, sugerir uma
    data em sábado ou domingo, empurra pra segunda-feira seguinte. Roda
    depois da resposta da IA, direto no dict (in-place)."""
    for item in sugestao.get("reprogramacoes", []):
        data_str = (item.get("dataSugerida") or "").strip()
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", data_str)
        if not m:
            continue
        dt = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        dias_ate_segunda = {5: 2, 6: 1}.get(dt.weekday())  # 5=sábado, 6=domingo
        if dias_ate_segunda:
            dt_corrigida = dt + timedelta(days=dias_ate_segunda)
            item["dataSugerida"] = dt_corrigida.strftime("%d/%m/%Y")


def _proximos_dias_uteis(a_partir_de, quantidade=12):
    """Retorna uma lista de (data_str, nome_dia_semana) dos próximos N dias
    úteis (seg-sex) a partir do dia seguinte a `a_partir_de`. Calculado em
    Python, não deixado por conta da IA — remove qualquer chance de erro
    de cálculo de data/dia da semana por parte do modelo."""
    nomes = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
             "sexta-feira", "sábado", "domingo"]
    dias = []
    d = a_partir_de + timedelta(days=1)
    while len(dias) < quantidade:
        if d.weekday() < 5:  # 0-4 = seg a sex
            dias.append((d.strftime("%d/%m/%Y"), nomes[d.weekday()]))
        d += timedelta(days=1)
    return dias


def _chamar_gemini_com_retry(payload, timeout=45, tentativas=3, usar_chave_teste=False):
    """Chama a API do Gemini com retry automático em caso de 429 (limite
    de taxa) — espera crescente entre tentativas (2s, 5s, 10s). Um pico
    passageiro de uso (ex.: várias chamadas em sequência rápida) costuma
    se resolver sozinho em poucos segundos; isso evita expor esse erro
    direto pro usuário na maioria dos casos. Levanta a exceção normalmente
    se todas as tentativas falharem (ex.: cota diária realmente esgotada,
    que não se resolve só esperando).

    usar_chave_teste=True usa GEMINI_API_KEY_TESTE (projeto separado no
    Google AI Studio) em vez da chave de produção — só pra diagnósticos,
    nunca no fluxo normal do dashboard."""
    chave = (GEMINI_API_KEY_TESTE if usar_chave_teste and GEMINI_API_KEY_TESTE else GEMINI_API_KEY)
    esperas = [2, 5, 10]
    ultima_excecao = None
    for tentativa in range(tentativas):
        try:
            resp = requests.post(f"{GEMINI_URL}?key={chave}", json=payload, timeout=timeout)
            if resp.status_code == 429 and tentativa < tentativas - 1:
                time.sleep(esperas[tentativa])
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.HTTPError as e:
            ultima_excecao = e
            if resp.status_code == 429 and tentativa < tentativas - 1:
                time.sleep(esperas[tentativa])
                continue
            raise
    raise ultima_excecao


def _montar_prompt_priorizacao(atividades, hoje_str):
    mapa_cluster = _mapa_cluster_usina()
    linhas = []
    for a in atividades:
        cluster = mapa_cluster.get((a.get("usina") or "").strip(), "não mapeado")
        linhas.append(
            f"- id={a['id']} | OS={a.get('numeroOS') or '—'} | Cliente={a['cliente']} | Usina={a['usina']} | "
            f"Cluster/Região={cluster} | Equipamento={a.get('equipamento') or '—'} | "
            f"Descrição={a.get('descricao') or '—'} | Responsável/Equipe={a.get('responsavel') or 'não informado'} | "
            f"Prioridade={a.get('prioridade') or 'Média'} | Prazo={a.get('prazo') or 'sem prazo definido'} | "
            f"Estado Fracttal={a.get('statusOS') or '—'} | % concluído={a.get('percentualOS') or '0'}"
        )
    lista_atividades = "\n".join(linhas)

    return f"""Aja como um Engenheiro(a) de O&M Sênior especialista em usinas fotovoltaicas, responsável por decidir a ordem de execução das atividades de campo do dia para múltiplas equipes técnicas espalhadas por várias usinas.

CONTEXTO:
Hoje é {hoje_str}. Há um volume grande de atividades/OS em aberto e fica difícil pro supervisor saber, à primeira vista, o que priorizar. Seu trabalho é ler a lista completa abaixo e devolver uma ordem de prioridade clara e justificada — não é uma escala de quem faz o quê, é um raio-x de "o que mais importa agora e por quê".

CRITÉRIOS DE PRIORIZAÇÃO (avalie todos, na ordem de peso abaixo):

1. IMPACTO NA GERAÇÃO/EFICIÊNCIA DA USINA — o critério mais pesado. Qualquer atividade cuja descrição indique equipamento parado, desligado, offline, string sem corrente, inversor fora, usina total ou parcialmente fora de operação, deve subir para o topo, porque isso é dinheiro de geração perdido a cada hora que passa. Uma atividade cosmética ou de rotina (limpeza, organização, inspeção sem falha) nunca deve furar a frente de uma atividade que representa geração parada.

2. CRITICIDADE + URGÊNCIA DE PRAZO — cruze o campo "Prioridade" (Alta/Média/Baixa) com o quanto falta pro prazo (ou se já está vencido). Uma "Alta" vencida ou vencendo hoje/amanhã é mais urgente que uma "Alta" com prazo confortável. Prazo vencido não é sinônimo automático de mais urgente se o impacto na geração for baixo — pese os dois juntos.

3. DEPENDÊNCIA ENTRE ATIVIDADES — isso é essencial e frequentemente ignorado numa lista simples por prazo. Antes de finalizar a ordem, procure ativamente por atividades na MESMA usina (ou no mesmo equipamento) que tenham uma relação de pré-requisito entre si, e NUNCA sugira a atividade dependente antes da que ela depende. Exemplos do tipo de raciocínio esperado (use como padrão, não como lista fechada — identifique outros casos parecidos na lista real):
   - Recomposição/emenda de cabos SEMPRE antes de amarração/organização dos mesmos cabos (não dá pra organizar o que ainda não foi consertado).
   - Diagnóstico/inspeção de uma falha SEMPRE antes do reparo dela (não dá pra consertar sem saber a causa confirmada).
   - Reparo elétrico ou troca de componente SEMPRE antes de teste/comissionamento daquele circuito.
   - Verificação de aterramento/segurança SEMPRE antes de energizar ou religar o equipamento.
   - Troca de fusível ou proteção SEMPRE antes de testar a string/circuito protegido por ele.
   - Reparo físico/estrutural numa string ou trilho SEMPRE antes de limpeza de módulos naquele mesmo trecho (limpar antes só suja de novo durante o reparo).
   - Calibração de sensor SEMPRE depois de qualquer reparo físico no equipamento monitorado por ele (calibrar antes exige recalibrar depois).
   - Controle de vegetação/acesso ao redor de um equipamento SEMPRE antes de manutenção elétrica que exija acesso seguro àquele ponto.
   Se identificar uma dependência assim na lista, a atividade pré-requisito deve aparecer com prioridade igual ou maior que a dependente, mesmo que isoladamente pareça menos urgente — porque atrasá-la atrasa a outra também.

4. AGRUPAMENTO POR EQUIPE E USINA (redução de deslocamento) — REGRA RÍGIDA, do mesmo peso que os critérios 1-3, não é só desempate: uma mesma equipe/responsável ("Responsável/Equipe" na lista) NUNCA deve aparecer com atividades de USINAS DIFERENTES intercaladas na ordem final. Se a equipe "X" tem atividades em Usina A e também em Usina B, TODAS as atividades da equipe X na Usina A devem aparecer em posições consecutivas antes de qualquer atividade da equipe X na Usina B (ou vice-versa) — nunca A, depois B, depois A de novo. Use o campo "Cluster/Região" do mesmo jeito: atividades do mesmo cluster, mesmo que de responsáveis diferentes, também devem ficar próximas na lista sempre que os critérios 1-3 permitirem. Isso existe porque a ordem de prioridade também comunica o que a equipe deve fazer em sequência no mesmo deslocamento — intercalar usinas diferentes pra mesma equipe sugere um vai-e-vem fisicamente inviável no mesmo dia.

REGRA DE OURO PRA COMBINAR OS 4 CRITÉRIOS: primeiro ordene por impacto/prazo/dependência (1-3). DEPOIS, ao montar a lista final, reagrupe mantendo blocos contíguos por equipe+usina — dentro de um mesmo bloco, a ordem interna já definida pelos critérios 1-3 se mantém; entre blocos, o bloco com a atividade mais urgente (critérios 1-3) do grupo vem primeiro.

ATIVIDADES EM ABERTO HOJE:
{lista_atividades}

FORMATO DE SAÍDA (OBRIGATÓRIO):
Responda APENAS com um JSON válido (sem markdown, sem blocos de código com crase, sem texto antes ou depois), no formato:

{{
  "resumo_executivo": "2-3 frases dando o panorama geral do dia: quantas coisas críticas existem, algum padrão de dependência ou agrupamento geográfico relevante encontrado",
  "prioridades": [
    {{
      "posicao": <número da posição na fila, 1 = mais prioritário>,
      "id": "<id da atividade, exatamente como veio na lista>",
      "numeroOS": "<número da OS, exatamente como veio na lista>",
      "usina": "<usina>",
      "cluster": "<cluster/região>",
      "equipamento": "<equipamento/descrição resumida>",
      "motivo": "<justificativa curta e direta — cite o critério principal: impacto na geração, prazo, dependência de outra atividade (cite qual), ou agrupamento geográfico>"
    }}
  ],
  "mensagem_pronta": "<texto já formatado, pronto pra copiar e enviar, começando com um cabeçalho tipo '🎯 PRIORIDADES DE HOJE — <data>', listando as atividades em ordem numerada com usina + equipamento + motivo resumido em 1 linha cada, agrupadas visualmente por cluster quando fizer sentido. Use emojis moderadamente (🔴 pra crítico/geração parada, ⚠️ pra urgente por prazo, 📍 pra agrupamento geográfico). Máximo as 15 primeiras posições — se houver mais atividades, termine com uma linha tipo 'e mais N atividades de prioridade menor no painel'.>"
}}

Não invente atividades que não estão na lista. Inclua em "prioridades" todas as atividades recebidas, ordenadas do id 1 (mais prioritário) até a última — mas em "mensagem_pronta" mostre só o topo (até 15), como instruído."""


def _montar_prompt_reprogramacao(atividades, hoje_str, proximos_dias_uteis):
    linhas = []
    for a in atividades:
        linhas.append(
            f"- id={a['id']} | OS={a.get('numeroOS') or '—'} | Cliente={a['cliente']} | Usina={a['usina']} | "
            f"Equipamento={a.get('equipamento') or '—'} | Responsável/Equipe={a.get('responsavel') or 'não informado'} | "
            f"Prioridade={a.get('prioridade') or 'Média'} | Prazo atual={a.get('prazo') or 'sem prazo definido'} | "
            f"Status={a.get('status')}"
        )
    lista_atividades = "\n".join(linhas)
    lista_dias_uteis = "\n".join(f"- {data} ({nome})" for data, nome in proximos_dias_uteis)
    primeiro_dia = proximos_dias_uteis[0][0]
    primeiro_dia_nome = proximos_dias_uteis[0][1]

    return f"""Aja como um Programador(a) de Manutenção Sênior de uma empresa de O&M de usinas solares fotovoltaicas. Você é especialista em otimizar rotas e agendas de equipes de campo, minimizando deslocamento e maximizando produtividade.

CONTEXTO:
Hoje é {hoje_str}. Abaixo está a lista de atividades/OS em aberto que precisam ser reprogramadas para datas futuras.

DIAS ÚTEIS DISPONÍVEIS PRA REPROGRAMAR (já calculados, use SOMENTE essas datas — não calcule por conta própria, não use nenhuma data fora desta lista):
{lista_dias_uteis}

O primeiro dia útil disponível é {primeiro_dia} ({primeiro_dia_nome}) — a menos que os turnos desse dia já estejam no limite do critério 3 abaixo, ele DEVE ser usado por pelo menos uma equipe. Nunca pule esse primeiro dia sem necessidade real de agenda.

REGRA MAIS IMPORTANTE (NUNCA VIOLAR):
- Cada "Responsável/Equipe" representa uma equipe de campo fisicamente alocada. Uma mesma equipe NUNCA pode ter atividades programadas em USINAS DIFERENTES no mesmo dia — o deslocamento entre usinas inviabiliza isso. Se a equipe tem atividades em mais de uma usina, agrupe-as em dias diferentes, dedicando um ou mais dias consecutivos inteiros a cada usina antes de mover a equipe pra próxima.
- Atividades da MESMA equipe na MESMA usina podem (e devem, quando fizer sentido) ser agrupadas no mesmo dia ou em dias consecutivos, pra reduzir viagens.

REGRA FIXA DE DIA DA SEMANA (também NUNCA VIOLAR — tem prioridade sobre os critérios de preenchimento por proximidade abaixo): a equipe do Cláudio Ferreira (cluster CE Leste 01) tem dias fixos por cliente, definidos pelo Fred:
- Atividades em usinas do cliente GD Energy (ex.: Guajirú, Sol do Norte I, Sol do Norte II) alocadas a essa equipe: "dataSugerida" DEVE cair numa quarta-feira (dentre as datas disponíveis na lista de dias úteis).
- Atividades em usinas do cliente Alves Lima (ABC Morada Nova) alocadas a essa equipe: "dataSugerida" DEVE cair numa segunda-feira (dentre as datas disponíveis na lista de dias úteis).
- Se não houver nenhuma quarta-feira (ou segunda-feira, conforme o caso) disponível na lista de dias úteis fornecida, escolha a data disponível mais próxima e explique isso claramente na "justificativa".
- Essa regra vale só pra essa equipe/cliente específicos — não aplique padrão parecido pra outras equipes sem instrução explícita.

OUTROS CRITÉRIOS DE PRIORIZAÇÃO (em ordem de importância):
1. Atividades com prioridade "Alta" devem ser reprogramadas para as datas mais próximas possíveis.
2. Atividades que já estão com prazo vencido ou vencendo nos próximos dias têm urgência maior que as sem prazo definido ou com prazo distante.
3. SEJA CONSERVADOR NA QUANTIDADE POR DIA — isso é crítico. Grande parte dessas atividades já está atrasada justamente porque a agenda anterior foi otimista demais e não sobrou tempo real de execução, deslocamento dentro da própria usina, imprevistos e deslocamento até o próximo compromisso. Distribua no máximo 1 atividade por turno (manhã OU tarde) por equipe — ou seja, no máximo 2 atividades por dia por equipe — a menos que sejam claramente rápidas/simples (ex.: inspeção visual, verificação de temperatura), caso em que até 2 por turno é aceitável. Nunca mais que isso.
4. REGRA RÍGIDA, SEM NENHUMA EXCEÇÃO: a "dataSugerida" de TODA atividade precisa ser uma das datas listadas em "DIAS ÚTEIS DISPONÍVEIS" acima. Nunca use uma data que não esteja nessa lista — ela já exclui sábados e domingos pra você.
5. Preencha os dias úteis mais próximos primeiro, na ordem em que aparecem na lista — não pule um dia disponível pra frente sem necessidade. Só avance pra um dia mais distante da lista quando os turnos dos dias mais próximos já estiverem no limite do critério 3.
6. Para cada atividade, defina também um TURNO (manhã ou tarde) dentro do dia sugerido, respeitando o limite de 1-2 atividades por turno do critério 3.

ATIVIDADES A REPROGRAMAR:
{lista_atividades}

FORMATO DE SAÍDA (OBRIGATÓRIO):
Responda APENAS com um JSON válido (sem markdown, sem blocos de código com crase, sem texto antes ou depois), no formato:

{{
  "resumo": "1-2 frases explicando a lógica geral usada no agrupamento",
  "reprogramacoes": [
    {{
      "id": "<id da atividade, exatamente como veio na lista>",
      "numeroOS": "<número da OS, exatamente como veio na lista>",
      "usina": "<usina>",
      "equipamento": "<equipamento/atividade, exatamente como veio na lista>",
      "responsavel": "<responsável/equipe>",
      "dataAtual": "<prazo atual, ou 'sem prazo definido'>",
      "dataSugerida": "<nova data sugerida, formato dd/mm/aaaa, OBRIGATORIAMENTE um dia de segunda a sexta-feira>",
      "turno": "<'manhã' ou 'tarde'>",
      "justificativa": "<motivo curto da escolha dessa data/turno, mencionando o agrupamento por usina/equipe quando relevante>"
    }}
  ]
}}

Não invente atividades que não estão na lista. Não omita nenhuma atividade da lista — toda atividade precisa aparecer em "reprogramacoes" com uma data sugerida (sempre dia útil) e um turno."""


# Mesmo mapeamento usado no frontend (_RESPONSAVEL_ALIASES em index.html)
# pra manter o rótulo de "equipe" consistente entre o modal de Comunicados
# e a Priorização IA — o mesmo técnico grafado de formas diferentes na
# Fracttal (apelido x nome completo) cai numa única entrada.
RESPONSAVEL_ALIASES = {
    "deivity jhon cunha saugo": "Deivity Saugo",
    "claudio ferreira": "Cláudio Ferreira",
    "valmir junior": "Valmir Júnior",
}


def _normalizar_responsavel(nome):
    limpo = re.sub(r"\s+", " ", (nome or "")).strip()
    if not limpo:
        return ""
    return RESPONSAVEL_ALIASES.get(limpo.lower(), limpo)


def _equipe_label(item, mapa_cluster):
    """Mesmo cálculo do frontend: a.cluster || responsável normalizado ||
    'Sem cluster'. Usado tanto pra exibir quanto pra filtrar por seleção."""
    cluster = mapa_cluster.get((item.get("usina") or "").strip(), "")
    if cluster:
        return cluster
    resp = _normalizar_responsavel(item.get("responsavel"))
    return resp or "Sem cluster"


@app.route("/sugerir-priorizacao-diaria", methods=["POST", "OPTIONS"])
def sugerir_priorizacao_diaria():
    """
    Usa o Gemini pra analisar as atividades em aberto e sugerir uma ordem
    de prioridade pro dia, considerando impacto na geração, criticidade/
    prazo, dependência entre atividades (ex.: recompor cabo antes de
    amarrar) e agrupamento geográfico pra reduzir deslocamento. Usado pelo
    botão "Sugerir Priorização (IA)" dentro do modal de Comunicados —
    gera uma mensagem separada, não mistura com o comunicado normal por
    usina.

    Aceita opcionalmente {"clusters": ["SP Centro 01", ...]} no corpo do
    POST — mesma seleção de checkboxes já usada nos Comunicados — pra
    restringir a análise só às equipes marcadas, em vez de misturar todas
    as usinas numa lista só (dificultava separar por técnico na hora de
    enviar). Sem esse campo (ou lista vazia), mantém o comportamento
    antigo de considerar todas as atividades — compatibilidade com
    chamadas antigas.
    """
    if request.method == "OPTIONS":
        return ("", 204)
    if not GEMINI_API_KEY:
        return jsonify({"ok": False, "error": "GEMINI_API_KEY não configurada no servidor"}), 500

    dados = request.get_json(force=True, silent=True) or {}
    clusters_selecionados = set(c.strip() for c in dados.get("clusters", []) if c and c.strip())

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    mapa_cluster = _mapa_cluster_usina()
    status_excluidos = {"concluído", "concluido", "cancelado", "convertida em ocorrência", "convertida em ocorrencia"}
    atividades = []
    for row in todos[1:]:
        if len(row) < len(ATIV_HEADERS_JSON):
            row = row + [""] * (len(ATIV_HEADERS_JSON) - len(row))
        item = dict(zip(ATIV_HEADERS_JSON, row[:len(ATIV_HEADERS_JSON)]))
        if not item.get("id"):
            continue
        if (item.get("status") or "").strip().lower() in status_excluidos:
            continue
        if (item.get("statusOS") or "").strip() == "Em Revisão":
            continue  # já foi feita, aguardando confirmação — não é prioridade de execução
        if clusters_selecionados and _equipe_label(item, mapa_cluster) not in clusters_selecionados:
            continue
        atividades.append(item)

    if not atividades:
        motivo = ("Nenhuma atividade em aberto nos clusters selecionados"
                   if clusters_selecionados else
                   "Nenhuma atividade em aberto encontrada pra priorizar")
        return jsonify({"ok": False, "error": motivo}), 400

    total_original = len(atividades)
    truncado = total_original > 70
    if truncado:
        # mesmo teto de segurança usado na reprogramação — prioriza uma
        # pré-seleção grosseira (Alta primeiro, prazo mais próximo) antes
        # de mandar pra IA, que então refina de verdade considerando
        # dependência e geografia.
        def _chave_urgencia(item):
            prioridade_peso = {"alta": 0, "média": 1, "media": 1, "baixa": 2}.get((item.get("prioridade") or "").strip().lower(), 1)
            prazo_str = (item.get("prazo") or "").strip()
            m = re.match(r"(\d{2})/(\d{2})/(\d{4})", prazo_str)
            prazo_ts = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).timestamp() if m else float("inf")
            return (prioridade_peso, prazo_ts)
        atividades = sorted(atividades, key=_chave_urgencia)[:70]

    hoje_str = agora_br().strftime('%d/%m/%Y (%A)')
    prompt = _montar_prompt_priorizacao(atividades, hoje_str)

    try:
        resp = _chamar_gemini_com_retry(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.25,
                    "maxOutputTokens": 24576,
                    "responseMimeType": "application/json",
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=50,
            usar_chave_teste=(request.args.get("diagnostico", "").lower() == "true"),
        )
        data = resp.json()
        candidato = data["candidates"][0]
        finish_reason = candidato.get("finishReason", "")
        if finish_reason == "MAX_TOKENS":
            log.error(f"[sugerir-priorizacao] Resposta cortada por limite de tokens ({len(atividades)} atividades)")
            return jsonify({"ok": False, "error": ("A resposta da IA foi cortada por ser grande demais. "
                            "Tente novamente em instantes.")}), 502
        texto = candidato["content"]["parts"][0]["text"].strip()
        texto_limpo = re.sub(r"^```json\s*|\s*```$", "", texto.strip())
        sugestao = json.loads(texto_limpo)
        return jsonify({"ok": True, "truncado": truncado, "total_atividades": total_original,
                         "consideradas": len(atividades), **sugestao}), 200
    except requests.exceptions.HTTPError as e:
        log.error(f"[sugerir-priorizacao] Erro HTTP do Gemini: {e}")
        return jsonify({"ok": False, "error": f"Erro ao consultar a IA: {e}"}), 502
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        log.error(f"[sugerir-priorizacao] Erro ao processar resposta do Gemini: {e}")
        return jsonify({"ok": False, "error": "A IA retornou uma resposta em formato inesperado. Tente novamente."}), 502
    except Exception as e:
        log.error(f"[sugerir-priorizacao] Erro inesperado: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/sugerir-reprogramacao", methods=["POST", "OPTIONS"])
def sugerir_reprogramacao():
    """
    Analisa as atividades em aberto (vindas da Fracttal ou não) e usa o
    Gemini pra sugerir uma reprogramação otimizada, respeitando que uma
    mesma equipe não pode ser escalada em usinas diferentes no mesmo dia
    (restrição de deslocamento). Usado pela aba "Reprogramações" do
    Painel de Atividades.
    """
    if request.method == "OPTIONS":
        return ("", 204)
    if not GEMINI_API_KEY:
        return jsonify({"ok": False, "error": "GEMINI_API_KEY não configurada no servidor"}), 500

    try:
        body = request.get_json(force=True) or {}
    except Exception:
        body = {}

    ids_filtro = set(str(x) for x in body.get("ids", [])) if body.get("ids") else None

    try:
        ws = get_atividades_sheet()
        todos = ws.get_all_values()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    status_excluidos = {"concluído", "concluido", "cancelado", "convertida em ocorrência", "convertida em ocorrencia"}
    atividades = []
    for row in todos[1:]:
        if len(row) < len(ATIV_HEADERS_JSON):
            row = row + [""] * (len(ATIV_HEADERS_JSON) - len(row))
        item = dict(zip(ATIV_HEADERS_JSON, row[:len(ATIV_HEADERS_JSON)]))
        if not item.get("id"):
            continue
        if (item.get("status") or "").strip().lower() in status_excluidos:
            continue
        # Camada extra de proteção (17/07/2026): "Em Revisão" é o estado
        # de uma OS já concluída em campo, só aguardando confirmação da
        # Fracttal — não faz sentido sugerir uma NOVA data pra ela. O
        # frontend já filtra isso antes de mandar os ids, mas replicar
        # aqui evita que um chamado direto ao endpoint (sem passar pelo
        # filtro do modal) traga OSs que não deveriam ser reprogramadas.
        if (item.get("statusOS") or "").strip().lower() in ("em revisão", "em revisao"):
            continue
        if ids_filtro is not None and item["id"] not in ids_filtro:
            continue
        atividades.append(item)

    if not atividades:
        return jsonify({"ok": False, "error": "Nenhuma atividade em aberto encontrada para reprogramar"}), 400
    total_original = len(atividades)
    truncado = total_original > 60
    if truncado:
        # Limite de segurança pro tempo de resposta da IA + tamanho do
        # prompt/resposta. A causa real do 502 anterior era o modelo
        # gastando tempo em "thinking" estendido (thinkingConfig ausente);
        # com isso desativado, o processamento ficou rápido o bastante
        # (~13s pra 25 atividades) pra suportar um teto bem maior.
        # Prioriza as mais urgentes: Alta prioridade primeiro, depois por
        # prazo mais próximo/vencido.
        def _chave_urgencia(item):
            prioridade_peso = {"alta": 0, "média": 1, "media": 1, "baixa": 2}.get((item.get("prioridade") or "").strip().lower(), 1)
            prazo_str = (item.get("prazo") or "").strip()
            m = re.match(r"(\d{2})/(\d{2})/(\d{4})", prazo_str)
            prazo_ts = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).timestamp() if m else float("inf")
            return (prioridade_peso, prazo_ts)
        atividades = sorted(atividades, key=_chave_urgencia)[:60]

    hoje_str = agora_br().strftime('%d/%m/%Y (%A)')
    proximos_dias_uteis = _proximos_dias_uteis(agora_br())
    prompt = _montar_prompt_reprogramacao(atividades, hoje_str, proximos_dias_uteis)

    try:
        resp = _chamar_gemini_com_retry(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 24576,
                    "responseMimeType": "application/json",
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=45,
            usar_chave_teste=(request.args.get("diagnostico", "").lower() == "true"),
        )
        data = resp.json()
        candidato = data["candidates"][0]
        finish_reason = candidato.get("finishReason", "")
        if finish_reason == "MAX_TOKENS":
            log.error(f"[sugerir-reprogramacao] Resposta cortada por limite de tokens ({len(atividades)} atividades)")
            return jsonify({"ok": False, "error": ("A resposta da IA foi cortada por ser grande demais. "
                            "Tente com menos atividades de uma vez (filtre por cliente/usina).")}), 502
        texto = candidato["content"]["parts"][0]["text"].strip()
        texto_limpo = re.sub(r"^```json\s*|\s*```$", "", texto.strip())
        sugestao = json.loads(texto_limpo)
        _corrigir_fins_de_semana(sugestao)
        _comprimir_agenda_reprogramacao(sugestao, agora_br())
        mapa_cluster = _mapa_cluster_usina()
        for item in sugestao.get("reprogramacoes", []):
            item["cluster"] = mapa_cluster.get((item.get("usina") or "").strip(), "")
        return jsonify({"ok": True, "sugestao": sugestao, "total_atividades": len(atividades),
                         "total_original": total_original, "truncado": truncado})
    except json.JSONDecodeError as e:
        log.error(f"[sugerir-reprogramacao] Resposta não é JSON válido: {e} | texto={texto[:500] if 'texto' in dir() else '?'}")
        return jsonify({"ok": False, "error": "A IA retornou um formato inesperado. Tente novamente."}), 502
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            log.error(f"[sugerir-reprogramacao] Cota da IA esgotada mesmo apos retries: {e}")
            return jsonify({"ok": False, "error": ("A IA está temporariamente sem cota disponível (uso "
                            "excessivo em pouco tempo). Aguarde alguns minutos e tente de novo.")}), 429
        log.error(f"[sugerir-reprogramacao] Erro: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception as e:
        log.error(f"[sugerir-reprogramacao] Erro: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/gerar-texto-os-ia", methods=["POST", "OPTIONS"])
def gerar_texto_os_ia():
    if request.method == "OPTIONS":
        return ("", 204)
    if not GEMINI_API_KEY:
        return jsonify({"ok": False, "error": "GEMINI_API_KEY não configurada no servidor"}), 500
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "Body inválido"}), 400

    prompt = _montar_prompt_os(body)
    diagnostico = request.args.get("diagnostico", "").lower() == "true"
    try:
        resp = _chamar_gemini_com_retry(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 4096,
                    "responseMimeType": "application/json",
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=25,
            usar_chave_teste=diagnostico,
        )
        data = resp.json()
        candidato = data["candidates"][0]
        finish_reason = candidato.get("finishReason", "")
        texto_bruto = candidato["content"]["parts"][0]["text"].strip()
        if not texto_bruto or len(texto_bruto) < 20:
            log.error(f"[gerar-texto-os-ia] Resposta curta/vazia (finishReason={finish_reason}): {texto_bruto!r}")
            raise ValueError(f"Resposta incompleta da IA (finishReason={finish_reason or 'desconhecido'})")

        texto_limpo = re.sub(r"^```json\s*|\s*```$", "", texto_bruto.strip())
        try:
            parsed = json.loads(texto_limpo)
            textos = parsed.get("textos") or []
            textos = [t.strip() for t in textos if t and t.strip()]
        except (json.JSONDecodeError, AttributeError):
            # fallback: se a IA não devolveu o JSON esperado por algum
            # motivo, trata a resposta inteira como um texto único —
            # evita quebrar a funcionalidade por causa de um formato
            # inesperado pontual.
            textos = [texto_bruto]

        if not textos:
            raise ValueError("A IA não retornou nenhum texto de OS")

        # "texto" continua existindo (primeiro item) pra não quebrar quem
        # já usava o formato antigo; "textos" é a lista completa, usada
        # quando a solicitação foi dividida em mais de uma OS.
        resultado = {"ok": True, "texto": textos[0], "textos": textos}
        if diagnostico:
            resultado["chave_teste_configurada"] = bool(GEMINI_API_KEY_TESTE)
        return jsonify(resultado)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            log.error(f"[gerar-texto-os-ia] Cota da IA esgotada mesmo apos retries: {e}")
            return jsonify({"ok": False, "error": ("A IA está temporariamente sem cota disponível (uso "
                            "excessivo em pouco tempo). Aguarde alguns minutos e tente de novo.")}), 429
        log.error(f"[gerar-texto-os-ia] Erro: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception as e:
        log.error(f"[gerar-texto-os-ia] Erro: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/converter-ocorrencia-em-atividade", methods=["POST", "OPTIONS"])
def converter_ocorrencia_em_atividade():
    """
    Converte uma Ocorrência em uma Atividade: cria uma nova linha no Painel de
    Atividades com os dados da ocorrência (incluindo o histórico cronológico
    transferido), e marca a ocorrência original como "Convertida em Atividade".
    """
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "Body inválido"}), 400

    ocorrencia_id = str(body.get("id", "")).strip()
    editor = body.get("editor", "dashboard").strip()
    if not ocorrencia_id:
        return jsonify({"ok": False, "error": "id é obrigatório"}), 400

    try:
        ws_falhas = get_sheet()
        todos_falhas = ws_falhas.get_all_values()
        linha_idx = None
        linha_atual = None
        for i, row in enumerate(todos_falhas[1:], start=2):
            if row and str(row[0]).strip() == ocorrencia_id:
                linha_idx = i
                linha_atual = row
                break
        if not linha_idx:
            return jsonify({"ok": False, "error": "ocorrência não encontrada"}), 404

        # linha_atual: [ID, Cliente, Usina, Equipamento, Falha, Causa, Impactados, Ação,
        #               Status, Ticket, NumeroOS, Historico, ...]
        cliente     = linha_atual[1]  if len(linha_atual) > 1  else ""
        usina       = linha_atual[2]  if len(linha_atual) > 2  else ""
        equipamento = linha_atual[3]  if len(linha_atual) > 3  else ""
        falha       = linha_atual[4]  if len(linha_atual) > 4  else ""
        causa       = linha_atual[5]  if len(linha_atual) > 5  else ""
        acao        = linha_atual[7]  if len(linha_atual) > 7  else ""
        status_ocorr= linha_atual[8]  if len(linha_atual) > 8  else ""
        numero_os   = linha_atual[10] if len(linha_atual) > 10 else ""
        historico_ocorr = linha_atual[11] if len(linha_atual) > 11 else ""

        descricao = falha or "Sem descrição"
        if causa:
            descricao += f" — Causa: {causa}"

        nota_conversao = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - Convertida do Painel de "
                           f"Falhas (Ocorrência #{ocorrencia_id}) por {_editor_legivel(editor)}.")
        historico_atividade = nota_conversao
        if acao:
            historico_atividade += f"\nAção registrada na ocorrência: {acao}"
        if historico_ocorr:
            historico_atividade += "\n" + historico_ocorr

        status_atividade = status_ocorr if status_ocorr and status_ocorr.lower() not in (
            "concluído", "concluido", "convertida em atividade") else "Em Aberto"

        ws_ativ = get_atividades_sheet()
        todos_ativ = ws_ativ.get_all_values()
        novo_id_atividade = _proximo_id_atividade(todos_ativ)
        agora = agora_br().strftime('%d/%m/%Y %H:%M:%S')

        ws_ativ.append_row([novo_id_atividade, cliente, usina, equipamento, descricao, "", "",
                             "Média", status_atividade, agora, "", historico_atividade, editor, numero_os])

        # Marca a ocorrência original como convertida
        ws_falhas.update_cell(linha_idx, 9, "Convertida em Atividade")  # coluna I = Status
        entry = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - Convertida em atividade "
                 f"#{novo_id_atividade} por {_editor_legivel(editor)}.")
        novo_hist_ocorr = f"{historico_ocorr}\n{entry}".strip() if historico_ocorr else entry
        ws_falhas.update_cell(linha_idx, 12, novo_hist_ocorr)  # coluna L = Historico

        log.info(f"[converter-ocorrencia] Ocorrência #{ocorrencia_id} -> Atividade #{novo_id_atividade}")
        return jsonify({"ok": True, "novaAtividadeId": novo_id_atividade})
    except Exception as e:
        log.error(f"[converter-ocorrencia-em-atividade] Erro: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/gerar-relatorio-semanal", methods=["POST", "OPTIONS"])
def gerar_relatorio_semanal_route():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = request.get_json(force=True) or {}
        cliente = str(body.get("cliente", "")).strip()
        data_inicio = datetime.strptime(body["dataInicio"], "%Y-%m-%d")
        data_fim = datetime.strptime(body["dataFim"], "%Y-%m-%d").replace(
            hour=23, minute=59, second=59
        )
        if not cliente:
            return jsonify({"ok": False, "error": "cliente e obrigatorio"}), 400

        ws = get_sheet()
        todos = carregar_planilha(ws)
        grupos_falhas = coletar_ocorrencias_semana(todos, cliente, data_inicio, data_fim)

        try:
            cliente_norm = _norm(cliente)
            todos_chamados = _chamados_fabricante_itens()
            chamados = [
                c for c in todos_chamados
                if cliente_norm in _norm(c.get("Cliente", ""))
                and _norm(c.get("Status", "")) != "concluido"
            ]
        except Exception as e:
            log.error(f"[Relatorio Semanal] Erro ao ler ChamadosFabricante: {e}")
            chamados = []

        try:
            ws_atividades = get_atividades_sheet()
            todos_atividades = carregar_planilha(ws_atividades)
            grupos_atividades = coletar_atividades_semana(todos_atividades, cliente, data_inicio, data_fim)
        except Exception as e:
            log.error(f"[Relatorio Semanal] Erro ao ler Painel de Atividades: {e}")
            grupos_atividades = {}

        grupos = mesclar_grupos(grupos_falhas, grupos_atividades)

        try:
            zeladoria_valores = carregar_planilha(get_zeladoria_sheet())
            zeladoria_usinas = coletar_zeladoria(zeladoria_valores, cliente)
        except Exception as e:
            log.error(f"[Relatorio Semanal] Erro ao ler aba de Zeladoria: {e}")
            zeladoria_usinas = []

        if not grupos and not chamados:
            return jsonify({"ok": False, "error": "Nenhuma ocorrencia encontrada no periodo"}), 404

        semana_num = data_fim.isocalendar()[1]
        data_label = data_fim.strftime('%d/%m/%Y')
        buf = gerar_relatorio_pptx(cliente, semana_num, data_label, grupos,
                                    chamados=chamados, zeladoria_usinas=zeladoria_usinas)

        nome_arquivo = f"Relatorio_{cliente}_{data_inicio.strftime('%Y%m%d')}.pptx".replace(" ", "_")
        return send_file(
            buf,
            as_attachment=True,
            download_name=nome_arquivo,
            mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    except Exception as e:
        log.error(f"[Relatorio Semanal] Erro: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


def gerar_os():
    """
    Gera o texto de solicitação de OS (Título + Comentários) a partir do
    contexto de uma falha, chamando a API da Anthropic do lado do servidor
    (a chave nunca é exposta ao navegador/dashboard).

    Body esperado (JSON):
    {
      "equipamento": "...", "usina": "...", "falha": "...", "causa": "...",
      "impactados": "...", "acao": "...", "historico": "..."
    }

    Retorna: {"ok": true, "texto": "Título:\n...\n\nComentários:\n..."}
    """
    if request.method == "OPTIONS":
        return ("", 204)

    if not ANTHROPIC_API_KEY:
        return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY não configurada no servidor."}), 500

    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "Body inválido — esperado JSON."}), 400

    equipamento = body.get("equipamento", "")
    usina       = body.get("usina", "")
    falha       = body.get("falha", "")
    causa       = body.get("causa", "")
    impactados  = body.get("impactados", "")
    acao        = body.get("acao", "")
    historico   = body.get("historico", "")

    system_prompt = (
        "Você é um engenheiro de O&M (operação e manutenção) de usinas solares, "
        "redigindo solicitações de Ordem de Serviço (OS) técnica para equipe de campo.\n\n"
        "Gere SEMPRE a saída EXATAMENTE neste formato, sem nenhum texto antes ou depois:\n\n"
        "Título:\n"
        "<uma linha, objetiva, descrevendo o diagnóstico/inspeção necessária para o "
        "equipamento e a falha específica>\n\n"
        "Comentários:\n\n"
        "* <item 1 do checklist técnico>\n"
        "* <item 2 do checklist técnico>\n"
        "* <item 3 do checklist técnico>\n"
        "* <item 4 ou mais itens, conforme necessário — geralmente entre 4 e 6 itens>\n\n"
        "Regras:\n"
        "- O checklist deve ser ESPECÍFICO ao tipo de equipamento (inversor, tracker, "
        "motor, TCU, câmera/CFTV, nobreak, transformador, chave seccionadora, "
        "piranômetro, etc.) e à causa da falha informada — nunca genérico.\n"
        "- Use linguagem técnica de campo, direta, em formato de instrução (verbos no "
        "infinitivo: verificar, conferir, inspecionar, avaliar, registrar, medir, testar).\n"
        "- Considere o histórico cronológico para não repetir verificações já feitas, e "
        "para direcionar o checklist ao que ainda falta investigar/resolver.\n"
        "- Considere os equipamentos impactados para garantir que o checklist cubra "
        "todos eles quando relevante.\n"
        "- O título deve mencionar o equipamento/local específico quando disponível.\n"
        "- Nunca inclua explicações, saudações, ou qualquer texto fora do formato "
        "Título/Comentários especificado."
    )

    user_content = (
        f"Equipamento: {equipamento or 'não informado'}\n"
        f"Usina: {usina or 'não informado'}\n"
        f"Falha: {falha or 'não informado'}\n"
        f"Causa: {causa or 'não informado'}\n"
        f"Equipamentos impactados: {impactados or 'não informado'}\n"
        f"Ações já realizadas: {acao or 'nenhuma registrada'}\n"
        f"Histórico cronológico:\n{historico or 'sem histórico registrado'}"
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 600,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_content}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        texto = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                texto = block.get("text", "").strip()
                break
        if not texto:
            return jsonify({"ok": False, "error": "Resposta vazia da IA."}), 502
        return jsonify({"ok": True, "texto": texto}), 200

    except requests.exceptions.RequestException as e:
        log.error(f"[GerarOS] Erro na chamada à API Anthropic: {e}")
        return jsonify({"ok": False, "error": f"Erro ao chamar a API: {str(e)}"}), 502
    except Exception as e:
        log.error(f"[GerarOS] Erro inesperado: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/limpar-duplicatas", methods=["GET", "POST"])
def limpar_duplicatas():
    """
    Limpa duplicatas da planilha.

    Acesse direto pelo navegador (GET):
      https://whatsapp-painel-falhas.onrender.com/limpar-duplicatas?secret=falhas2026

    Para cada grupo de linhas com mesmo fingerprint (usina+equip+falha)
    em aberto, mantém apenas a PRIMEIRA (menor ID) e remove as demais,
    consolidando as ações e o histórico na linha mantida.

    Seguro para executar múltiplas vezes (idempotente).
    Retorna: { ok, removidas, consolidadas, mantidas }
    """
    try:
        # Aceita secret via query string (GET) ou header (POST)
        secret_qs     = request.args.get("secret", "")
        secret_header = request.headers.get("X-Webhook-Secret", "")
        secret        = secret_qs or secret_header
        if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
            return jsonify({"error": "unauthorized — adicione ?secret=VALOR na URL"}), 401

        ws    = get_sheet()
        todos = carregar_planilha(ws)

        # Indexa todas as linhas abertas por fingerprint
        grupos = {}  # fingerprint → [(num_linha, row), ...]
        for i, row in enumerate(todos[1:], start=2):
            if len(row) < 9: continue
            id_val = (row[0] or "").strip()
            if not id_val: continue
            status = row[8].strip().lower()
            if "conclu" in status or "resolv" in status or "fechad" in status:
                continue
            fp = fingerprint_ocorrencia(row[2], row[3], row[4])
            if not fp: continue
            grupos.setdefault(fp, []).append((i, row))

        removidas    = 0
        consolidadas = 0
        mantidas     = 0

        for fp, linhas in grupos.items():
            if len(linhas) <= 1:
                mantidas += 1
                continue

            # Ordena por ID numérico — mantém a primeira
            linhas_ord = sorted(linhas, key=lambda x: int(x[1][0]) if x[1][0].isdigit() else 999999)
            linha_principal_num, linha_principal_row = linhas_ord[0]
            duplicatas = linhas_ord[1:]

            # Consolida ações e histórico das duplicatas na linha principal
            acao_consolidada  = (linha_principal_row[7] if len(linha_principal_row) > 7 else "").strip()
            hist_consolidado  = (linha_principal_row[11] if len(linha_principal_row) > 11 else "").strip()

            for _, dup_row in duplicatas:
                acao_dup = (dup_row[7] if len(dup_row) > 7 else "").strip()
                hist_dup = (dup_row[11] if len(dup_row) > 11 else "").strip()

                # Acrescenta ação da duplicata se tiver informação nova
                if acao_dup and acao_dup not in acao_consolidada:
                    acao_consolidada = (acao_consolidada + "\n" + acao_dup).strip()

                # Acrescenta entradas do histórico que não existem ainda
                for linha_hist in hist_dup.split("\n"):
                    linha_hist = linha_hist.strip()
                    if linha_hist and linha_hist not in hist_consolidado:
                        hist_consolidado = (hist_consolidado + "\n" + linha_hist).strip()

            # Atualiza linha principal com conteúdo consolidado
            ws.update_cell(linha_principal_num, 8,  acao_consolidada)
            ws.update_cell(linha_principal_num, 12, hist_consolidado)
            mantidas += 1
            consolidadas += 1

            # Remove duplicatas (limpa o conteúdo das células — não deleta a linha
            # para não deslocar índices; marca como removida com ID vazio)
            for dup_num, dup_row in duplicatas:
                ws.update(f"A{dup_num}:L{dup_num}", [["" for _ in range(12)]])
                removidas += 1
                log.info(f"🗑️  Removida duplicata linha {dup_num} | ID={dup_row[0]} | {dup_row[2]} / {dup_row[3]}")

        log.info(f"[Limpar] Concluído: {removidas} removidas, {consolidadas} consolidadas, {mantidas} mantidas")
        return jsonify({
            "ok":          True,
            "removidas":   removidas,
            "consolidadas": consolidadas,
            "mantidas":    mantidas,
        }), 200

    except Exception as e:
        log.error(f"[Limpar] Erro: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/test", methods=["POST"])
def test_parse():
    """Testa o parse sem gravar na planilha."""
    payload    = request.get_json(force=True) or {}
    texto      = payload.get("texto", "")
    blocos     = separar_blocos(texto)
    resultados = []
    for b in blocos:
        r = parse_bloco(b)
        if r:
            resultados.append(r)
    return jsonify({"total_blocos": len(blocos), "validos": len(resultados), "resultados": resultados}), 200


try:
    carregar_push_subscriptions()
except Exception as e:
    log.error(f"[Push] Erro na carga inicial de subscriptions: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)

