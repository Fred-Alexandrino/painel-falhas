"""Boletins de Medição (BM) dentro do Omni — 24/11/2026.

Substitui a plataforma HTML que gravava na pasta Boletins_Medicao do OneDrive.
Usuário único: Fred (perfil manager). Os dados ficam num SQLite próprio
(boletins_medicao.db, ao lado do app.py) — não passam pelo Google Sheets.

Modelo:
- clientes      → cadastro do cliente (usinas, regras de período/NF/envio,
                  configuração do e-mail). Documento JSON por cliente.
- lancamentos   → custos adicionais / descontos, SEMPRE presos a uma
                  competência (AAAA-MM). Um custo só entra no BM do mês dele,
                  o que acaba com o risco de cobrar o mesmo custo duas vezes.
- emissoes      → cada BM gerado vira um registro com cópia congelada dos
                  dados (snapshot), hash, revisão (R00, R01...) e status:
                  gerado → enviado → aprovado → nf (ou cancelado).
- anexos        → NF/recibo/comprovante de um lançamento, e os PDFs das
                  emissões, gravados em disco (bm_arquivos/).

Acesso: header X-BM-Chave (hash guardado no banco, definido via
POST /bm/chave com X-Deploy-Secret). Perfis de supervisora/campo
(X-Escopo / X-Visao) são sempre recusados.

Registro no app.py (2 linhas no fim do arquivo, não remover):
    from boletins_medicao import registrar as _bm_registrar
    _bm_registrar(app, log, _bm_hook_compromisso)
"""
import os
import json
import sqlite3
import hashlib
import hmac
import threading
import uuid
import re
from datetime import datetime, timezone, timedelta

from flask import request, jsonify, send_file

_BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_BASE, "boletins_medicao.db")
ARQ_DIR = os.path.join(_BASE, "bm_arquivos")
_LOCK = threading.Lock()
_TZ = timezone(timedelta(hours=-3))
STATUS_VALIDOS = ("gerado", "enviado", "aprovado", "nf", "cancelado")
MAX_ARQ = 20 * 1024 * 1024


def _agora():
    return datetime.now(_TZ).strftime("%Y-%m-%dT%H:%M:%S")


