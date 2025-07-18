from fastapi import FastAPI, Request, Depends, HTTPException, Form, BackgroundTasks, status, UploadFile, File
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import json
import asyncio
import jwt
import zipfile
import yaml
import tempfile
import shutil
import git
import os
import secrets
import hashlib
import time
import re
from urllib.parse import urlparse
from typing import List, Optional
from passlib.context import CryptContext
import html

from app.database import get_db, init_db, HawksTarget as HawksTargetDB, HawksTemplate as HawksTemplateDB, HawksScanResult, HawksSettings as HawksSettingsDB
from app.schemas import HawksTargetCreate, HawksTarget, HawksTemplateCreate, HawksTemplate, HawksLoginRequest, HawksSettings
from app.scanner import hawks_scanner
from app.config import hawks_config

# Security setup
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Rate limiting storage
login_attempts = {}
RATE_LIMIT_ATTEMPTS = 5
RATE_LIMIT_WINDOW = 900  # 15 minutes

app = FastAPI(title="Hawks", docs_url=None, redoc_url=None, debug=False)

# Security middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # More permissive for development
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
security = HTTPBearer(auto_error=False)

# Initialize database with error handling
try:
    print("Hawks - Initializing database...")
    init_db()
    print("Hawks - Database initialized successfully")
except Exception as e:
    print(f"Hawks - Database initialization error: {e}")
    print("Hawks - Attempting to create database directory and retry...")
    
    # Try to create the database directory and retry
    try:
        # Create the current directory if it doesn't exist
        if not os.path.exists('.'):
            os.makedirs('.', exist_ok=True)
        
        # Retry database initialization
        init_db()
        print("Hawks - Database initialized successfully on retry")
    except Exception as retry_error:
        print(f"Hawks - Database initialization failed on retry: {retry_error}")
        print("Hawks - Application will continue but database operations may fail")

# Security functions
def verify_password(plain_password, hashed_password):
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    """Hash a password"""
    return pwd_context.hash(password)

def get_or_create_settings(db: Session) -> HawksSettingsDB:
    """Obtém as configurações do banco de dados, criando se não existirem."""
    settings = db.query(HawksSettingsDB).filter(HawksSettingsDB.id == 1).first()
    if not settings:
        settings = HawksSettingsDB(id=1, chaos_api_key="", chaos_enabled=False)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings

def is_rate_limited(ip_address: str) -> bool:
    """Check if IP is rate limited"""
    current_time = time.time()
    
    # Clean old entries
    for ip in list(login_attempts.keys()):
        if current_time - login_attempts[ip]['last_attempt'] > RATE_LIMIT_WINDOW:
            del login_attempts[ip]
    
    if ip_address not in login_attempts:
        return False
    
    attempts = login_attempts[ip_address]
    if attempts['count'] >= RATE_LIMIT_ATTEMPTS:
        if current_time - attempts['last_attempt'] < RATE_LIMIT_WINDOW:
            return True
        else:
            # Reset after window
            del login_attempts[ip_address]
            return False
    
    return False

def record_failed_login(ip_address: str):
    """Record a failed login attempt"""
    current_time = time.time()
    
    if ip_address not in login_attempts:
        login_attempts[ip_address] = {'count': 0, 'last_attempt': current_time}
    
    login_attempts[ip_address]['count'] += 1
    login_attempts[ip_address]['last_attempt'] = current_time

def validate_domain_input(domain: str) -> str:
    """Validate and sanitize domain input"""
    if not domain:
        raise ValueError("Domain cannot be empty")
    
    # Remove whitespace and convert to lowercase
    domain = domain.strip().lower()
    
    # Remove protocol if present
    if domain.startswith(('http://', 'https://')):
        domain = domain.split('://', 1)[1]
    
    # Remove path if present
    if '/' in domain:
        domain = domain.split('/')[0]
    
    # Basic domain validation
    if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$', domain):
        # Try IP validation
        ip_pattern = r'^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
        if not re.match(ip_pattern, domain):
            raise ValueError("Invalid domain or IP address format")
    
    return domain

