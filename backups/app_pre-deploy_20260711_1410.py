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

import os, re, json, logging, time, random
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials
from relatorio_semanal import (coletar_ocorrencias_semana, coletar_atividades_semana,
                                mesclar_grupos, gerar_relatorio_pptx,
                                coletar_chamados_abertos, listar_usinas_cliente,
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

ATIVIDADES_SHEET_NAME = "Painel de Atividades"
ATIVIDADES_HEADERS = ["ID", "Cliente", "Usina", "Equipamento", "Descricao", "Responsavel", "Prazo",
                       "Prioridade", "Status", "DataCriacao", "DataConclusao", "Historico", "Editor",
                       "NumeroOS"]

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
                      "ultimaVerificacaoOS"]

ATIV_CAMPO_COL = {
    "cliente": 2, "usina": 3, "equipamento": 4, "descricao": 5, "responsavel": 6,
    "prazo": 7, "prioridade": 8, "status": 9, "historico": 12, "numeroOS": 14,
    "statusOS": 15, "observacoesOS": 16, "linkOS": 17, "statusTarefaOS": 18, "etiquetasOS": 19,
    "anotacoesPessoais": 20, "percentualOS": 21, "statusGeralOS": 22, "detalhesEquipamentosOS": 23,
    "ultimaVerificacaoOS": 24,
}

