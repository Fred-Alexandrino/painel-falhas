# WhatsApp → Painel de Falhas
Sistema que captura mensagens de grupos do WhatsApp e alimenta automaticamente
o Google Sheets "Acompanhamento de Falhas - O&M V2".

## Passo a passo de configuração

### 1. Google Service Account (5 minutos, gratuito)
1. Acesse https://console.cloud.google.com
2. Crie um projeto novo (ex: "painel-falhas")
3. Ative a API: "Google Sheets API" e "Google Drive API"
4. Vá em "Credenciais" → "Criar credenciais" → "Conta de serviço"
5. Dê um nome (ex: "painel-bot") e clique em "Criar e continuar"
6. Na lista de contas de serviço, clique na criada → aba "Chaves" → "Adicionar chave" → JSON
7. Baixe o arquivo JSON — você vai colar o conteúdo no Render
8. **Compartilhe a planilha** com o e-mail da service account (aparece no JSON como "client_email")
   → No Google Sheets: Compartilhar → cole o e-mail → permissão "Editor"

### 2. Deploy no Render (gratuito)
1. Crie conta em https://render.com
2. "New" → "Web Service" → conecte ao GitHub (suba essa pasta como repositório)
3. Runtime: Python | Build: `pip install -r requirements.txt` | Start: `gunicorn app:app --bind 0.0.0.0:$PORT`
4. Configure as variáveis de ambiente:
   - `GOOGLE_CREDENTIALS_JSON`: cole TODO o conteúdo do arquivo JSON baixado
   - `SHEET_ID`: 1VLo8__wxSJVWiUIFd_JTcOnadJlUt440i1M1pC0ehTs
   - `SHEET_NAME`: Acompanhamento de Falhas - O&M V2
   - `WEBHOOK_SECRET`: invente uma senha (ex: minhasenha123)
5. Clique em "Deploy" — em ~2 minutos você terá uma URL como https://whatsapp-painel.onrender.com

### 3. Evolution API (gratuito, self-hosted)
Opção mais simples: usar a instância demo ou fazer deploy também no Render.
1. Fork do repo: https://github.com/EvolutionAPI/evolution-api
2. Deploy no Render como segundo serviço
3. Acesse o painel da Evolution API → crie uma instância → escaneie o QR code com o celular
4. Configure o webhook: URL do seu servidor + /webhook
   Ex: https://whatsapp-painel.onrender.com/webhook
5. Ative os eventos: "MESSAGES_UPSERT"

### 4. Filtrar os grupos certos
No painel da Evolution API, envie uma mensagem de teste em cada grupo de falhas.
Nos logs do Render você verá o ID do grupo (ex: 5521999999999-1234567890@g.us).
Cole os IDs na variável `GRUPOS_IDS` separados por vírgula.

### 5. Testar
```bash
curl -X POST https://seu-servidor.onrender.com/test \
  -H "Content-Type: application/json" \
  -d '{"texto": "🔴 Usina: Ibaté I\n* Problema: Inversor desligado 10\n* Descrição dos Problemas: Baixa impedância.\n* Ação: Técnico acionado.\n* Equipe Acionada: Sim, @Rodolfo Oliveira - SP-1\n* Supervisor Acionado: sim, Fred\n* Inicio ocorrência: 25/06/2026 - 08:00\n* Fim ocorrência:\n* Nº da OS: --"}'
```

## Formato esperado das mensagens
```
🔴 Usina: Ibaté I
* Problema: Inversor desligado 10
* Descrição dos Problemas: Inversor 10 com baixa impedância de isolamento.
* Impacto: 200KW
* Ação: Técnico acionado para verificação.
* Equipe Acionada: Sim, @Rodolfo Oliveira - SP-1
* Supervisor Acionado: sim, Fred
* Inicio ocorrência: 25/06/2026 - 06:00
* Fim ocorrência:
* Nº da OS: --
```