def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal"""
    # Remove directory traversal attempts
    filename = os.path.basename(filename)
    
    # Remove dangerous characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    
    # Limit length
    if len(filename) > 255:
        filename = filename[:255]
    
    return filename

def validate_yaml_content(content: str) -> bool:
    """Validate YAML content for security"""
    try:
        # Parse YAML safely
        data = yaml.safe_load(content)
        
        # Check for dangerous constructs
        content_lower = content.lower()
        dangerous_patterns = [
            'system', 'exec', 'eval', 'import', 'subprocess',
            '__import__', 'open(', 'file(', 'input(', 'raw_input('
        ]
        
        for pattern in dangerous_patterns:
            if pattern in content_lower:
                return False
        
        return True
    except yaml.YAMLError:
        return False

@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Add security headers to all responses"""
    response = await call_next(request)
    
    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    
    # CSP header
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline' cdn.tailwindcss.com; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    response.headers["Content-Security-Policy"] = csp
    
    return response

@app.on_event("startup")
async def startup_event():
    """Inicializa serviços quando a aplicação sobe"""
    print("Hawks - Iniciando serviços...")
    # Iniciar o processador de fila automaticamente
    await hawks_scanner.start_queue_processor()
    print("Hawks - Processador de fila iniciado")

@app.on_event("shutdown")
async def shutdown_event():
    """Limpa recursos quando a aplicação é encerrada"""
    print("Hawks - Encerrando serviços...")
    await hawks_scanner.stop_queue_processor()
    print("Hawks - Serviços encerrados")

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=24)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, hawks_config.secret_key, algorithm="HS256")
    return encoded_jwt

def verify_token(token: str):
    try:
        payload = jwt.decode(token, hawks_config.secret_key, algorithms=["HS256"])
        username: str = payload.get("sub")
        if username is None:
            return None
        return username
    except jwt.PyJWTError:
        return None

def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    return verify_token(token)

def check_auth(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials or credentials.credentials != "authenticated":
        return False
    return True

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard")
    return RedirectResponse(url="/login")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    # Get client IP
    client_ip = request.client.host
    
    # Check rate limiting
    if is_rate_limited(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again later."
        )
    
    # Input validation
    if not username or not password or len(username) > 100 or len(password) > 100:
        record_failed_login(client_ip)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Sanitize username
    username = html.escape(username.strip())
    
    # Check credentials (for now using plaintext, but should use hashed passwords)
    if username == hawks_config.admin_username and password == hawks_config.admin_password:
        # Generate secure token
        access_token_expires = timedelta(hours=8)  # Reduced from 24h for security
        access_token = create_access_token(
            data={"sub": username, "iat": time.time()}, 
            expires_delta=access_token_expires
        )
        
        response = RedirectResponse(url="/dashboard", status_code=302)
        response.set_cookie(
            key="access_token", 
            value=access_token, 
            httponly=True, 
            max_age=28800,  # 8 hours
            secure=False,  # False for development (use True in production with HTTPS)
            samesite="lax"  # More permissive for development
        )
        
        # Clear failed attempts on successful login
        if client_ip in login_attempts:
            del login_attempts[client_ip]
        
        return response
    
    # Record failed attempt
    record_failed_login(client_ip)
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    return response

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    settings = get_or_create_settings(db)
    return templates.TemplateResponse("settings.html", {"request": request, "settings": settings, "message": None})

@app.post("/settings")
async def update_settings(
    request: Request,
    db: Session = Depends(get_db),
    chaos_api_key: str = Form(""),
    chaos_enabled: bool = Form(False)
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    settings = get_or_create_settings(db)
    settings.chaos_api_key = chaos_api_key
    settings.chaos_enabled = chaos_enabled
    db.commit()
    
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings,
        "message": "Configurações salvas com sucesso!"
    })

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    
    targets_count = db.query(HawksTargetDB).count()
    templates_count = db.query(HawksTemplateDB).count()
    recent_scans = db.query(HawksScanResult).order_by(HawksScanResult.started_at.desc()).limit(5).all()
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "targets_count": targets_count,
        "templates_count": templates_count,
        "recent_scans": recent_scans
    })

