from fastapi import FastAPI, Request, Depends, HTTPException, Form, BackgroundTasks, status, UploadFile, File
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
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
from urllib.parse import urlparse
from typing import List, Optional

from app.database import get_db, init_db, HawksTarget as HawksTargetDB, HawksTemplate as HawksTemplateDB, HawksScanResult
from app.schemas import HawksTargetCreate, HawksTarget, HawksTemplateCreate, HawksTemplate, HawksLoginRequest
from app.scanner import hawks_scanner
from app.config import hawks_config

app = FastAPI(title="Hawks", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
security = HTTPBearer(auto_error=False)

init_db()

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
async def login(username: str = Form(...), password: str = Form(...)):
    if username == hawks_config.admin_username and password == hawks_config.admin_password:
        access_token_expires = timedelta(hours=24)
        access_token = create_access_token(
            data={"sub": username}, expires_delta=access_token_expires
        )
        response = RedirectResponse(url="/dashboard", status_code=302)
        response.set_cookie(
            key="access_token", 
            value=access_token, 
            httponly=True, 
            max_age=86400,
            secure=False
        )
        return response
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie("access_token")
    return response

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
    
    existing = db.query(HawksTargetDB).filter(HawksTargetDB.domain_ip == domain_ip).first()
    if existing:
        raise HTTPException(status_code=400, detail="Target already exists")
    
    target = HawksTargetDB(domain_ip=domain_ip)
    db.add(target)
    db.commit()
    return RedirectResponse(url="/targets", status_code=302)

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
    
    existing = db.query(HawksTemplateDB).filter(HawksTemplateDB.name == name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Template name already exists")
    
    # Salvar template no banco
    template = HawksTemplateDB(name=name, content=content, enabled=enabled, order_index=order_index)
    db.add(template)
    db.commit()
    
    # Salvar template físico na pasta templates/custom
    try:
        custom_dir = os.path.join(os.getcwd(), "templates", "custom")
        os.makedirs(custom_dir, exist_ok=True)
        template_file_path = os.path.join(custom_dir, f"{name}.yaml")
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
    
    custom_dir = os.path.join(os.getcwd(), "templates", "custom")
    os.makedirs(custom_dir, exist_ok=True)
    
    if file.content_type == "application/zip" or file.filename.endswith('.zip'):
        with tempfile.TemporaryDirectory() as tmpdirname:
            zip_path = f"{tmpdirname}/{file.filename}"
            with open(zip_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(tmpdirname)
                
                for extracted_file in zip_ref.namelist():
                    if extracted_file.endswith(".yaml") or extracted_file.endswith(".yml"):
                        try:
                            with open(f"{tmpdirname}/{extracted_file}", 'r', encoding='utf-8') as yaml_file:
                                yaml_content = yaml_file.read()
                                template_name = os.path.basename(extracted_file).replace('.yaml', '').replace('.yml', '')
                                
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
                                    with open(template_file_path, 'w', encoding='utf-8') as f:
                                        f.write(yaml_content)
                        except Exception as e:
                            continue
                db.commit()
    
    elif file.content_type in ["application/x-yaml", "text/yaml"] or file.filename.endswith(('.yaml', '.yml')):
        contents = await file.read()
        yaml_content = contents.decode("utf-8")
        template_name = file.filename.replace('.yaml', '').replace('.yml', '')
        
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
    
    if not file.filename.endswith(('.txt', '.csv', '.list')):
        raise HTTPException(status_code=400, detail="Apenas arquivos .txt, .csv ou .list são aceitos")
    
    try:
        contents = await file.read()
        domains_text = contents.decode("utf-8")
        
        # Processar linhas do arquivo
        domains = []
        for line in domains_text.split('\n'):
            domain = line.strip()
            if domain and not domain.startswith('#'):  # Ignorar comentários
                # Remover http/https se presente
                if domain.startswith(('http://', 'https://')):
                    domain = domain.split('://', 1)[1]
                if '/' in domain:
                    domain = domain.split('/')[0]
                domains.append(domain)
        
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
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao processar arquivo: {str(e)}")

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
