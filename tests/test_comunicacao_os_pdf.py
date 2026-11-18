# -*- coding: utf-8 -*-
"""18/11/2026: PDF no envio de relatório e OS da Fracttal como documento anexado."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comunicacao_mensagens as cm  # noqa: E402

BASE = {"tipo": "envio_relatorio", "cliente": "Thopen", "rascunho": "", "dataHoraEnvio": "18/11/2026 10:00"}

OS = {"wo_folio": "8626", "company": {"description": "GRID CO. LTDA"}, "created_by": "Juliana", "personnel_description": "Eduardo",
      "creation_date": "2026-06-29T14:24:58+00:00", "completed_percentage": 100, "note": "Aguardando drone",
      "tasks": [{"items_log_description": "Ar Condicionado { X-ARCD }", "items_log_groups_1_description": "Thopen - Topázio (Matão 2) - SP",
                 "tasks_description": "MPA - Ar Condicionado", "tasks_types_main_description": "Preventiva", "done": True,
                 "initial_date": "2026-07-01T11:17:14+00:00", "final_date": "2026-07-01T11:40:41+00:00", "real_duration": 1407,
                 "task_form_items": [
                     {"id_task_form_item_type": 4, "value": "1", "description": "Verificar corrosão", "order_number": 1},
                     {"id_task_form_item_type": 4, "value": "3", "description": "Termografia", "order_number": 2},
                     {"id_task_form_item_type": 1, "value": "Limpeza geral", "description": "Descreva a atividade", "order_number": 3}],
                 "task_images": [{"type": 3, "value": "Tudo ok"}]}]}


class TestPdfEDocumento(unittest.TestCase):
    def test_pdf_aceito_so_no_envio_de_relatorio(self):
        partes = cm.prints_validos(dict(BASE, prints=[{"base64": "QUJD", "mimeType": "application/pdf"}]))
        self.assertEqual(partes[0]["inline_data"]["mime_type"], "application/pdf")
        with self.assertRaises(ValueError):
            cm.prints_validos({"tipo": "cliente", "prints": [{"base64": "QUJD", "mimeType": "application/pdf"}]})

    def test_so_pdf_sem_rascunho_gera_contexto(self):
        c = cm.montar_contexto(dict(BASE, prints=[{"base64": "QUJD", "mimeType": "application/pdf"}]))
        self.assertIn("PDF ANEXADO", c)
        self.assertNotIn("PRINTS ANEXADOS", c)

    def test_documento_da_os_vai_no_contexto(self):
        texto = cm.montar_texto_os(OS)
        self.assertIn("ORDEM DE TRABALHO Nº 8626", texto)
        self.assertIn("Verificar corrosão: Aprovou", texto)
        self.assertIn("Falhou — Ar Condicionado { X-ARCD }: Termografia", texto)
        self.assertIn("Observação: Tudo ok", texto)
        c = cm.montar_contexto(dict(BASE, documentos=[{"nome": "OS 8626 (Fracttal)", "texto": texto}]))
        self.assertIn("DOCUMENTO ANEXADO — OS 8626 (Fracttal)", c)
        self.assertIn("Termografia", c)

    def test_link_sem_printwo(self):
        with self.assertRaises(ValueError):
            cm.ler_os_fracttal("https://app.fracttal.com/tasks/wo")


if __name__ == "__main__":
    unittest.main()