ATIV_TOTAL_COLUNAS = 24

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
                  22: "statusGeralOS", 23: "detalhesEquipamentosOS", 24: "ultimaVerificacaoOS"}
        precisa = False
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
                              ws=None, todos=None):
    """
    Cria uma linha na aba Painel de Atividades. Usada tanto pelo endpoint
    HTTP /nova-atividade quanto pelo sync automático do Fracttal
    (/sync-fracttal, /backfill-fracttal), para evitar duplicar a lógica de
    escrita na planilha.

    Se `ws`/`todos` forem passados (leitura já feita por quem chamou, ex.
    sync em lote), evita reler a planilha inteira a cada chamada.
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
    historico_inicial = f"{agora_br().strftime('%d/%m/%Y %H:%M')} - Atividade criada por {editor}."

    linha = [novo_id, cliente, usina, equipamento, descricao, responsavel, prazo,
             prioridade, status, agora, "", historico_inicial, editor, numeroOS,
             statusOS, observacoesOS, linkOS, statusTarefaOS, etiquetasOS, anotacoesPessoais,
             percentualOS, statusGeralOS, detalhesEquipamentosOS, ""]
    ws.append_row(linha)
    # mantém `todos` coerente para quem estiver criando várias atividades em sequência
    todos.append(linha)
    log.info(f"[atividade] #{novo_id} {cliente}/{usina} — {descricao[:60]} | editor={editor}")
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
        "status": "Em Aberto",
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
            novo_id = _criar_atividade_interna(ws=ws, todos=todos, **mapeado)
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


def _sync_fracttal_core(desde_horas=3, limite_checagem_status=25):
    """
    Núcleo da sincronização: busca OTs recentes na Fracttal, cria atividades
    novas e checa mudança de status em atividades já abertas. Usado tanto
    pelo /sync-fracttal (cron protegido) quanto pelo /atualizar-os-agora
    (botão público do dashboard).
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
            novo_id = _criar_atividade_interna(ws=ws, todos=todos, **mapeado)
            if alerta:
                _aplicar_update_campo_atividade(ws, len(todos), todos[-1], "historico", alerta,
                                                 "fracttal-sync", append=True)
            criadas.append({"numeroOS": mapeado["numeroOS"], "id": novo_id, "itens": len(tasks), "alerta": alerta})
            os_existentes.add(mapeado["numeroOS"])
            try:
                enviar_push(
                    titulo=f"🆕 Nova OS Fracttal — {mapeado['numeroOS']}",
                    corpo=f"{mapeado['usina']} ({mapeado['cliente']}): {mapeado['descricao'][:120]}",
                    tipo="fracttal_nova_os",
                )
            except Exception as e:
                log.error(f"[sync-fracttal] Falha ao enviar push de nova OS {mapeado['numeroOS']}: {e}")
        except Exception as e:
            log.error(f"[sync-fracttal] Erro ao criar atividade para OT {mapeado.get('numeroOS')}: {e}")
            erros.append(mapeado.get("numeroOS", "?"))

    # ── Segunda passada: reconsulta a OS completa (todas as tarefas) em OSs
    # já criadas e ainda em aberto, e atualiza TODOS os campos derivados
    # (statusOS, percentualOS, statusGeralOS, statusTarefaOS,
    # detalhesEquipamentosOS) — não só o status bruto da OT.
    #
    # RODÍZIO JUSTO: em vez de sempre iterar da linha 2 pra baixo (o que
    # faz as OSs mais antigas sempre consumirem a cota e as mais recentes
    # nunca serem alcançadas quando há mais candidatas que o limite),
    # ordena as candidatas por "ultimaVerificacaoOS" (nunca verificadas
    # primeiro, depois as verificadas há mais tempo) e pega só as N
    # primeiras. Assim, ao longo de várias rodadas, TODAS as OSs acabam
    # sendo cobertas, e as recém-criadas são priorizadas na primeira vez.
    mudancas_status = []
    erros_checagem = []
    candidatas = []
    selecionadas = []
    try:
        candidatas = []
        for i, row in enumerate(todos[1:], start=2):
            if len(row) < ATIV_TOTAL_COLUNAS:
                row = row + [""] * (ATIV_TOTAL_COLUNAS - len(row))
            numero_os = row[13].strip()
            status_os_atual = row[14].strip()
            if not numero_os:
                continue  # só monitora quem está de fato vinculado a uma OS da Fracttal
            if status_os_atual in ("Finalizada", "Cancelada"):
                continue  # só para de checar quando a Fracttal encerra de vez
            ultima_verificacao = row[23].strip()  # vazio = nunca verificada, prioridade máxima
            candidatas.append((ultima_verificacao, i, row))

        random.shuffle(candidatas)  # desempate aleatório entre "nunca verificadas" (senão sempre as mesmas primeiras da planilha ganham a cota)
        candidatas.sort(key=lambda t: t[0])  # "" (nunca) vem antes de qualquer timestamp
        selecionadas = candidatas[:limite_checagem_status]

        for _, i, row in selecionadas:
            editor_linha = row[12].strip()
            numero_os = row[13].strip()
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
                ws.update_cell(i, ATIV_CAMPO_COL["ultimaVerificacaoOS"], agora_iso)
                if not tasks:
                    time.sleep(0.6)
                    continue

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

                if mudou:
                    entry = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - Status na OS (Fracttal) atualizado: "
                             f"\"{status_os_atual or '—'}\" → \"{status_novo or status_os_atual or '—'}\", "
                             f"{percentual_atual or '0'}% → {percentual_novo}% ({status_geral_novo}).")
                    hist_atual = row[ATIV_COL_HISTORICO - 1] if len(row) >= ATIV_COL_HISTORICO else ""
                    ws.update_cell(i, ATIV_COL_HISTORICO, f"{hist_atual}\n{entry}".strip() if hist_atual else entry)
                    mudancas_status.append({"numeroOS": numero_os, "statusOS": status_novo,
                                             "percentualOS": percentual_novo, "statusGeralOS": status_geral_novo})

                    # só considera a OS realmente encerrada quando o ESTADO
                    # (card do Kanban na Fracttal) chega em "Finalizada".
                    # "Em Revisão" é só a etapa de verificação — a OS ainda
                    # está ativa/em aberto do ponto de vista do dashboard,
                    # só muda de card dentro da Fracttal. Só quando ela sai
                    # de vez pra "OSs Concluídas" lá (Finalizada) é que
                    # consideramos concluída aqui.
                    status_efetivo = status_novo or status_os_atual
                    concluida_de_fato = status_efetivo == "Finalizada"
                    if concluida_de_fato and status_interno_atual not in ("Concluído", "Cancelado"):
                        ws.update_cell(i, ATIV_CAMPO_COL["status"], "Concluído")
                    # regressão: estava Finalizada e voltou pra Em Processo/Em
                    # Revisão (reprovada ou reaberta na Fracttal) — reabre.
                    elif not concluida_de_fato and status_interno_atual == "Concluído":
                        ws.update_cell(i, ATIV_CAMPO_COL["status"], "Em Aberto")
                        reabertura = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - ⚠️ OS reaberta automaticamente: "
                                      f"estava marcada como concluída, mas a Fracttal mostra status \"{status_efetivo or '—'}\" "
                                      f"(voltou pra Em Processo/Em Revisão — provavelmente reprovada ou reaberta).")
                        ws.update_cell(i, ATIV_COL_HISTORICO, f"{hist_atual}\n{entry}\n{reabertura}".strip())

                    try:
                        enviar_push(
                            titulo=f"🔄 OS {numero_os} atualizada",
                            corpo=f"{status_geral_novo} — {percentual_novo}% concluído",
                            tipo="fracttal_status",
                        )
                    except Exception as e:
                        log.error(f"[sync-fracttal] Falha ao enviar push de mudança de status {numero_os}: {e}")
                time.sleep(0.6)
            except Exception as e:
                log.error(f"[sync-fracttal] Erro ao checar status da OS {numero_os}: {e}")
                erros_checagem.append({"numeroOS": numero_os, "erro": str(e)})
    except Exception as e:
        log.error(f"[sync-fracttal] Erro na checagem de mudanças de status: {e}")
        erros_checagem.append({"erro_geral": str(e)})

    log.info(f"[sync-fracttal] criadas={len(criadas)} revisao_manual={len(revisao_manual)} erros={len(erros)} "
             f"mudancas_status={len(mudancas_status)} checadas={len(selecionadas)} erros_checagem={len(erros_checagem)}")
    return {"ok": True, "criadas": criadas, "revisao_manual": revisao_manual, "erros": erros,
            "mudancas_status": mudancas_status, "checadas_nesta_rodada": len(selecionadas),
            "candidatas_totais": len(candidatas), "erros_checagem": erros_checagem}, 200



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
                ws.update_cell(i, ATIV_CAMPO_COL["status"], "Em Aberto")
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

