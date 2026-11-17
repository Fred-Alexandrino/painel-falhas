# -*- coding: utf-8 -*-
"""Testes do backend da Central de Mensagens (sem chamar a IA de verdade).
Rodar: python3 -m unittest discover -s tests -p "test_comunicacao*.py" """
import json
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask  # noqa: E402

import comunicacao_mensagens as cm  # noqa: E402

BASE = {
    "tipo": "cliente",
    "destinatarios": ["Iago"],
    "ufvs": [{"ufv": "UFV Nova Londrina II", "cidadeUf": "Nova Londrina/PR", "cliente": "Thopen",
              "tecnico": "Ronaldo Silva", "funcao": "Eletricista"}],
    "dataHoraEnvio": "17/11/2026 09:00",
    "rascunho": "inversor 2.1 com fusível CC queimado na string 9, sem peça em estoque",
}


class FakeResp:
    def __init__(self, texto):
        self._d = {"candidates": [{"content": {"parts": [{"text": texto}]}}]}

    def json(self):
        return self._d


class TestContexto(unittest.TestCase):
    def test_campos_do_bloco_11_5(self):
        c = cm.montar_contexto(dict(BASE))
        for trecho in ("TIPO: cliente", "DESTINATÁRIO(S): Iago",
                       "UFV(s): UFV Nova Londrina II, Nova Londrina/PR, cliente Thopen, técnico responsável Ronaldo Silva (Eletricista)",
                       "DATA/HORA DE ENVIO: 17/11/2026 09:00", "RASCUNHO/RELATO:\ninversor 2.1"):
            self.assertIn(trecho, c)
        self.assertNotIn("AJUSTE PEDIDO", c)

    def test_regras_da_central_sem_saudacao(self):
        c = cm.montar_contexto(dict(BASE))
        self.assertIn("NÃO escreva saudação", c)
        self.assertIn("Não anuncie a cordialidade", c)

    def test_ajuste_leva_versao_atual_completa(self):
        b = dict(BASE, ajuste="mais curto", versaoAtual={"mensagens": [{"texto": "Texto A"}, {"texto": "Texto B"}]})
        c = cm.montar_contexto(b)
        self.assertIn("AJUSTE PEDIDO (se houver): mais curto  → devolver o texto COMPLETO corrigido", c)
        self.assertIn('"Texto A"', c)
        self.assertIn('"Texto B"', c)

    def test_dividir(self):
        b = dict(BASE, dividir=True, versaoAtual={"mensagens": [{"texto": "Texto longo"}]})
        self.assertIn("Divida em 2 mensagens", cm.montar_contexto(b))

    def test_erros_de_entrada(self):
        for b, trecho in (
            (dict(BASE, tipo="sms"), "tipo inválido"),
            (dict(BASE, rascunho=""), "rascunho"),
            (dict(BASE, ufvs=[]), "UFV"),
            (dict(BASE, ajuste="x"), "versão anterior"),
            (dict(BASE, rascunho="x" * 12001), "muito longo"),
        ):
            with self.assertRaises(ValueError) as e:
                cm.montar_contexto(b)
            self.assertIn(trecho, str(e.exception))

    def test_prompt_vai_como_system_instruction(self):
        p = cm.payload_gemini("ctx")
        self.assertEqual(p["system_instruction"]["parts"][0]["text"], cm.SYSTEM_PROMPT_11_5)
        self.assertEqual(p["generationConfig"]["responseMimeType"], "application/json")


class TestInterpretar(unittest.TestCase):
    def test_json_normal(self):
        r = cm.interpretar_resposta(json.dumps({"introducao": "ok", "mensagens": [{"texto": "A"}, {"texto": " "}],
                                                "observacoes": ["obs"]}), "cliente")
        self.assertEqual(r, {"introducao": "ok", "mensagens": [{"texto": "A"}], "observacoes": ["obs"]})

    def test_json_com_cerca_e_assunto_so_no_email(self):
        bruto = '```json\n{"mensagens":[{"assunto":"UFV X — Y","texto":"T"}]}\n```'
        self.assertEqual(cm.interpretar_resposta(bruto, "email")["mensagens"], [{"assunto": "UFV X — Y", "texto": "T"}])
        self.assertEqual(cm.interpretar_resposta(bruto, "cliente")["mensagens"], [{"texto": "T"}])

    def test_sem_mensagem_e_erro(self):
        with self.assertRaises(ValueError):
            cm.interpretar_resposta('{"mensagens":[]}', "cliente")
        with self.assertRaises(ValueError):
            cm.interpretar_resposta("não é json", "cliente")


class TestEndpoint(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.chamadas = []

        def fake(payload, timeout=45):
            self.chamadas.append(payload)
            return FakeResp(json.dumps({"mensagens": [{"texto": "Corpo *UFV Nova Londrina II*.\n\nSeguimos à disposição."}]}))

        cm.registrar(self.app, fake, logging.getLogger("t"))
        self.c = self.app.test_client()

    def test_gerar(self):
        r = self.c.post("/comunicacao/gerar", json=BASE)
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(len(d["mensagens"]), 1)
        self.assertEqual(len(self.chamadas), 1)

    def test_400_sem_chamar_ia(self):
        r = self.c.post("/comunicacao/gerar", json=dict(BASE, ufvs=[]))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.chamadas, [])

    def test_502_quando_ia_falha(self):
        app = Flask(__name__)

        def falha(payload, timeout=45):
            raise RuntimeError("429 Too Many Requests")

        cm.registrar(app, falha, logging.getLogger("t"))
        r = app.test_client().post("/comunicacao/gerar", json=BASE)
        self.assertEqual(r.status_code, 502)
        self.assertIn("cota", r.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
