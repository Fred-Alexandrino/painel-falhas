# Central O&M — backend (VM1)

- Antes de mexer em qualquer gerador de texto ou relatório, leia `docs/PADRAO_COMUNICACAO_RELATORIOS_GRID.md` no repositório `PAINELDEFALHAS` (fonte de verdade do padrão de comunicação da Grid Co.).
- Central de Mensagens: `comunicacao_mensagens.py`, registrado no fim do `app.py` (2 linhas — não remover ao reenviar o `app.py`). O system prompt é o texto da seção 11.5, sem alteração. Testes: `python3 -m unittest discover -s tests -p "test_comunicacao*.py"`.
- Chaves e secrets só no `.env` da VM1.
- Boletins de Medição: `boletins_medicao.py` (rotas `/bm/*`, SQLite `boletins_medicao.db` + arquivos em `bm_arquivos/`), registrado no fim do `app.py` junto com `_bm_hook_compromisso` — não remover ao reenviar o `app.py`.
