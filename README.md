# Hawks

Hawks é uma plataforma de reconhecimento web minimalista desenvolvida com FastAPI e Tailwind CSS. A aplicação oferece uma interface limpa em preto e branco para gerenciar alvos, executar scans automatizados e gerenciar templates do Nuclei.

## Características

- **Interface Minimalista**: Design limpo em preto e branco usando Tailwind CSS
- **Autenticação Simples**: Login apenas para administrador (sem cadastro)
- **Pipeline Automatizado**: Subfinder → Chaos (opcional) → HTTPX → Nuclei
- **Execução Assíncrona**: Jobs em background para cada ferramenta
- **CRUD Completo**: Gerenciamento de alvos e templates YAML
- **Monitoramento**: Dashboard com métricas e resultados recentes

## Estrutura do Projeto

```
hawks/
├── app/
│   ├── templates/          # Templates HTML
│   ├── static/            # Arquivos estáticos
│   ├── config.py          # Configurações
│   ├── database.py        # Modelos SQLAlchemy
│   ├── schemas.py         # Modelos Pydantic
│   └── scanner.py         # Engine de scanning
├── main.py                # Aplicação principal
├── requirements.txt       # Dependências
├── .env                   # Variáveis de ambiente
└── README.md
```

## Instalação

1. **Clone o repositório**:
```bash
git clone <repo-url>
cd hawks
```

2. **Configure o ambiente**:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate     # Windows
```

3. **Instale as dependências**:
```bash
pip install -r requirements.txt
```

4. **Configure o arquivo .env**:
```bash
SECRET_KEY=hawks-super-secret-key-change-in-production
ADMIN_USERNAME=admin
ADMIN_PASSWORD=hawks
CHAOS_API_KEY=your-chaos-api-key-here
DATABASE_URL=sqlite:///./hawks.db
```

5. **Execute a aplicação**:
```bash
python main.py
```

A aplicação estará disponível em `http://localhost:8000`

## Dependências Externas

Para funcionalidade completa, instale as seguintes ferramentas:

- **Subfinder**: `go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest`
- **Chaos**: `go install github.com/projectdiscovery/chaos-client/cmd/chaos@latest`
- **HTTPX**: `go install github.com/projectdiscovery/httpx/cmd/httpx@latest`
- **Nuclei**: `go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest`

## Uso

### Login
- Acesse `http://localhost:8000`
- Use as credenciais configuradas no .env
- Redirecionamento automático para o dashboard

### Dashboard
- Visão geral dos alvos e scans
- Métricas básicas
- Resultados recentes

### Alvos
- Adicionar novos domínios/IPs
- Executar scans automatizados
- Visualizar status dos scans
- Remover alvos

### Templates
- Gerenciar templates YAML do Nuclei
- Ativar/desativar templates
- Definir ordem de execução
- CRUD completo

### Scans
- Visualizar histórico de scans
- Resultados detalhados
- Status de execução
- Logs de erro

## API

A aplicação oferece endpoints básicos:

- `GET /api/targets` - Lista todos os alvos
- `GET /api/scan-results/{target_id}` - Resultados de um alvo específico

## Configuração

### Variáveis de Ambiente

- `SECRET_KEY`: Chave secreta para sessões
- `ADMIN_USERNAME`: Nome de usuário do administrador
- `ADMIN_PASSWORD`: Senha do administrador
- `CHAOS_API_KEY`: Chave da API do Chaos (opcional)
- `DATABASE_URL`: URL do banco de dados SQLite

### Personalização

- Modifique os templates HTML em `app/templates/`
- Ajuste cores no Tailwind CSS conforme necessário
- Configure timeout e outros parâmetros no scanner

## Pipeline de Scanning

1. **Subfinder**: Descoberta de subdomínios
2. **Chaos**: Subdomínios adicionais (se API key disponível)
3. **HTTPX**: Verificação de serviços HTTP/HTTPS
4. **Nuclei**: Execução de templates de vulnerabilidade

Cada etapa é executada de forma assíncrona e os resultados são armazenados no banco de dados.

## Segurança

- Autenticação baseada em cookies HTTP-only
- Validação de entrada com Pydantic
- Execução isolada de comandos externos
- Sanitização de templates YAML

## Desenvolvimento

Para contribuir com o projeto:

1. Fork o repositório
2. Crie uma branch para sua feature
3. Implemente as mudanças
4. Execute os testes
5. Abra um Pull Request

## Licença

Este projeto é distribuído sob a licença MIT.