def _con():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _init_db():
    os.makedirs(ARQ_DIR, exist_ok=True)
    with _LOCK, _con() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS clientes (id TEXT PRIMARY KEY, doc TEXT NOT NULL,
            ativo INTEGER NOT NULL DEFAULT 1, atualizado TEXT);
        CREATE TABLE IF NOT EXISTS lancamentos (id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL,
            competencia TEXT NOT NULL, doc TEXT NOT NULL, excluido INTEGER NOT NULL DEFAULT 0,
            criado TEXT, atualizado TEXT);
        CREATE INDEX IF NOT EXISTS ix_lanc_cli ON lancamentos(cliente_id, competencia);
        CREATE TABLE IF NOT EXISTS anexos (id TEXT PRIMARY KEY, dono TEXT NOT NULL, tipo TEXT NOT NULL,
            nome TEXT, mime TEXT, tamanho INTEGER, caminho TEXT, rotulo TEXT, criado TEXT);
        CREATE INDEX IF NOT EXISTS ix_anexo_dono ON anexos(dono);
        CREATE TABLE IF NOT EXISTS emissoes (id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL,
            competencia TEXT NOT NULL, revisao INTEGER NOT NULL DEFAULT 0, usinas TEXT,
            status TEXT NOT NULL, snapshot TEXT, total REAL, hash TEXT, titulo TEXT,
            criado TEXT, enviado_em TEXT, aprovado_em TEXT, nf_em TEXT, info TEXT,
            historico TEXT, origem TEXT DEFAULT 'omni');
        CREATE INDEX IF NOT EXISTS ix_em_cli ON emissoes(cliente_id, competencia);
        CREATE TABLE IF NOT EXISTS log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, acao TEXT, detalhe TEXT);
        """)


def _log(c, acao, detalhe):
    c.execute("INSERT INTO log(ts, acao, detalhe) VALUES (?,?,?)",
              (_agora(), acao, json.dumps(detalhe, ensure_ascii=False)[:4000]))
    _backup_diario()


def _backup_diario():
    """Cópia do banco 1x por dia (na 1ª gravação do dia), guarda 30 dias."""
    try:
        pasta = os.path.join(ARQ_DIR, "backups")
        os.makedirs(pasta, exist_ok=True)
        destino = os.path.join(pasta, "bm_" + datetime.now(_TZ).strftime("%Y%m%d") + ".db")
        if os.path.exists(destino):
            return
        src = sqlite3.connect(DB_PATH, timeout=30)
        dst = sqlite3.connect(destino)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        antigos = sorted(f for f in os.listdir(pasta) if f.startswith("bm_") and f.endswith(".db"))
        for f in antigos[:-30]:
            os.remove(os.path.join(pasta, f))
    except Exception:
        pass


def _hash(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_COMP_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _emissao_row(r, com_snapshot=False):
    d = {
        "id": r["id"], "clienteId": r["cliente_id"], "competencia": r["competencia"],
        "revisao": r["revisao"], "usinas": json.loads(r["usinas"] or "[]"),
        "status": r["status"], "total": r["total"], "hash": r["hash"], "titulo": r["titulo"],
        "criado": r["criado"], "enviadoEm": r["enviado_em"], "aprovadoEm": r["aprovado_em"],
        "nfEm": r["nf_em"], "info": json.loads(r["info"] or "{}"),
        "historico": json.loads(r["historico"] or "[]"), "origem": r["origem"],
    }
    if com_snapshot:
        d["snapshot"] = json.loads(r["snapshot"]) if r["snapshot"] else None
    return d


def registrar(app, log, hook_compromisso=None):
    """Registra as rotas /bm/* no app Flask."""
    _init_db()

    def _autorizado():
        if (request.headers.get("X-Escopo") or "").strip() or (request.headers.get("X-Visao") or "").strip():
            return False, "Perfil sem acesso aos Boletins de Medição."
        chave = (request.headers.get("X-BM-Chave") or "").strip()
        with _con() as c:
            r = c.execute("SELECT v FROM kv WHERE k='chave_hash'").fetchone()
        if not r:
            return False, "Chave do BM ainda não definida no servidor."
        if not chave or not hmac.compare_digest(_hash(chave), r["v"]):
            return False, "Chave do BM inválida."
        return True, ""

    def _guard():
        if request.method == "OPTIONS":
            return ("", 204)
        ok, msg = _autorizado()
        if not ok:
            return jsonify({"ok": False, "error": msg, "precisaChave": True}), 403
        return None

    def _deploy_ok():
        seg = os.environ.get("DEPLOY_SECRET", "")
        return bool(seg) and hmac.compare_digest(request.headers.get("X-Deploy-Secret", ""), seg)

    # ── chave ────────────────────────────────────────────────────────
    @app.route("/bm/chave", methods=["POST", "OPTIONS"])
    def bm_definir_chave():
        if request.method == "OPTIONS":
            return ("", 204)
        if not _deploy_ok():
            return jsonify({"ok": False, "error": "não autorizado"}), 403
        chave = str((request.get_json(force=True, silent=True) or {}).get("chave") or "").strip()
        if len(chave) < 8:
            return jsonify({"ok": False, "error": "chave muito curta (mín. 8)"}), 400
        with _LOCK, _con() as c:
            c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES ('chave_hash', ?)", (_hash(chave),))
            _log(c, "chave", {"definida": True})
        return jsonify({"ok": True})

    @app.route("/bm/ping", methods=["GET", "OPTIONS"])
    def bm_ping():
        g = _guard()
        if g:
            return g
        return jsonify({"ok": True})

    # ── leitura completa ─────────────────────────────────────────────
    @app.route("/bm/estado", methods=["GET", "OPTIONS"])
    def bm_estado():
        g = _guard()
        if g:
            return g
        with _con() as c:
            clientes = []
            for r in c.execute("SELECT doc, ativo, atualizado FROM clientes ORDER BY atualizado"):
                d = json.loads(r["doc"])
                d["_ativo"] = bool(r["ativo"])
                d["_atualizado"] = r["atualizado"]
                clientes.append(d)
            lancs = []
            for r in c.execute("SELECT * FROM lancamentos WHERE excluido=0"):
                d = json.loads(r["doc"])
                d.update({"id": r["id"], "clienteId": r["cliente_id"], "competencia": r["competencia"],
                          "_criado": r["criado"], "_atualizado": r["atualizado"]})
                lancs.append(d)
            anexos = [dict(r) for r in c.execute(
                "SELECT id, dono, tipo, nome, mime, tamanho, rotulo, criado FROM anexos ORDER BY criado")]
            emissoes = [_emissao_row(r) for r in c.execute(
                "SELECT * FROM emissoes ORDER BY competencia DESC, revisao DESC, criado DESC")]
        return jsonify({"ok": True, "clientes": clientes, "lancamentos": lancs,
                        "anexos": anexos, "emissoes": emissoes, "agora": _agora()})

    # ── clientes ─────────────────────────────────────────────────────
    @app.route("/bm/cliente", methods=["POST", "OPTIONS"])
    def bm_salvar_cliente():
        g = _guard()
        if g:
            return g
        doc = (request.get_json(force=True, silent=True) or {}).get("cliente") or {}
        if not isinstance(doc, dict) or not str(doc.get("nome") or "").strip():
            return jsonify({"ok": False, "error": "cliente sem nome"}), 400
        doc = {k: v for k, v in doc.items() if not k.startswith("_")}
        doc["id"] = str(doc.get("id") or uuid.uuid4().hex[:12])
        with _LOCK, _con() as c:
            c.execute("INSERT INTO clientes(id, doc, ativo, atualizado) VALUES (?,?,1,?) "
                      "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc, ativo=1, atualizado=excluded.atualizado",
                      (doc["id"], json.dumps(doc, ensure_ascii=False), _agora()))
            _log(c, "cliente_salvo", {"id": doc["id"], "nome": doc.get("nome")})
        return jsonify({"ok": True, "cliente": doc})

    @app.route("/bm/cliente/arquivar", methods=["POST", "OPTIONS"])
    def bm_arquivar_cliente():
        g = _guard()
        if g:
            return g
        body = request.get_json(force=True, silent=True) or {}
        cid = str(body.get("id") or "")
        ativo = 1 if body.get("ativo") else 0
        with _LOCK, _con() as c:
            n = c.execute("UPDATE clientes SET ativo=?, atualizado=? WHERE id=?", (ativo, _agora(), cid)).rowcount
            _log(c, "cliente_ativo", {"id": cid, "ativo": ativo})
        return jsonify({"ok": bool(n)})

    # ── lançamentos (custos / descontos) ─────────────────────────────
    @app.route("/bm/lancamento", methods=["POST", "OPTIONS"])
    def bm_salvar_lancamento():
        g = _guard()
        if g:
            return g
        doc = (request.get_json(force=True, silent=True) or {}).get("lancamento") or {}
        cid = str(doc.get("clienteId") or "")
        comp = str(doc.get("competencia") or "")
        if not cid or not _COMP_RE.match(comp):
            return jsonify({"ok": False, "error": "clienteId e competencia (AAAA-MM) são obrigatórios"}), 400
        lid = str(doc.get("id") or uuid.uuid4().hex[:14])
        corpo = {k: v for k, v in doc.items() if k not in ("id", "clienteId", "competencia") and not k.startswith("_")}
        agora = _agora()
        with _LOCK, _con() as c:
            if not c.execute("SELECT 1 FROM clientes WHERE id=?", (cid,)).fetchone():
                return jsonify({"ok": False, "error": "cliente não encontrado"}), 404
            antigo = c.execute("SELECT competencia FROM lancamentos WHERE id=?", (lid,)).fetchone()
            c.execute("INSERT INTO lancamentos(id, cliente_id, competencia, doc, excluido, criado, atualizado) "
                      "VALUES (?,?,?,?,0,?,?) ON CONFLICT(id) DO UPDATE SET cliente_id=excluded.cliente_id, "
                      "competencia=excluded.competencia, doc=excluded.doc, excluido=0, atualizado=excluded.atualizado",
                      (lid, cid, comp, json.dumps(corpo, ensure_ascii=False), agora, agora))
            # aviso: competência que já tem BM enviado para essa usina
            aviso = ""
            for r in c.execute("SELECT usinas, revisao, status FROM emissoes WHERE cliente_id=? AND competencia=? "
                               "AND status IN ('enviado','aprovado','nf')", (cid, comp)):
                us = json.loads(r["usinas"] or "[]")
                if not us or corpo.get("ufv") in us:
                    aviso = (f"O BM desta competência já foi enviado (R{r['revisao']:02d}, {r['status']}). "
                             "Esta alteração só vai para o cliente numa nova revisão.")
                    break
            _log(c, "lancamento_salvo", {"id": lid, "cliente": cid, "competencia": comp,
                                         "antes": antigo["competencia"] if antigo else None})
        return jsonify({"ok": True, "id": lid, "aviso": aviso})

    @app.route("/bm/lancamento/excluir", methods=["POST", "OPTIONS"])
    def bm_excluir_lancamento():
        g = _guard()
        if g:
            return g
        lid = str((request.get_json(force=True, silent=True) or {}).get("id") or "")
        with _LOCK, _con() as c:
            n = c.execute("UPDATE lancamentos SET excluido=1, atualizado=? WHERE id=?", (_agora(), lid)).rowcount
            _log(c, "lancamento_excluido", {"id": lid})
        return jsonify({"ok": bool(n)})

    # ── anexos ───────────────────────────────────────────────────────
    def _gravar_arquivo(f, dono, tipo, rotulo=""):
        dados = f.read()
        if not dados:
            raise ValueError("arquivo vazio")
        if len(dados) > MAX_ARQ:
            raise ValueError("arquivo maior que 20 MB")
        aid = uuid.uuid4().hex[:16]
        nome = os.path.basename(f.filename or "arquivo")[:180]
        ext = os.path.splitext(nome)[1][:10]
        caminho = os.path.join(ARQ_DIR, aid + ext)
        with open(caminho, "wb") as fh:
            fh.write(dados)
        return {"id": aid, "dono": dono, "tipo": tipo, "nome": nome,
                "mime": f.mimetype or "application/octet-stream", "tamanho": len(dados),
                "caminho": caminho, "rotulo": rotulo, "criado": _agora()}

    def _inserir_anexo(c, a):
        c.execute("INSERT INTO anexos(id, dono, tipo, nome, mime, tamanho, caminho, rotulo, criado) "
                  "VALUES (:id,:dono,:tipo,:nome,:mime,:tamanho,:caminho,:rotulo,:criado)", a)

    @app.route("/bm/anexo", methods=["POST", "OPTIONS"])
    def bm_anexo_upload():
        g = _guard()
        if g:
            return g
        dono = str(request.form.get("lancamentoId") or "")
        f = request.files.get("arquivo")
        if not dono or not f:
            return jsonify({"ok": False, "error": "lancamentoId e arquivo são obrigatórios"}), 400
        try:
            a = _gravar_arquivo(f, dono, "lancamento", request.form.get("rotulo") or "")
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        with _LOCK, _con() as c:
            _inserir_anexo(c, a)
            _log(c, "anexo", {"id": a["id"], "dono": dono, "nome": a["nome"]})
        a.pop("caminho")
        return jsonify({"ok": True, "anexo": a})

    @app.route("/bm/anexo/excluir", methods=["POST", "OPTIONS"])
    def bm_anexo_excluir():
        g = _guard()
        if g:
            return g
        aid = str((request.get_json(force=True, silent=True) or {}).get("id") or "")
        with _LOCK, _con() as c:
            r = c.execute("SELECT tipo FROM anexos WHERE id=?", (aid,)).fetchone()
            if not r:
                return jsonify({"ok": False, "error": "anexo não encontrado"}), 404
            if r["tipo"] == "pdf_emissao":
                return jsonify({"ok": False, "error": "PDF de BM emitido não pode ser apagado"}), 400
            c.execute("UPDATE anexos SET dono='__excluido__:' || dono WHERE id=?", (aid,))
            _log(c, "anexo_excluido", {"id": aid})
        return jsonify({"ok": True})

    @app.route("/bm/arquivo/<aid>", methods=["GET", "OPTIONS"])
    def bm_arquivo(aid):
        g = _guard()
        if g:
            return g
        with _con() as c:
            r = c.execute("SELECT * FROM anexos WHERE id=?", (aid,)).fetchone()
        if not r or not os.path.exists(r["caminho"]):
            return jsonify({"ok": False, "error": "arquivo não encontrado"}), 404
        return send_file(r["caminho"], mimetype=r["mime"], as_attachment=True, download_name=r["nome"])

    # ── emissões ─────────────────────────────────────────────────────
    @app.route("/bm/emissao/gerar", methods=["POST", "OPTIONS"])
    def bm_emissao_gerar():
        """Congela um BM. Se já existe uma emissão 'gerado' (não enviada) do
        mesmo cliente/competência com as mesmas usinas, ela é substituída
        (mesma revisão). Se a anterior já foi enviada, abre revisão nova."""
        g = _guard()
        if g:
            return g
        b = request.get_json(force=True, silent=True) or {}
        cid = str(b.get("clienteId") or "")
        comp = str(b.get("competencia") or "")
        usinas = sorted(str(u) for u in (b.get("usinas") or []))
        snap = b.get("snapshot")
        if not cid or not _COMP_RE.match(comp) or not usinas or not isinstance(snap, dict):
            return jsonify({"ok": False, "error": "clienteId, competencia, usinas e snapshot são obrigatórios"}), 400
        snap_txt = _canon(snap)
        h = _hash(snap_txt)
        agora = _agora()
        with _LOCK, _con() as c:
            rows = c.execute("SELECT * FROM emissoes WHERE cliente_id=? AND competencia=? ORDER BY revisao DESC",
                             (cid, comp)).fetchall()
            mesma = [r for r in rows if sorted(json.loads(r["usinas"] or "[]")) == usinas]
            if mesma and mesma[0]["status"] == "gerado":
                r = mesma[0]
                if r["hash"] == h:
                    return jsonify({"ok": True, "emissao": _emissao_row(r), "reaproveitada": True})
                hist = json.loads(r["historico"] or "[]") + [{"ts": agora, "acao": "regerado", "hashAnterior": r["hash"]}]
                c.execute("UPDATE emissoes SET snapshot=?, total=?, hash=?, titulo=?, criado=?, historico=? WHERE id=?",
                          (snap_txt, b.get("total"), h, b.get("titulo"), agora, json.dumps(hist, ensure_ascii=False), r["id"]))
                c.execute("UPDATE anexos SET dono='__substituido__:' || dono WHERE dono=? AND tipo='pdf_emissao'", (r["id"],))
                eid = r["id"]
            else:
                # usinas desta emissão que já constam em BM enviado da mesma competência → revisão
                ja_enviadas = [r for r in rows if r["status"] in ("enviado", "aprovado", "nf")
                               and set(json.loads(r["usinas"] or "[]")) & set(usinas)]
                revisao = (max([r["revisao"] for r in ja_enviadas]) + 1) if ja_enviadas else 0
                eid = uuid.uuid4().hex[:14]
                hist = [{"ts": agora, "acao": "gerado", "revisao": revisao}]
                c.execute("INSERT INTO emissoes(id, cliente_id, competencia, revisao, usinas, status, snapshot, total, "
                          "hash, titulo, criado, info, historico, origem) VALUES (?,?,?,?,?,'gerado',?,?,?,?,?,'{}',?,'omni')",
                          (eid, cid, comp, revisao, json.dumps(usinas, ensure_ascii=False), snap_txt, b.get("total"),
                           h, b.get("titulo"), agora, json.dumps(hist, ensure_ascii=False)))
            _log(c, "emissao_gerada", {"id": eid, "cliente": cid, "competencia": comp, "usinas": usinas})
            r = c.execute("SELECT * FROM emissoes WHERE id=?", (eid,)).fetchone()
        return jsonify({"ok": True, "emissao": _emissao_row(r)})

    @app.route("/bm/emissao/<eid>", methods=["GET", "OPTIONS"])
    def bm_emissao_ler(eid):
        g = _guard()
        if g:
            return g
        with _con() as c:
            r = c.execute("SELECT * FROM emissoes WHERE id=?", (eid,)).fetchone()
        if not r:
            return jsonify({"ok": False, "error": "emissão não encontrada"}), 404
        return jsonify({"ok": True, "emissao": _emissao_row(r, com_snapshot=True)})

    @app.route("/bm/emissao/<eid>/pdf", methods=["POST", "OPTIONS"])
    def bm_emissao_pdf(eid):
        g = _guard()
        if g:
            return g
        f = request.files.get("arquivo")
        if not f:
            return jsonify({"ok": False, "error": "arquivo obrigatório"}), 400
        with _con() as c:
            r = c.execute("SELECT status FROM emissoes WHERE id=?", (eid,)).fetchone()
        if not r:
            return jsonify({"ok": False, "error": "emissão não encontrada"}), 404
        rotulo = str(request.form.get("usina") or "")
        try:
            a = _gravar_arquivo(f, eid, "pdf_emissao", rotulo)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        with _LOCK, _con() as c:
            # mesmo PDF da mesma usina regravado enquanto a emissão não saiu: guarda só o último
            if r["status"] == "gerado":
                c.execute("UPDATE anexos SET dono='__substituido__:' || dono WHERE dono=? AND tipo='pdf_emissao' AND rotulo=?",
                          (eid, rotulo))
            _inserir_anexo(c, a)
        a.pop("caminho")
        return jsonify({"ok": True, "anexo": a})

    @app.route("/bm/emissao/status", methods=["POST", "OPTIONS"])
    def bm_emissao_status():
        g = _guard()
        if g:
            return g
        b = request.get_json(force=True, silent=True) or {}
        eid = str(b.get("id") or "")
        st = str(b.get("status") or "")
        if st not in STATUS_VALIDOS:
            return jsonify({"ok": False, "error": "status inválido"}), 400
        data = str(b.get("data") or "") or _agora()
        info_nova = b.get("info") or {}
        agora = _agora()
        with _LOCK, _con() as c:
            r = c.execute("SELECT * FROM emissoes WHERE id=?", (eid,)).fetchone()
            if not r:
                return jsonify({"ok": False, "error": "emissão não encontrada"}), 404
            info = json.loads(r["info"] or "{}")
            info.update({k: v for k, v in info_nova.items() if v not in (None, "")})
            hist = json.loads(r["historico"] or "[]") + [{"ts": agora, "acao": st, "data": data,
                                                         "info": info_nova}]
            campos = {"status": st, "info": json.dumps(info, ensure_ascii=False),
                      "historico": json.dumps(hist, ensure_ascii=False)}
            if st == "enviado":
                campos["enviado_em"] = data
            elif st == "aprovado":
                campos["aprovado_em"] = data
                if not r["enviado_em"]:
                    campos["enviado_em"] = data
            elif st == "nf":
                campos["nf_em"] = data
            elif st == "gerado":  # reabrir
                campos.update({"enviado_em": None, "aprovado_em": None, "nf_em": None})
            sets = ", ".join(f"{k}=?" for k in campos)
            c.execute(f"UPDATE emissoes SET {sets} WHERE id=?", (*campos.values(), eid))
            _log(c, "emissao_status", {"id": eid, "status": st, "data": data})
            r2 = c.execute("SELECT * FROM emissoes WHERE id=?", (eid,)).fetchone()
            cli = c.execute("SELECT doc FROM clientes WHERE id=?", (r["cliente_id"],)).fetchone()
        aviso_comp = ""
        if hook_compromisso and cli and st in ("enviado", "aprovado", "nf"):
            try:
                nome_comp = (json.loads(cli["doc"]).get("compromissoCliente") or "").strip()
                if nome_comp:
                    aviso_comp = hook_compromisso(nome_comp, r["competencia"],
                                                  {"enviado": 0, "aprovado": 1, "nf": 2}[st]) or ""
            except Exception as e:  # nunca derruba o registro do BM
                log.error(f"[bm] hook compromisso falhou: {e}")
                aviso_comp = f"Card de prazos não atualizado: {e}"
        return jsonify({"ok": True, "emissao": _emissao_row(r2), "avisoCompromisso": aviso_comp})

    # ── importação (migração) e diagnóstico ──────────────────────────
    @app.route("/bm/importar", methods=["POST", "OPTIONS"])
    def bm_importar():
        """Carga inicial idempotente (upsert por id). Só com X-Deploy-Secret."""
        if request.method == "OPTIONS":
            return ("", 204)
        if not _deploy_ok():
            return jsonify({"ok": False, "error": "não autorizado"}), 403
        b = request.get_json(force=True, silent=True) or {}
        agora = _agora()
        n = {"clientes": 0, "lancamentos": 0, "emissoes": 0}
        with _LOCK, _con() as c:
            for d in b.get("clientes") or []:
                c.execute("INSERT INTO clientes(id, doc, ativo, atualizado) VALUES (?,?,?,?) "
                          "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc, ativo=excluded.ativo, atualizado=excluded.atualizado",
                          (d["id"], json.dumps({k: v for k, v in d.items() if not k.startswith("_")}, ensure_ascii=False),
                           0 if d.get("_ativo") is False else 1, agora))
                n["clientes"] += 1
            for d in b.get("lancamentos") or []:
                corpo = {k: v for k, v in d.items() if k not in ("id", "clienteId", "competencia")}
                c.execute("INSERT INTO lancamentos(id, cliente_id, competencia, doc, excluido, criado, atualizado) "
                          "VALUES (?,?,?,?,0,?,?) ON CONFLICT(id) DO UPDATE SET doc=excluded.doc, "
                          "competencia=excluded.competencia, atualizado=excluded.atualizado",
                          (d["id"], d["clienteId"], d["competencia"], json.dumps(corpo, ensure_ascii=False), agora, agora))
                n["lancamentos"] += 1
            for d in b.get("emissoes") or []:
                c.execute("INSERT OR REPLACE INTO emissoes(id, cliente_id, competencia, revisao, usinas, status, snapshot, "
                          "total, hash, titulo, criado, enviado_em, aprovado_em, nf_em, info, historico, origem) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (d["id"], d["clienteId"], d["competencia"], d.get("revisao", 0),
                           json.dumps(d.get("usinas") or [], ensure_ascii=False), d.get("status", "enviado"),
                           None, d.get("total"), None, d.get("titulo"), d.get("criado") or agora,
                           d.get("enviadoEm"), d.get("aprovadoEm"), d.get("nfEm"),
                           json.dumps(d.get("info") or {}, ensure_ascii=False),
                           json.dumps(d.get("historico") or [], ensure_ascii=False), d.get("origem") or "historico"))
                n["emissoes"] += 1
            _log(c, "importacao", n)
        return jsonify({"ok": True, "importado": n})

    @app.route("/bm/exportar", methods=["GET", "OPTIONS"])
    def bm_exportar():
        """Backup completo em JSON (inclui snapshots das emissões; não inclui os arquivos)."""
        g = _guard()
        if g:
            return g
        with _con() as c:
            dados = {
                "exportadoEm": _agora(),
                "clientes": [dict(r) for r in c.execute("SELECT * FROM clientes")],
                "lancamentos": [dict(r) for r in c.execute("SELECT * FROM lancamentos")],
                "anexos": [{k: r[k] for k in r.keys() if k != "caminho"} for r in c.execute("SELECT * FROM anexos")],
                "emissoes": [dict(r) for r in c.execute("SELECT * FROM emissoes")],
            }
        return jsonify(dados)

    @app.route("/bm/diagnostico", methods=["GET", "OPTIONS"])
    def bm_diagnostico():
        """Contagens + últimos eventos. X-Deploy-Secret ou chave do BM."""
        if request.method == "OPTIONS":
            return ("", 204)
        if not _deploy_ok():
            g = _guard()
            if g:
                return g
        with _con() as c:
            cont = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("clientes", "lancamentos", "anexos", "emissoes", "log")}
            chave = bool(c.execute("SELECT 1 FROM kv WHERE k='chave_hash'").fetchone())
            ultimos = [dict(r) for r in c.execute("SELECT ts, acao, detalhe FROM log ORDER BY id DESC LIMIT 30")]
            ems = [_emissao_row(r) for r in c.execute(
                "SELECT * FROM emissoes ORDER BY competencia DESC, revisao DESC LIMIT 60")]
            clis = [{"id": r["id"], "nome": json.loads(r["doc"]).get("nome"), "ref": json.loads(r["doc"]).get("ref"),
                     "ativo": r["ativo"]} for r in c.execute("SELECT * FROM clientes")]
        return jsonify({"ok": True, "contagens": cont, "chaveDefinida": chave, "clientes": clis,
                        "emissoes": ems, "log": ultimos})

    log.info("[bm] rotas /bm/* registradas (boletins_medicao.db)")