@app.get("/targets", response_class=HTMLResponse)
async def targets_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    
    targets = db.query(HawksTargetDB).all()
    queue_status = hawks_scanner.get_queue_status()
    return templates.TemplateResponse("targets.html", {
        "request": request, 
        "targets": targets,
        "queue_status": queue_status
    })

@app.post("/targets")
async def create_target(request: Request, domain_ip: str = Form(...), db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    
    try:
        # Validate and sanitize domain input
        clean_domain = validate_domain_input(domain_ip)
        
        existing = db.query(HawksTargetDB).filter(HawksTargetDB.domain_ip == clean_domain).first()
        if existing:
            raise HTTPException(status_code=400, detail="Target already exists")
        
        target = HawksTargetDB(domain_ip=clean_domain)
        db.add(target)
        db.commit()
        return RedirectResponse(url="/targets", status_code=302)
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/targets/{target_id}/scan")
async def scan_target(request: Request, target_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    target = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    
    target.scan_status = "running"
    target.last_scan = datetime.utcnow()
    db.commit()
    
    background_tasks.add_task(hawks_scanner.scan_target, target_id, target.domain_ip, db)
    return {"status": "started"}

@app.post("/targets/{target_id}/stop-scan")
async def stop_scan(request: Request, target_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    target = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    
    # Parar o scan no scanner
    hawks_scanner.stop_scan(target_id)
    
    # Atualizar status no banco
    target.scan_status = "stopped"
    db.commit()
    
    return {"status": "stopped"}

@app.delete("/targets/{target_id}")
async def delete_target(request: Request, target_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    target = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    
    db.delete(target)
    db.commit()
    return {"status": "deleted"}

@app.get("/scans", response_class=HTMLResponse)
async def scans_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    
    scan_results = db.query(HawksScanResult).order_by(HawksScanResult.started_at.desc()).all()
    return templates.TemplateResponse("scans.html", {"request": request, "scan_results": scan_results})

@app.get("/templates", response_class=HTMLResponse)
async def templates_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    
    templates_list = db.query(HawksTemplateDB).order_by(HawksTemplateDB.order_index).all()
    return templates.TemplateResponse("templates.html", {"request": request, "templates": templates_list})

@app.get("/nuclei-results", response_class=HTMLResponse)
async def nuclei_results_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    
    # Buscar apenas resultados do nuclei
    nuclei_results = db.query(HawksScanResult).filter(
        HawksScanResult.scan_type == "nuclei",
        HawksScanResult.status == "success"
    ).order_by(HawksScanResult.started_at.desc()).all()
    
    # Buscar informações dos targets
    targets_info = {}
    for result in nuclei_results:
        if result.target_id not in targets_info:
            target = db.query(HawksTargetDB).filter(HawksTargetDB.id == result.target_id).first()
            targets_info[result.target_id] = target.domain_ip if target else "Target removido"
    
    # Processar os resultados para extrair vulnerabilidades
    processed_results = []
    for result in nuclei_results:
        try:
            result_data = json.loads(result.result_data)
            vulnerabilities = result_data.get("results", [])
            
            for vuln in vulnerabilities:
                processed_results.append({
                    "target_name": targets_info[result.target_id],
                    "target_id": result.target_id,
                    "scan_date": result.started_at,
                    "template_id": vuln.get("template-id", "N/A"),
                    "template_name": vuln.get("info", {}).get("name", "N/A"),
                    "severity": vuln.get("info", {}).get("severity", "info"),
                    "matched_at": vuln.get("matched-at", "N/A"),
                    "host": vuln.get("host", "N/A"),
                    "type": vuln.get("type", "N/A"),
                    "vulnerability": vuln
                })
        except (json.JSONDecodeError, KeyError):
            continue
    
    # Ordenar por data mais recente
    processed_results.sort(key=lambda x: x["scan_date"], reverse=True)
    
    return templates.TemplateResponse("nuclei_results.html", {
        "request": request, 
        "nuclei_results": processed_results,
        "total_vulnerabilities": len(processed_results)
    })

@app.post("/templates")
async def create_template(
    request: Request,
    name: str = Form(...),
    content: str = Form(...),
    enabled: bool = Form(False),
    order_index: int = Form(0),
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    
    # Input validation
    if not name or not content:
        raise HTTPException(status_code=400, detail="Name and content are required")
    
    if len(name) > 100 or len(content) > 100000:  # Limit content size
        raise HTTPException(status_code=400, detail="Template name or content too long")
    
    # Sanitize template name
    name = sanitize_filename(name.strip())
    if not name:
        raise HTTPException(status_code=400, detail="Invalid template name")
    
    # Validate YAML content
    if not validate_yaml_content(content):
        raise HTTPException(status_code=400, detail="Invalid or potentially dangerous YAML content")
    
    existing = db.query(HawksTemplateDB).filter(HawksTemplateDB.name == name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Template name already exists")
    
    # Validate order_index
    if order_index < 0 or order_index > 1000:
        order_index = 0
    
    # Salvar template no banco
    template = HawksTemplateDB(
        name=html.escape(name), 
        content=content, 
        enabled=enabled, 
        order_index=order_index
    )
    db.add(template)
    db.commit()
    
    # Salvar template físico na pasta templates/custom
    try:
        custom_dir = os.path.join(os.getcwd(), "templates", "custom")
        os.makedirs(custom_dir, exist_ok=True)
        
        # Use sanitized filename
        safe_filename = f"{name}.yaml"
        template_file_path = os.path.join(custom_dir, safe_filename)
        
        # Ensure we don't overwrite files outside the custom directory
        if not os.path.abspath(template_file_path).startswith(os.path.abspath(custom_dir)):
            raise HTTPException(status_code=400, detail="Invalid file path")
        
        with open(template_file_path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        print(f"Warning: Could not save template file: {e}")
    
    return RedirectResponse(url="/templates", status_code=302)

@app.delete("/templates/{template_id}")
async def delete_template(request: Request, template_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    template = db.query(HawksTemplateDB).filter(HawksTemplateDB.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    # Remover arquivo físico se existir
    try:
        custom_dir = os.path.join(os.getcwd(), "templates", "custom")
        template_file_path = os.path.join(custom_dir, f"{template.name}.yaml")
        if os.path.exists(template_file_path):
            os.remove(template_file_path)
    except Exception as e:
        print(f"Warning: Could not remove template file: {e}")
    
    db.delete(template)
    db.commit()
    return {"status": "deleted"}

@app.post("/templates/upload")
async def upload_template(
    request: Request, 
    file: UploadFile = File(...), 
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    
    # File validation
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    
    # Sanitize filename
    safe_filename = sanitize_filename(file.filename)
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    # File size validation (limit to 10MB)
    MAX_FILE_SIZE = 10 * 1024 * 1024
    if hasattr(file, 'size') and file.size > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")
    
    # Allowed file types
    allowed_extensions = ['.yaml', '.yml', '.zip']
    if not any(safe_filename.lower().endswith(ext) for ext in allowed_extensions):
        raise HTTPException(status_code=400, detail="Only .yaml, .yml, and .zip files are allowed")
    
    custom_dir = os.path.join(os.getcwd(), "templates", "custom")
    os.makedirs(custom_dir, exist_ok=True)
    
    if file.content_type == "application/zip" or safe_filename.endswith('.zip'):
        with tempfile.TemporaryDirectory() as tmpdirname:
            zip_path = f"{tmpdirname}/{safe_filename}"
            
            # Read file content with size limit
            content = await file.read()
            if len(content) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail="File too large")
            
            with open(zip_path, "wb") as buffer:
                buffer.write(content)
            
            try:
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    # Check for zip bombs
                    if len(zip_ref.namelist()) > 100:  # Limit number of files
                        raise HTTPException(status_code=400, detail="Too many files in ZIP")
                    
                    zip_ref.extractall(tmpdirname)
                    
                    for extracted_file in zip_ref.namelist():
                        # Prevent path traversal
                        safe_extracted = sanitize_filename(os.path.basename(extracted_file))
                        if not safe_extracted:
                            continue
                            
                        if safe_extracted.endswith(".yaml") or safe_extracted.endswith(".yml"):
                            try:
                                file_path = os.path.join(tmpdirname, extracted_file)
                                
                                # Ensure file is within temp directory
                                if not os.path.abspath(file_path).startswith(os.path.abspath(tmpdirname)):
                                    continue
                                
                                with open(file_path, 'r', encoding='utf-8') as yaml_file:
                                    yaml_content = yaml_file.read()
                                    
                                    # Validate YAML content
                                    if not validate_yaml_content(yaml_content):
                                        continue
                                    
                                    template_name = safe_extracted.replace('.yaml', '').replace('.yml', '')
                                    template_name = html.escape(template_name)
                                    
                                    # Salvar no banco
                                    existing = db.query(HawksTemplateDB).filter(HawksTemplateDB.name == template_name).first()
                                    if not existing:
                                        db_template = HawksTemplateDB(
                                            name=template_name, 
                                            content=yaml_content, 
                                            enabled=True, 
                                            order_index=0
                                        )
                                        db.add(db_template)
                                        
                                        # Salvar arquivo físico
                                        template_file_path = os.path.join(custom_dir, f"{template_name}.yaml")
                                        
                                        # Verify path is safe
                                        if os.path.abspath(template_file_path).startswith(os.path.abspath(custom_dir)):
                                            with open(template_file_path, 'w', encoding='utf-8') as f:
                                                f.write(yaml_content)
                            except Exception as e:
                                continue
                    db.commit()
            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail="Invalid ZIP file")
    
    elif file.content_type in ["application/x-yaml", "text/yaml"] or safe_filename.endswith(('.yaml', '.yml')):
        contents = await file.read()
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large")
        
        try:
            yaml_content = contents.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Invalid file encoding")
        
        # Validate YAML content
        if not validate_yaml_content(yaml_content):
            raise HTTPException(status_code=400, detail="Invalid or potentially dangerous YAML content")
        
        template_name = safe_filename.replace('.yaml', '').replace('.yml', '')
        template_name = html.escape(template_name)
        
        # Salvar no banco
        existing = db.query(HawksTemplateDB).filter(HawksTemplateDB.name == template_name).first()
        if not existing:
            template = HawksTemplateDB(
                name=template_name, 
                content=yaml_content, 
                enabled=True, 
                order_index=0
            )
            db.add(template)
            db.commit()
            
            # Salvar arquivo físico
            template_file_path = os.path.join(custom_dir, f"{template_name}.yaml")
            
            # Verify path is safe
            if os.path.abspath(template_file_path).startswith(os.path.abspath(custom_dir)):
                with open(template_file_path, 'w', encoding='utf-8') as f:
                    f.write(yaml_content)
    
    else:
        raise HTTPException(status_code=400, detail="File type not supported")
    
    return RedirectResponse(url="/templates", status_code=302)

@app.post("/templates/clone")
async def clone_templates_from_github(
    request: Request, 
    github_url: str = Form(...), 
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    
    try:
        # Validar URL do GitHub
        parsed_url = urlparse(github_url)
        if 'github.com' not in parsed_url.netloc:
            raise HTTPException(status_code=400, detail="URL deve ser do GitHub")
        
        custom_dir = os.path.join(os.getcwd(), "templates", "custom")
        os.makedirs(custom_dir, exist_ok=True)
        
        # Criar diretório temporário para clone
        with tempfile.TemporaryDirectory() as tmpdirname:
            try:
                # Clonar repositório
                repo = git.Repo.clone_from(github_url, tmpdirname)
                
                # Procurar por arquivos YAML no repositório
                yaml_files = []
                for root, dirs, files in os.walk(tmpdirname):
                    for file in files:
                        if file.endswith('.yaml') or file.endswith('.yml'):
                            yaml_files.append(os.path.join(root, file))
                
                # Adicionar templates ao banco
                templates_added = 0
                for yaml_file in yaml_files:
                    try:
                        with open(yaml_file, 'r', encoding='utf-8') as f:
                            content = f.read()
                        
                        # Nome do template baseado no nome do arquivo
                        filename = os.path.basename(yaml_file)
                        template_name = filename.replace('.yaml', '').replace('.yml', '')
                        
                        # Verificar se já existe
                        existing = db.query(HawksTemplateDB).filter(HawksTemplateDB.name == template_name).first()
                        if not existing:
                            template = HawksTemplateDB(
                                name=template_name,
                                content=content,
                                enabled=True,
                                order_index=0
                            )
                            db.add(template)
                            templates_added += 1
                            
                            # Salvar arquivo físico
                            template_file_path = os.path.join(custom_dir, f"{template_name}.yaml")
                            with open(template_file_path, 'w', encoding='utf-8') as f:
                                f.write(content)
                    except Exception as e:
                        continue
                
                db.commit()
                
                if templates_added > 0:
                    return RedirectResponse(url=f"/templates?message={templates_added}_templates_cloned", status_code=302)
                else:
                    return RedirectResponse(url="/templates?error=no_templates_found", status_code=302)
                    
            except git.exc.GitCommandError as e:
                raise HTTPException(status_code=400, detail="Erro ao clonar repositório")
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro: {str(e)}")

@app.get("/api/targets", response_model=List[HawksTarget])
async def api_get_targets(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return db.query(HawksTargetDB).all()

@app.get("/api/scan-results/{target_id}")
async def api_get_scan_results(request: Request, target_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    results = db.query(HawksScanResult).filter(HawksScanResult.target_id == target_id).all()
    return results

@app.get("/targets/{target_id}/dashboard", response_class=HTMLResponse)
async def target_dashboard(request: Request, target_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    
    target = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    
    # Buscar todos os resultados de scan para este target
    scan_results = db.query(HawksScanResult).filter(HawksScanResult.target_id == target_id).order_by(HawksScanResult.started_at.desc()).all()
    
    # Agrupar resultados por tipo de scan
    subfinder_results = [r for r in scan_results if r.scan_type == "subfinder"]
    chaos_results = [r for r in scan_results if r.scan_type == "chaos"]
    httpx_results = [r for r in scan_results if r.scan_type == "httpx"]
    nuclei_results = [r for r in scan_results if r.scan_type == "nuclei"]
    
    # Estatísticas
    total_subdomains = 0
    live_hosts = 0
    vulnerabilities = 0
    
    # Contar subdomínios
    for result in subfinder_results + chaos_results:
        if result.status == "success" and result.result_data:
            try:
                data = json.loads(result.result_data)
                if "subdomains" in data:
                    total_subdomains += len(data["subdomains"])
            except:
                pass
    
    # Contar hosts vivos
    for result in httpx_results:
        if result.status == "success" and result.result_data:
            try:
                data = json.loads(result.result_data)
                if "live_hosts" in data:
                    live_hosts += len(data["live_hosts"])
            except:
                pass
    
    # Contar vulnerabilidades
    for result in nuclei_results:
        if result.status == "success" and result.result_data:
            try:
                data = json.loads(result.result_data)
                if "results" in data:
                    vulnerabilities += len(data["results"])
            except:
                pass
    
    return templates.TemplateResponse("target_dashboard.html", {
        "request": request,
        "target": target,
        "scan_results": scan_results,
        "subfinder_results": subfinder_results,
        "chaos_results": chaos_results,
        "httpx_results": httpx_results,
        "nuclei_results": nuclei_results,
        "total_subdomains": total_subdomains,
        "live_hosts": live_hosts,
        "vulnerabilities": vulnerabilities
    })

@app.get("/api/targets/{target_id}/status")
async def get_target_status(request: Request, target_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    target = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    
    return {
        "id": target.id,
        "domain_ip": target.domain_ip,
        "scan_status": target.scan_status,
        "last_scan": target.last_scan.isoformat() if target.last_scan else None
    }

@app.post("/targets/upload")
async def upload_targets(
    request: Request, 
    file: UploadFile = File(...), 
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    
    # File validation
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    
    safe_filename = sanitize_filename(file.filename)
    if not safe_filename.endswith(('.txt', '.csv', '.list')):
        raise HTTPException(status_code=400, detail="Apenas arquivos .txt, .csv ou .list são aceitos")
    
    # File size validation (limit to 5MB)
    MAX_TARGETS_FILE_SIZE = 5 * 1024 * 1024
    
    try:
        contents = await file.read()
        if len(contents) > MAX_TARGETS_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large (max 5MB)")
        
        try:
            domains_text = contents.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Invalid file encoding")
        
        # Limit number of lines
        lines = domains_text.split('\n')
        if len(lines) > 10000:  # Limit to 10k targets
            raise HTTPException(status_code=400, detail="Too many targets (max 10,000)")
        
        # Processar linhas do arquivo
        domains = []
        for line in lines:
            domain = line.strip()
            if domain and not domain.startswith('#'):  # Ignorar comentários
                try:
                    # Validate and sanitize each domain
                    clean_domain = validate_domain_input(domain)
                    domains.append(clean_domain)
                except ValueError:
                    # Skip invalid domains instead of failing
                    continue
        
        # Limit processed domains
        if len(domains) > 5000:
            domains = domains[:5000]  # Process only first 5000
        
        # Adicionar domínios únicos ao banco
        added_count = 0
        for domain in set(domains):  # Remove duplicatas
            existing = db.query(HawksTargetDB).filter(HawksTargetDB.domain_ip == domain).first()
            if not existing:
                target = HawksTargetDB(domain_ip=domain)
                db.add(target)
                added_count += 1
        
        db.commit()
        return RedirectResponse(url=f"/targets?message={added_count}_targets_added", status_code=302)
        
    except HTTPException:
        raise
    except Exception as e:
        # Don't expose internal error details
        raise HTTPException(status_code=400, detail="Error processing file")

@app.post("/targets/scan-selected")
async def scan_selected_targets(
    request: Request,
    target_ids: List[int] = Form(...),
    db: Session = Depends(get_db)
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Atualizar status dos targets selecionados
    for target_id in target_ids:
        target = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
        if target:
            target.scan_status = "queued"
            target.last_scan = datetime.utcnow()
    db.commit()
    
    # Adicionar à fila de scan
    await hawks_scanner.scan_multiple_targets(target_ids, db)
    
    return {"status": "queued", "targets_count": len(target_ids)}

@app.post("/targets/scan-all")
async def scan_all_targets(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Pegar todos os targets
    targets = db.query(HawksTargetDB).all()
    target_ids = [t.id for t in targets]
    
    # Atualizar status
    for target in targets:
        target.scan_status = "queued"
        target.last_scan = datetime.utcnow()
    db.commit()
    
    # Adicionar à fila de scan
    await hawks_scanner.scan_multiple_targets(target_ids, db)
    
    return {"status": "queued", "targets_count": len(target_ids)}

@app.get("/api/queue-status")
async def get_queue_status(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    return hawks_scanner.get_queue_status()

@app.get("/api/queue-status-detailed")
async def get_detailed_queue_status(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    status = hawks_scanner.get_queue_status()
    
    # Adicionar informações dos jobs de scan
    scan_jobs_info = {}
    for job_id, job_data in hawks_scanner.scan_jobs.items():
        scan_jobs_info[job_id] = {
            "status": job_data.get("status", "unknown"),
            "progress": job_data.get("progress", []),
            "error": job_data.get("error")
        }
    
    status["scan_jobs"] = scan_jobs_info
    return status

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
