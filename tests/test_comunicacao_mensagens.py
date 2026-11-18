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
        self.assertIn("EXEMPLOS DE REFERÊNCIA", c)
        self.assertIn("Aguardo retorno.", cm.montar_contexto(dict(BASE, tipo="tecnico")))

    def test_cargos_vao_no_contexto(self):
        c = cm.montar_contexto(dict(BASE, pessoas=[{"nome": "Vitor Valadares", "cargo": "Supervisor de Campo"}]))
        self.assertIn("CARGOS: Vitor Valadares — Supervisor de Campo", c)

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
            (dict(BASE, ufvs=[]), "cliente"),
            (dict(BASE, tipo="tecnico", ufvs=[]), "UFV"),
            (dict(BASE, rascunho=""), "rascunho"),
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


class TestClienteEPrints(unittest.TestCase):
    def test_cliente_sem_ufv(self):
        c = cm.montar_contexto(dict(BASE, ufvs=[], cliente="Thopen"))
        self.assertIn("não selecionadas (cliente Thopen)", c)

    def test_so_print_sem_rascunho(self):
        b = dict(BASE, rascunho="", prints=[{"base64": "QUJD", "mimeType": "image/png"}])
        c = cm.montar_contexto(b)
        self.assertIn("PRINTS ANEXADOS: 1", c)
        partes = cm.payload_gemini(c, cm.prints_validos(b))["contents"][0]["parts"]
        self.assertEqual(partes[1], {"inline_data": {"mime_type": "image/png", "data": "QUJD"}})

    def test_data_url_e_limites(self):
        self.assertEqual(cm.prints_validos({"prints": [{"base64": "data:image/jpeg;base64,QUJD", "mimeType": "image/jpeg"}]})[0]["inline_data"]["data"], "QUJD")
        for prints, trecho in (([{"base64": "QQ==", "mimeType": "application/pdf"}], "imagem"),
                               ([{"base64": "QQ=="}] * 5, "no máximo")):
            with self.assertRaises(ValueError) as e:
                cm.prints_validos({"prints": prints})
            self.assertIn(trecho, str(e.exception))


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

    def test_gerar_com_print_vai_para_a_ia(self):
        r = self.c.post("/comunicacao/gerar", json=dict(BASE, prints=[{"base64": "QUJD", "mimeType": "image/png"}]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.chamadas[-1]["contents"][0]["parts"]), 2)

    def test_gerar(self):
        r = self.c.post("/comunicacao/gerar", json=BASE)
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(len(d["mensagens"]), 1)
        self.assertEqual(len(self.chamadas), 1)

    def test_400_sem_chamar_ia(self):
        r = self.c.post("/comunicacao/gerar", json=dict(BASE, tipo="tecnico", ufvs=[]))
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


class TestAssinaturaEmail(unittest.TestCase):
    def test_corta_bloco_depois_de_atenciosamente(self):
        from comunicacao_mensagens import cortar_assinatura_email
        t = "Corpo.\n\nFico no aguardo.\n\nAtenciosamente,\n\nFred Alexandrino\nSupervisor de O&M\nGrid Co."
        self.assertEqual(cortar_assinatura_email(t), "Corpo.\n\nFico no aguardo.\n\nAtenciosamente,")

    def test_interpretar_email_sem_assinatura(self):
        from comunicacao_mensagens import interpretar_resposta
        import json as _j
        r = interpretar_resposta(_j.dumps({"mensagens": [{"assunto": "X", "texto": "A.\n\nAtenciosamente,\nFred Alexandrino\nSupervisor de O&M\nGrid Co."}]}), "email")
        self.assertTrue(r["mensagens"][0]["texto"].endswith("Atenciosamente,"))
