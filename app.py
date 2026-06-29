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

import os, re, json, logging
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials

# Push notifications (pywebpush)
try:
    from pywebpush import webpush, WebPushException
    PUSH_ENABLED = True
except ImportError:
    PUSH_ENABLED = False
    log_push = logging.getLogger(__name__)
    log_push.warning("pywebpush não instalado — notificações push desabilitadas")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# Permite requisições do GitHub Pages e de qualquer origem
# (o dashboard fica em fred-alexandrino.github.io)
CORS(app, resources={r"/*": {"origins": "*"}})

# ── Configuração ──────────────────────────────────────────────────────────────
SHEET_ID       = os.environ.get("SHEET_ID", "1VLo8__wxSJVWiUIFd_JTcOnadJlUt440i1M1pC0ehTs")
SHEET_NAME     = os.environ.get("SHEET_NAME", "Painel de Falhas - Fred Alexandrino")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
GRUPOS_FILTRO  = os.environ.get("GRUPOS_IDS", "").split(",")

# ── Configuração VAPID para notificações push ────────────────────────────────
VAPID_PUBLIC_KEY  = os.environ.get("VAPID_PUBLIC_KEY", "BPU55JogEEcV6GlCUONmzkVam8Tt9a0DuX3FYfn_ltgKc8p1fahQiE8v5RGECnMkSYEXMyUzOYBtslhUdiOJ6Jk")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CLAIMS      = {"sub": "mailto:fred@gridco.com.br"}

# Subscriptions em memória (persistidas na planilha em produção)
# { endpoint: subscription_json }
_push_subscriptions = {}

# ── URL do servidor WhatsApp (Baileys) — usado pelo endpoint /rondas (Baileys) — usado pelo endpoint /rondas
WPP_SERVER_URL = os.environ.get("WPP_SERVER_URL", "").rstrip("/")

# Nome da aba de log de mensagens
LOG_SHEET_NAME = "Log de Mensagens"

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
        ts = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
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

        desde = datetime.now().timestamp() - (horas * 3600)
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

        desde = datetime.now().timestamp() - (horas * 3600)
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

        limite = datetime.now().timestamp() - (5 * 24 * 3600)
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
        r"^(?:(?:🔴|🟡|🟢|🟠|✅|⏸️|🔧)[\s]*)?(?:DESVIO:[\s]*|UFV[\s]+DESVIO:[\s]*)?(?:UFV[\s]+)?Usina:?[\s]*([^\n\r*·:]{2,60}?)\s*$",
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
    "cos_equipe":     re.compile(r"·\s*(?:Equipe\s+Acionada|T[eé]cnico\s+Acionado):[ \t]*([^\n\r]+)", re.IGNORECASE),
    "cos_supervisor": re.compile(r"·\s*Supervisor(?:\s+Acionado)?:[ \t]*([^\n\r]+)", re.IGNORECASE),
    "cos_inicio":     re.compile(r"·\s*In[ií]ci[oo](?:\s+da\s+[Oo]corrência)?:[ \t]*([^\n\r]+)", re.IGNORECASE),
    "cos_fim":        re.compile(r"·\s*(?:Fim|T[eé]rmino)(?:\s+da\s+[Oo]corrência)?:[ \t]*([^\n\r]*)", re.IGNORECASE),
    "cos_os":         re.compile(r"·\s*N[ºo°]\.?[\s]*(?:da[\s]+)?OS:?[ \t]*([^\n\r]+)", re.IGNORECASE),
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def eh_formato_cos_grid(texto):
    tem_bullet = bool(re.search(r"·\s*(?:Problema|Descrição|Impacto|Ação|Equipe|Supervisor|Início|Fim|Nº)", texto, re.IGNORECASE))
    tem_usina  = bool(re.search(r"Usina:", texto, re.IGNORECASE))
    return tem_bullet and tem_usina

def extrair(texto, padrao):
    m = padrao.search(texto)
    return m.group(1).strip().lstrip("*·").strip() if m else ""

def vazio(v):
    return not v or str(v).strip() in ("", "--", "-", "N/A", "n/a", "não", "nao", "Não")

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

    hoje     = datetime.now().strftime("%d/%m")
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

    hoje       = datetime.now().strftime("%d/%m")
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
    }


# ── Google Sheets ─────────────────────────────────────────────────────────────

def get_sheet():
    gc = get_gc()
    return gc.open_by_key(SHEET_ID).worksheet(SHEET_NAME)

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
            hoje = datetime.now()
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


