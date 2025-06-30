#!/bin/bash

# Hawks Reconnaissance Scanner - Installation Script

echo "🦅 Hawks Reconnaissance Scanner Setup"
echo "======================================"

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.8+ and try again."
    exit 1
fi

echo "✅ Python 3 found"

# Check if pip is installed
if ! command -v pip3 &> /dev/null; then
    echo "❌ pip3 is not installed. Please install pip and try again."
    exit 1
fi

echo "✅ pip3 found"

# Check if Go is installed
if ! command -v go &> /dev/null; then
    echo "📥 Go not found. Installing Go..."
    
    # Detect architecture
    ARCH=$(uname -m)
    case $ARCH in
        x86_64) GO_ARCH="amd64" ;;
        aarch64) GO_ARCH="arm64" ;;
        armv6l) GO_ARCH="armv6l" ;;
        armv7l) GO_ARCH="armv6l" ;;
        *) echo "❌ Unsupported architecture: $ARCH"; exit 1 ;;
    esac
    
    # Download and install Go
    GO_VERSION="1.21.5"
    GO_TAR="go${GO_VERSION}.linux-${GO_ARCH}.tar.gz"
    
    wget -q "https://golang.org/dl/${GO_TAR}" -O "/tmp/${GO_TAR}"
    
    # Create local Go directory
    mkdir -p "$HOME/go"
    tar -C "$HOME" -xzf "/tmp/${GO_TAR}"
    
    # Add Go to PATH for this session
    export PATH="$HOME/go/bin:$HOME/go/bin:$PATH"
    export GOPATH="$HOME/go"
    export GOROOT="$HOME/go"
    
    # Add Go to bashrc for permanent PATH
    echo 'export PATH="$HOME/go/bin:$HOME/go/bin:$PATH"' >> "$HOME/.bashrc"
    echo 'export GOPATH="$HOME/go"' >> "$HOME/.bashrc"
    echo 'export GOROOT="$HOME/go"' >> "$HOME/.bashrc"
    
    rm "/tmp/${GO_TAR}"
    echo "✅ Go installed successfully"
else
    echo "✅ Go found"
    export PATH="$HOME/go/bin:$PATH"
fi

# Create virtual environment
echo "📦 Creating virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install Python dependencies
echo "📥 Installing Python dependencies..."
pip install -r requirements.txt

# Install reconnaissance tools
echo "🔍 Installing reconnaissance tools..."

echo "📥 Installing Subfinder..."
go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest

echo "📥 Installing HTTPX..."
go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest

echo "📥 Installing Nuclei..."
go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest

echo "📥 Installing Chaos..."
go install -v github.com/projectdiscovery/chaos-client/cmd/chaos@latest

# Add Go bin to PATH if not already there
GO_BIN_PATH="$HOME/go/bin"
if [[ ":$PATH:" != *":$GO_BIN_PATH:"* ]]; then
    export PATH="$GO_BIN_PATH:$PATH"
fi

# Update Nuclei templates
echo "📥 Updating Nuclei templates..."
$HOME/go/bin/nuclei -update-templates

# Create templates directory
echo "📁 Creating templates directory..."
mkdir -p templates/custom

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "⚙️  Creating .env file..."
    cat > .env << EOF
SECRET_KEY=hawks-super-secret-key-change-in-production-$(date +%s)
ADMIN_USERNAME=admin
ADMIN_PASSWORD=hawks
CHAOS_API_KEY=
DATABASE_URL=sqlite:///./hawks.db
EOF
    echo "✅ .env file created with default values"
    echo "⚠️  Please update the .env file with your settings, especially the passwords!"
fi

# Initialize database
echo "🗄️  Initializing database..."
python3 -c "from app.database import init_db; init_db()"

# Verify tools installation
echo ""
echo "🔍 Verifying tool installations..."

TOOLS_OK=true

if $HOME/go/bin/subfinder -version > /dev/null 2>&1; then
    echo "✅ Subfinder installed and working"
else
    echo "❌ Subfinder installation failed"
    TOOLS_OK=false
fi

if $HOME/go/bin/httpx -version > /dev/null 2>&1; then
    echo "✅ HTTPX installed and working"
else
    echo "❌ HTTPX installation failed"
    TOOLS_OK=false
fi

if $HOME/go/bin/nuclei -version > /dev/null 2>&1; then
    echo "✅ Nuclei installed and working"
else
    echo "❌ Nuclei installation failed"
    TOOLS_OK=false
fi

if $HOME/go/bin/chaos -version > /dev/null 2>&1; then
    echo "✅ Chaos installed and working"
else
    echo "❌ Chaos installation failed"
    TOOLS_OK=false
fi

echo ""
if [ "$TOOLS_OK" = true ]; then
    echo "🎉 Installation completed successfully!"
else
    echo "⚠️  Installation completed with some issues. Please check the errors above."
fi