def _mapa_grupo_usina():
    ws_cfg = _get_config_sheet()
    valores = ws_cfg.get_all_values()
    mapa = {}
    for row in valores[1:]:
        if row and row[0].strip().startswith("grupo_usina:"):
            usina = row[0].strip()[len("grupo_usina:"):].strip()
            grupo_id = row[1].strip() if len(row) > 1 else ""
            if usina and grupo_id:
                mapa[usina] = grupo_id
    return mapa


def _mapa_cluster_usina():
    """Mapeia usina -> código de cluster/equipe regional (ex.: 'SP Centro
    01'), configurado na aba _Sistema como 'cluster_usina:<Usina>'."""
    ws_cfg = _get_config_sheet()
    valores = ws_cfg.get_all_values()
    mapa = {}
    for row in valores[1:]:
        if row and row[0].strip().startswith("cluster_usina:"):
            usina = row[0].strip()[len("cluster_usina:"):].strip()
            cluster = row[1].strip() if len(row) > 1 else ""
            if usina and cluster:
                mapa[usina] = cluster
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
    """Lógica compartilhada: só dispara o envio de verdade se for dia útil,
    estiver na janela 07:00-07:09 (BRT) e ainda não tiver sido enviado
    hoje. Retorna um dict com o resultado (nunca levanta exceção pro
    chamador, pra nunca quebrar quem estiver piggybackando nela)."""
    try:
        agora = agora_br()
        hoje_str = agora.strftime("%Y-%m-%d")

        if agora.weekday() >= 5:  # sábado=5, domingo=6
            return {"disparado": False, "motivo": "fim de semana"}
        if not (agora.hour == 7 and agora.minute < 10):
            return {"disparado": False, "motivo": f"fora da janela (agora {agora.strftime('%H:%M')})"}

        ja_enviado = _ler_trava("comunicados_enviados_em")
        if ja_enviado == hoje_str:
            return {"disparado": False, "motivo": "já enviado hoje"}

        _gravar_trava("comunicados_enviados_em", hoje_str)
        resultado = _enviar_comunicados_diarios_core()
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
        if status_os == "Em Revisão":
            # já foi enviada pra verificação na Fracttal — o técnico já fez
            # a parte dele, não faz sentido cobrar de novo no comunicado.
            # Continua rastreada/ativa no sistema até virar "Finalizada".
            continue
        usina = row[2].strip()
        if not usina:
            continue
        d = {
            "usina": usina,
            "equipamento": row[3].strip(),
            "descricao": row[4].strip(),
            "prazo": row[6].strip(),
            "numeroOS": row[13].strip(),
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
    Sincroniza OTs novas da Fracttal para o Painel de Atividades.
    Disparado periodicamente por cron via GitHub Actions
    (.github/workflows/sync-fracttal.yml). Protegido pelo mesmo
    WEBHOOK_SECRET usado nos demais endpoints sensíveis.
    """
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Webhook-Secret", "") or request.args.get("secret", "")
        if secret != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    body, status_code = _sync_fracttal_core(desde_horas=3)
    # piggyback: como este endpoint já é chamado de forma confiável a cada
    # 5 min via UptimeRobot, aproveita pra checar/disparar os comunicados
    # das 7h também — o cron dedicado do GitHub Actions sozinho atrasa de
    # forma imprevisível e já deixou de disparar no horário certo.
    body["comunicados_check"] = _verificar_e_disparar_comunicados_se_necessario()
    return jsonify(body), status_code


@app.route("/atualizar-os-agora", methods=["POST", "OPTIONS"])
def atualizar_os_agora():
    """
    Endpoint PÚBLICO (sem secret) pro botão "Atualizar OS" do dashboard —
    dispara uma busca forçada na Fracttal por OTs criadas/atualizadas nas
    últimas 6h (janela maior que o cron automático, pra não perder nada
    entre cliques) e cria o que for novo. Não expõe nada sensível, só
    aciona a mesma lógica de sincronização sob demanda.
    """
    if request.method == "OPTIONS":
        return ("", 204)
    body, status_code = _sync_fracttal_core(desde_horas=6, limite_checagem_status=20)
    return jsonify(body), status_code


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
                label = ATIV_CAMPO_LABEL.get(field, field)
                entry = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - {label} alterado "
                         f"de \"{valor_antigo or '—'}\" para \"{value}\" por {editor}.")
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
        label = ATIV_CAMPO_LABEL.get(field, field)
        entry = (f"{agora_br().strftime('%d/%m/%Y %H:%M')} - {label} alterado "
                 f"de \"{valor_antigo or '—'}\" para \"{value}\" por {editor}.")
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
        entry = f"{agora_br().strftime('%d/%m/%Y %H:%M')} - {editor}: {dados['descricao']}"
        _aplicar_update_campo_atividade(ws, linha_idx, linha_atual, "historico", entry, editor, append=True)
        todos = ws.get_all_values(); linha_atual = todos[linha_idx - 1]

    if dados["status"]:
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
                           f"Atividades (Atividade #{atividade_id}) por {editor}.")
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
                 f"#{novo_id_ocorrencia} por {editor}.")
        novo_hist_ativ = f"{historico_ativ}\n{entry}".strip() if historico_ativ else entry
        ws_ativ.update_cell(linha_idx, 12, novo_hist_ativ)  # coluna L = Historico

        log.info(f"[converter-atividade] Atividade #{atividade_id} -> Ocorrência #{novo_id_ocorrencia}")
        return jsonify({"ok": True, "novaOcorrenciaId": novo_id_ocorrencia})
    except Exception as e:
        log.error(f"[converter-atividade-em-ocorrencia] Erro: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Geração de texto de OS via Gemini (gratuito), com fallback local ───────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def _montar_prompt_os(d):
    return f"""Aja como um Engenheiro e Especialista em Operação e Manutenção (O&M), com foco em Usinas Solares Fotovoltaicas, sistemas elétricos, mecânicos e atividades de facilities (limpeza, conservação, manutenções civis).

Sua tarefa é redigir Ordens de Serviço (OS) baseadas na solicitação abaixo. Transforme a solicitação em um texto objetivo, profissional, técnico e estritamente padronizado.

REGRAS DE FORMATAÇÃO (OBRIGATÓRIO):
- Esqueça introduções, conclusões, saudações, tabelas, ou seções como "Objetivo", "Descrição", "Responsáveis" ou "Evidências".
- O texto deve conter APENAS o "Título" e os "Comentários". Siga este modelo exato:

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

EXEMPLOS DO PADRÃO ESPERADO:

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

Exemplo 4 (Atividade de Ajuste)
Título: Reposicionamento de câmeras de CFTV
Comentários:

1. Verificar a posição atual de cada câmera e o campo de visão afetado.
2. Realizar o reposicionamento físico conforme a necessidade operacional, ajustando inclinação e direcionamento.
3. Validar a visualização da imagem no sistema central de monitoramento para confirmar a cobertura desejada.
4. Registrar a atividade e as evidências de antes e depois da intervenção.

Aplique exclusivamente este padrão. Não invente números de ticket, causas, nomes ou dados que não foram informados abaixo. Gere apenas o texto da OS — sem introdução, comentário ou explicação antes ou depois.

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


def _chamar_gemini_com_retry(payload, timeout=45, tentativas=3):
    """Chama a API do Gemini com retry automático em caso de 429 (limite
    de taxa) — espera crescente entre tentativas (2s, 5s, 10s). Um pico
    passageiro de uso (ex.: várias chamadas em sequência rápida) costuma
    se resolver sozinho em poucos segundos; isso evita expor esse erro
    direto pro usuário na maioria dos casos. Levanta a exceção normalmente
    se todas as tentativas falharem (ex.: cota diária realmente esgotada,
    que não se resolve só esperando)."""
    esperas = [2, 5, 10]
    ultima_excecao = None
    for tentativa in range(tentativas):
        try:
            resp = requests.post(f"{GEMINI_URL}?key={GEMINI_API_KEY}", json=payload, timeout=timeout)
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
    try:
        resp = _chamar_gemini_com_retry(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 2048,
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=20,
        )
        data = resp.json()
        candidato = data["candidates"][0]
        finish_reason = candidato.get("finishReason", "")
        texto = candidato["content"]["parts"][0]["text"].strip()
        if not texto or len(texto) < 40:
            log.error(f"[gerar-texto-os-ia] Resposta curta/vazia (finishReason={finish_reason}): {texto!r}")
            raise ValueError(f"Resposta incompleta da IA (finishReason={finish_reason or 'desconhecido'})")
        return jsonify({"ok": True, "texto": texto})
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
                           f"Falhas (Ocorrência #{ocorrencia_id}) por {editor}.")
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
                 f"#{novo_id_atividade} por {editor}.")
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
        chamados = coletar_chamados_abertos(todos, cliente)

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
