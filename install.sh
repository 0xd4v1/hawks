#!/bin/bash

# Hawks Installation Script

echo "Hawks Installation"
echo "=================="

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 not found. Please install Python 3.8+"
    exit 1
fi
echo "Python 3 found"

# Check pip
if ! command -v pip3 &> /dev/null; then
    echo "Error: pip3 not found. Please install pip"
    exit 1
fi
echo "pip3 found"

# Check git
if ! command -v git &> /dev/null; then
    echo "Error: git not found. Please install git"
    exit 1
fi
echo "git found"

# Install/check Go
if ! command -v go &> /dev/null; then
    echo "Installing Go..."
    
    ARCH=$(uname -m)
    case $ARCH in
        x86_64) GO_ARCH="amd64" ;;
        aarch64) GO_ARCH="arm64" ;;
        armv6l) GO_ARCH="armv6l" ;;
        armv7l) GO_ARCH="armv6l" ;;
        *) echo "Error: Unsupported architecture: $ARCH"; exit 1 ;;
    esac
    
    GO_VERSION="1.21.5"
    GO_TAR="go${GO_VERSION}.linux-${GO_ARCH}.tar.gz"
    
    wget -q "https://golang.org/dl/${GO_TAR}" -O "/tmp/${GO_TAR}"
    mkdir -p "$HOME/go"
    tar -C "$HOME" -xzf "/tmp/${GO_TAR}"
    
    export PATH="$HOME/go/bin:$PATH"
    export GOPATH="$HOME/go"
    export GOROOT="$HOME/go"
    
    echo 'export PATH="$HOME/go/bin:$PATH"' >> "$HOME/.bashrc"
    echo 'export GOPATH="$HOME/go"' >> "$HOME/.bashrc"
    echo 'export GOROOT="$HOME/go"' >> "$HOME/.bashrc"
    
    rm "/tmp/${GO_TAR}"
    echo "Go installed"
else
    echo "Go found"
    export PATH="$HOME/go/bin:$PATH"
fi

# Create virtual environment
echo "Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt

# Install tools
echo "Installing reconnaissance tools..."
go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest
go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
go install -v github.com/projectdiscovery/chaos-client/cmd/chaos@latest

GO_BIN_PATH="$HOME/go/bin"
if [[ ":$PATH:" != *":$GO_BIN_PATH:"* ]]; then
    export PATH="$GO_BIN_PATH:$PATH"
fi

# Update Nuclei templates
echo "Updating Nuclei templates..."
$HOME/go/bin/nuclei -update-templates

# Create directories
mkdir -p templates/custom

# Create .env file
if [ ! -f .env ]; then
    echo "Creating .env file..."
    
    # Generate secure secret key
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
    
    # Generate secure password
    SECURE_PASSWORD=$(python3 -c "import secrets, string; chars = string.ascii_letters + string.digits + '!@#$%^&*'; print(''.join(secrets.choice(chars) for _ in range(16)))")
    
    cat > .env << EOF
SECRET_KEY=$SECRET_KEY
ADMIN_USERNAME=admin
ADMIN_PASSWORD=$SECURE_PASSWORD
CHAOS_API_KEY=
DATABASE_URL=sqlite:///./hawks.db
EOF
    echo "================================================"
    echo "🔐 IMPORTANT SECURITY INFORMATION 🔐"
    echo "================================================"
    echo "Admin Username: admin"
    echo "Admin Password: $SECURE_PASSWORD"
    echo "================================================"
    echo "⚠️  SAVE THESE CREDENTIALS SAFELY!"
    echo "⚠️  Change the password after first login!"
    echo "================================================"
    echo ".env file created with secure credentials."
else
    echo ".env file already exists. Skipping creation."
fi

# Initialize database
echo "Initializing database..."
python3 -c "from app.database import init_db; init_db()"

# Verify installations
echo "Verifying installations..."
TOOLS_OK=true

if ! $HOME/go/bin/subfinder -version > /dev/null 2>&1; then
    echo "Error: Subfinder installation failed"
    TOOLS_OK=false
fi

if ! $HOME/go/bin/httpx -version > /dev/null 2>&1; then
    echo "Error: HTTPX installation failed"
    TOOLS_OK=false
fi

if ! $HOME/go/bin/nuclei -version > /dev/null 2>&1; then
    echo "Error: Nuclei installation failed"
    TOOLS_OK=false
fi

if ! $HOME/go/bin/chaos -version > /dev/null 2>&1; then
    echo "Error: Chaos installation failed"
    TOOLS_OK=false
fi

if [ "$TOOLS_OK" = true ]; then
    echo "Installation completed successfully"
else
    echo "Installation completed with errors"
fi

echo ""
echo "To start:"
echo "  source venv/bin/activate"
echo "  python main.py"
echo "  Open: http://localhost:8000"
echo "  Login: admin / hawks"