def atualizar_ocorrencia(ws, num_linha, row, dados, origem="webhook"):
    """
    Atualiza uma ocorrência existente.

    REGRAS DE STATUS:
    - origem="webhook" (tempo real): status atualiza normalmente se mudou.
    - origem="ronda"  (reprocessamento): status NUNCA é alterado pela ronda,
      EXCETO quando detecta chamado fabricante + normal em campo
      → nesse caso define 'Aguardando Fabricante'.

    Histórico e Ação: sempre acrescenta (nunca substitui).
    """
    hoje = datetime.now().strftime("%d/%m")

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

    # Status
    status_atual = (row[8] if len(row) > 8 else "").strip().lower()
    ja_concluido = any(x in status_atual for x in ["conclu", "resolv", "fechad"])

    if not ja_concluido:
        novo_status = dados.get("status", "")
        if origem == "webhook":
            # Tempo real: atualiza status normalmente
            if status_mudou(row, novo_status):
                ws.update_cell(num_linha, 9, novo_status)
                log.info(f"   → Status (webhook): {row[8]} → {novo_status}")
        else:
            # Ronda: só muda se detectar chamado fabricante + campo normal
            texto_analise = " ".join(filter(None, [
                acao_nova,
                dados.get("causa", ""),
                dados.get("falha", ""),
            ]))
            if detectar_aguardando_fabricante(texto_analise):
                if status_atual not in ["aguardando fabricante"]:
                    ws.update_cell(num_linha, 9, "Aguardando Fabricante")
                    log.info(f"   → Status → Aguardando Fabricante (chamado+campo normal): linha {num_linha}")

    # OS — preenche se estava vazio
    os_num = dados.get("os", "")
    if not vazio(os_num):
        os_atual = (row[10] if len(row) > 10 else "").strip()
        if vazio(os_atual):
            ws.update_cell(num_linha, 11, os_num)

    log.info(f"🔄 Atualizado linha {num_linha} | {dados['usina']} / {dados.get('equipamento','')} [{origem}]")



def normalizar_ocorrencia(ws, num_linha, row, dados):
    """Fecha uma ocorrência: status → Concluído + entrada no histórico."""
    hoje = datetime.now().strftime("%d/%m")
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
        "",
        dados["os"],
        dados["historico"],
    ]
    ws.update(f"A{proxima_linha}:L{proxima_linha}", [linha])
    log.info(f"➕ Nova ocorrência ID={novo_id} | {dados['usina']} — {dados['equipamento']} | linha {proxima_linha}")

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
            )
        else:
            enviar_push(
                titulo=f"🔴 Nova falha — {usina_nome}",
                corpo=f"{equip_nome}: {falha_txt[:80] if falha_txt else 'Nova ocorrência registrada'} · {cliente}",
                tipo="nova_ocorrencia",
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
            novo_status = dados.get("status", "")

            mudou_acao   = acao_mudou(row, acao_nova)
            mudou_status = status_mudou(row, novo_status)

            # Rondas: só verifica ação nova — status nunca é alterado por ronda
            # (a lógica de Aguardando Fabricante fica dentro de atualizar_ocorrencia)
            if mudou_acao or detectar_aguardando_fabricante(
                " ".join(filter(None, [dados.get("acao_texto",""), dados.get("causa",""), dados.get("falha","")]))
            ):
                # CASO D — ação mudou ou detectou chamado+campo normal
                atualizar_ocorrencia(ws, num_linha, row, dados, origem="ronda")
                resultado["atualizados"].append(usina)
                todos = carregar_planilha(ws)
            else:
                # CASO C — nenhuma informação nova → ignora
                log.info(f"⏭️  Sem novidade: {usina} / {equip} — ignorado")
                resultado["ignorados"] += 1

    return resultado


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

        tem_usina  = bool(re.search(r"Usina:", texto, re.IGNORECASE))
        tem_emoji  = bool(re.search(r"🔴|🟡|🟢|🟠|✅|⏸️", texto))
        tem_bullet = eh_formato_cos_grid(texto)

        if not tem_usina and not tem_emoji and not tem_bullet:
            return jsonify({"status": "ignored", "reason": "no failure content"}), 200

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

        return jsonify({"status": "ignored", "reason": "no valid content"}), 200

    except Exception as e:
        log.error(f"❌ Erro no webhook: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


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
        _push_subscriptions[endpoint] = sub
        log.info(f"[Push] Nova subscription registrada: {endpoint[:60]}...")
        log.info(f"[Push] Total de dispositivos: {len(_push_subscriptions)}")

        # Envia notificação de boas-vindas
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
            ) if PUSH_ENABLED and VAPID_PRIVATE_KEY else None
        except Exception as e:
            log.warning(f"[Push] Erro na notificação de boas-vindas: {e}")

        return jsonify({"ok": True, "total": len(_push_subscriptions)}), 200

    except Exception as e:
        log.error(f"[Push] Erro ao registrar subscription: {e}", exc_info=True)
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


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":     "ok",
        "timestamp":  datetime.now().isoformat(),
        "wpp_server": WPP_SERVER_URL or "não configurado",
    }), 200


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
