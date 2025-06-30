from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os
import urllib.parse
from .config import hawks_config

engine = create_engine(hawks_config.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class HawksTarget(Base):
    __tablename__ = "targets"
    
    id = Column(Integer, primary_key=True, index=True)
    domain_ip = Column(String, index=True, nullable=False)
    scan_status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    last_scan = Column(DateTime, nullable=True)

class HawksTemplate(Base):
    __tablename__ = "templates"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)
    order_index = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class HawksScanResult(Base):
    __tablename__ = "scan_results"
    
    id = Column(Integer, primary_key=True, index=True)
    target_id = Column(Integer, nullable=False)
    scan_type = Column(String, nullable=False)
    status = Column(String, default="pending")
    result_data = Column(Text, nullable=True)
    error_msg = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    # Garantir que o diretório do banco de dados existe
    if hawks_config.database_url.startswith('sqlite:///'):
        # Extrair o caminho do arquivo do database_url
        db_path = hawks_config.database_url.replace('sqlite:///', '')
        
        # Se for um caminho relativo, resolve para o diretório atual
        if not os.path.isabs(db_path):
            db_path = os.path.abspath(db_path)
        
        # Criar o diretório pai se não existir
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, mode=0o755, exist_ok=True)
            print(f"Created database directory: {db_dir}")
        
        print(f"Initializing database at: {db_path}")
    
    # Criar todas as tabelas
    Base.metadata.create_all(bind=engine)
    print("Database initialized successfully!")
