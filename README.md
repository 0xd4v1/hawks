# 🦅 Hawks - Reconnaissance Platform

Hawks é uma plataforma web minimalista para reconnaissance automatizado com interface preta e branca.

## ✨ Funcionalidades

### 🎯 Gerenciamento de Alvos
- **Adicionar alvos individuais**: Domínios ou IPs únicos
- **Upload em lote**: Arquivo .txt/.csv/.list com múltiplos domínios
- **Seleção flexível**: Scan individual, selecionados ou todos
- **Dashboard por alvo**: Métricas detalhadas e histórico

### ⚡ Sistema de Filas Inteligente
- **Execução imediata**: Se não há scans ativos, executa na hora
- **Fila automática**: Gerencia até 3 scans simultâneos por padrão
- **Controle de threads**: 8 threads por scan (configurável)
- **Status em tempo real**: Atualização automática da interface

### 🔧 Pipeline de Scan
1. **Subfinder**: Descoberta de subdomínios
2. **Chaos** (opcional): Subdomínios adicionais via API
3. **HTTPX**: Verificação de hosts ativos (URLs válidas)
4. **Nuclei**: Scanning de vulnerabilidades com templates custom

### 📁 Gerenciamento de Templates
- **Upload YAML/ZIP**: Templates individuais ou pacotes
- **Clone GitHub**: Importar repositórios de templates
- **Armazenamento físico**: Templates salvos em `templates/custom/`
- **Ativação seletiva**: Controle por template

### 🔐 Autenticação e Segurança
- **Login JWT**: Autenticação via cookie seguro
- **Admin único**: Sem cadastro público
- **Configuração .env**: Senhas e chaves seguras

## 🚀 Instalação

```bash
# Clone e configure
git clone <repo>
cd hawks

# Instalar dependências e ferramentas
chmod +x install.sh
./install.sh

# Configurar variáveis (opcional)
cp .env.example .env
nano .env

# Executar
python main.py
```

## ⚙️ Configuração (.env)

```env
SECRET_KEY=hawks-super-secret-key-change-in-production
ADMIN_USERNAME=admin
ADMIN_PASSWORD=hawks
CHAOS_API_KEY=                    # Opcional para Chaos
DATABASE_URL=sqlite:///./hawks.db
MAX_CONCURRENT_SCANS=3            # Máximo scans simultâneos
SCAN_THREADS=8                    # Threads por scan
```

## 📊 Como Usar

1. **Login**: `http://localhost:8000` (admin/hawks)
2. **Adicionar Alvos**: 
   - Individual: Digite domínio/IP
   - Lote: Upload arquivo .txt
3. **Executar Scans**:
   - Selecionar alvos específicos
   - Scan todos os alvos
   - Individual por target
4. **Monitorar**: Dashboard com status em tempo real
5. **Templates**: Upload ou clone via GitHub

## 🎛️ Recursos Avançados

### Sistema de Filas
- **Execução imediata** quando há capacidade
- **Fila automática** quando limite atingido
- **Status visual** da fila e scans ativos

### Upload de Templates
```bash
# Formatos aceitos
- arquivo.yaml (template único)
- pacote.zip (múltiplos templates)
- GitHub repo (clone automático)
```

### API Endpoints
```bash
GET  /api/targets          # Lista targets
GET  /api/queue-status     # Status da fila
POST /targets/scan-all     # Scan todos
POST /targets/scan-selected # Scan selecionados
```

## 🔧 Especificações Técnicas

- **Backend**: FastAPI + SQLAlchemy + Pydantic
- **Frontend**: Jinja2 + TailwindCSS (CDN)
- **Banco**: SQLite (configurável)
- **Ferramentas**: Subfinder, HTTPX, Nuclei, Chaos
- **Async**: Processamento assíncrono com filas

## 🎯 Otimizado para VPS

- **32GB RAM + 12 cores**: Configuração recomendada
- **3 scans simultâneos**: Padrão seguro
- **8 threads por scan**: Balanceamento otimizado
- **Interface responsiva**: Acesso remoto fácil

## 🚀 Teste Rápido

```bash
# Executar teste automatizado
python test_hawks.py
```

Desenvolvido para ser **simples**, **rápido** e **eficiente** em VPS dedicadas.