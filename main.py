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
    return templates.TemplateResponse("targets.html", {"request": request, "targets": targets})

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
    
    template = HawksTemplateDB(name=name, content=content, enabled=enabled, order_index=order_index)
    db.add(template)
    db.commit()
    return RedirectResponse(url="/templates", status_code=302)

@app.delete("/templates/{template_id}")
async def delete_template(request: Request, template_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    template = db.query(HawksTemplateDB).filter(HawksTemplateDB.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
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
    
    if file.content_type == "application/zip":
        with tempfile.TemporaryDirectory() as tmpdirname:
            zip_path = f"{tmpdirname}/{file.filename}"
            with open(zip_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(tmpdirname)
                
                for extracted_file in zip_ref.namelist():
                    if extracted_file.endswith(".yaml") or extracted_file.endswith(".yml"):
                        with open(f"{tmpdirname}/{extracted_file}", 'r') as yaml_file:
                            yaml_content = yaml_file.read()
                            template_name = extracted_file[:-5]  # remove .yaml or .yml
                            db_template = HawksTemplateDB(
                                name=template_name, 
                                content=yaml_content, 
                                enabled=True, 
                                order_index=0
                            )
                            db.add(db_template)
                db.commit()
    
    elif file.content_type == "application/x-yaml" or file.content_type == "text/yaml":
        contents = await file.read()
        yaml_content = contents.decode("utf-8")
        
        template = HawksTemplateDB(
            name=file.filename, 
            content=yaml_content, 
            enabled=True, 
            order_index=0
        )
        db.add(template)
        db.commit()
    
    else:
        raise HTTPException(status_code=400, detail="File type not supported")
    
    return RedirectResponse(url="/templates", status_code=302)

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
