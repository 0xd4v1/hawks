import asyncio
import subprocess
import json
import tempfile
import os
import shutil
from typing import List, Dict, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from .database import HawksScanResult, HawksTemplate
from .config import hawks_config

class HawksScanner:
    def __init__(self):
        self.scan_jobs = {}
        self.stop_flags = {}
        self.tools_path = self._get_tools_path()
        self.scan_queue = asyncio.Queue()
        self.active_scans = {}
        self.scan_lock = asyncio.Lock()
        self.queue_processor_task = None
    
    async def start_queue_processor(self):
        """Inicia o processador de fila de scans"""
        if self.queue_processor_task is None:
            self.queue_processor_task = asyncio.create_task(self._queue_processor())
    
    async def _queue_processor(self):
        """Processador contínuo da fila de scans"""
        while True:
            try:
                # Verificar se há capacidade para novos scans
                async with self.scan_lock:
                    has_capacity = len(self.active_scans) < hawks_config.max_concurrent_scans
                
                if has_capacity and not self.scan_queue.empty():
                    try:
                        scan_data = await asyncio.wait_for(self.scan_queue.get(), timeout=0.1)
                        # Atualizar status para running antes de executar
                        target_id = scan_data[0]
                        scan_id = f"scan_{target_id}"
                        if scan_id in self.scan_jobs:
                            self.scan_jobs[scan_id]["status"] = "running"
                        
                        # Executar scan
                        asyncio.create_task(self._execute_scan(scan_data))
                    except asyncio.TimeoutError:
                        pass
                else:
                    # Se não há capacidade ou fila vazia, aguardar mais tempo
                    await asyncio.sleep(3)
                    
            except Exception as e:
                print(f"Erro no processador de fila: {e}")
                await asyncio.sleep(2)
    
    async def _execute_scan(self, scan_data):
        """Executa um scan da fila"""
        target_id, target, db = scan_data
        scan_id = f"scan_{target_id}"
        
        async with self.scan_lock:
            self.active_scans[scan_id] = True
        
        try:
            await self._run_scan_pipeline(target_id, target, db)
        finally:
            async with self.scan_lock:
                if scan_id in self.active_scans:
                    del self.active_scans[scan_id]
    
    def _get_tools_path(self) -> str:
        home_go_bin = os.path.expanduser("~/go/bin")
        if os.path.exists(home_go_bin):
            return home_go_bin
        return ""
    
    def _get_tool_path(self, tool_name: str) -> str:
        if self.tools_path:
            tool_path = os.path.join(self.tools_path, tool_name)
            if os.path.exists(tool_path):
                return tool_path
        
        system_path = shutil.which(tool_name)
        if system_path:
            return system_path
        
        return tool_name
    
    async def run_subfinder(self, target: str) -> Dict:
        try:
            subfinder_path = self._get_tool_path("subfinder")
            cmd = [subfinder_path, "-d", target, "-o", "-", "-silent"]
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                subdomains = stdout.decode().strip().split('\n')
                return {"status": "success", "subdomains": [s for s in subdomains if s]}
            else:
                return {"status": "error", "error": stderr.decode()}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def run_chaos(self, target: str) -> Dict:
        if not hawks_config.chaos_api_key:
            return {"status": "skipped", "reason": "No API key"}
        
        try:
            chaos_path = self._get_tool_path("chaos")
            cmd = [chaos_path, "-d", target, "-o", "-", "-silent"]
            env = os.environ.copy()
            env["CHAOS_API_KEY"] = hawks_config.chaos_api_key
            
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                subdomains = stdout.decode().strip().split('\n')
                return {"status": "success", "subdomains": [s for s in subdomains if s]}
            else:
                return {"status": "error", "error": stderr.decode()}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def run_httpx(self, subdomains: List[str]) -> Dict:
        if not subdomains:
            return {"status": "error", "error": "No subdomains to check"}
        
        try:
            # Preparar lista de URLs válidas
            urls_to_check = []
            for subdomain in subdomains:
                subdomain = subdomain.strip()
                if not subdomain:
                    continue
                    
                # Se já tem protocolo, usar como está
                if subdomain.startswith(('http://', 'https://')):
                    urls_to_check.append(subdomain)
                else:
                    # Adicionar ambos os protocolos
                    urls_to_check.append(f"https://{subdomain}")
                    urls_to_check.append(f"http://{subdomain}")
            
            if not urls_to_check:
                return {"status": "error", "error": "No valid URLs to check"}
            
            # Escrever URLs no arquivo temporário
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as f:
                for url in urls_to_check:
                    f.write(f"{url}\n")
                temp_file = f.name
            
            httpx_path = self._get_tool_path("httpx")
            cmd = [
                httpx_path, 
                "-l", temp_file, 
                "-silent", 
                "-no-color",
                "-timeout", "15",
                "-retries", "1",
                "-status-code",
                "-follow-redirects",
                "-mc", "200,201,202,204,301,302,303,307,308,401,403,405,429,500,502,503"
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            # Limpar arquivo temporário
            try:
                os.unlink(temp_file)
            except:
                pass
            
            if process.returncode == 0 or process.returncode == 1:  # 1 pode ser para alguns hosts não encontrados
                live_hosts = []
                output_lines = stdout.decode().strip().split('\n')
                
                for line in output_lines:
                    line = line.strip()
                    if line and not line.startswith('[') and '://' in line:
                        # Extrair URL da linha (pode ter status code no final)
                        parts = line.split()
                        if parts:
                            url = parts[0]
                            if url.startswith(('http://', 'https://')):
                                live_hosts.append(url)
                
                # Remover duplicatas mantendo ordem
                unique_hosts = []
                seen = set()
                for host in live_hosts:
                    if host not in seen:
                        unique_hosts.append(host)
                        seen.add(host)
                
                return {"status": "success", "live_hosts": unique_hosts}
            else:
                error_msg = stderr.decode().strip()
                if not error_msg:
                    error_msg = f"HTTPX failed with return code {process.returncode}"
                return {"status": "error", "error": error_msg}
                
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def run_nuclei(self, hosts: List[str], templates: List[str] = None) -> Dict:
        if not hosts:
            return {"status": "error", "error": "No hosts to scan"}
        
        try:
            # Escrever hosts no arquivo temporário
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as f:
                for host in hosts:
                    f.write(f"{host}\n")
                hosts_file = f.name
            
            nuclei_path = self._get_tool_path("nuclei")
            cmd = [nuclei_path, "-l", hosts_file, "-json", "-silent", "-no-color", "-no-meta"]
            
            # Usar templates custom se disponíveis na pasta templates/custom
            custom_templates_dir = os.path.join(os.getcwd(), "templates", "custom")
            if os.path.exists(custom_templates_dir) and os.listdir(custom_templates_dir):
                yaml_files = [f for f in os.listdir(custom_templates_dir) if f.endswith('.yaml') or f.endswith('.yml')]
                if yaml_files:
                    cmd.extend(["-t", custom_templates_dir])
                else:
                    # Usar templates padrão se não há custom
                    cmd.extend(["-t", "~/.nuclei-templates/"])
            else:
                # Usar templates padrão se pasta custom não existe ou está vazia
                cmd.extend(["-t", "~/.nuclei-templates/"])
            
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            # Limpar arquivo temporário
            try:
                os.unlink(hosts_file)
            except:
                pass
            
            if process.returncode == 0 or process.returncode == 1:  # 1 pode ser quando não há resultados
                results = []
                output_lines = stdout.decode().strip().split('\n')
                
                for line in output_lines:
                    line = line.strip()
                    if line and line.startswith('{'):
                        try:
                            result = json.loads(line)
                            results.append(result)
                        except json.JSONDecodeError:
                            continue
                
                return {"status": "success", "results": results}
            else:
                error_msg = stderr.decode().strip()
                if not error_msg:
                    error_msg = f"Nuclei failed with return code {process.returncode}"
                return {"status": "error", "error": error_msg}
                
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def scan_target(self, target_id: int, target: str, db: Session):
        """Adiciona scan à fila ou executa imediatamente se há capacidade"""
        scan_id = f"scan_{target_id}"
        self.scan_jobs[scan_id] = {"status": "queued", "progress": []}
        self.stop_flags[scan_id] = False
        
        # Verificar se pode executar imediatamente
        async with self.scan_lock:
            can_run_now = len(self.active_scans) < hawks_config.max_concurrent_scans
        
        if can_run_now:
            # Executar imediatamente
            async with self.scan_lock:
                self.active_scans[scan_id] = True
            
            self.scan_jobs[scan_id]["status"] = "running"
            
            # Atualizar status no banco
            from .database import HawksTarget as HawksTargetDB
            target_obj = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
            if target_obj:
                target_obj.scan_status = "running"
                db.commit()
            
            # Executar scan diretamente
            asyncio.create_task(self._run_scan_pipeline(target_id, target, db))
        else:
            # Adicionar à fila se não pode executar agora
            # Iniciar processador se ainda não foi iniciado
            await self.start_queue_processor()
            
            # Adicionar à fila
            await self.scan_queue.put((target_id, target, db))
            
            # Atualizar status no banco
            from .database import HawksTarget as HawksTargetDB
            target_obj = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
            if target_obj:
                target_obj.scan_status = "queued"
                db.commit()
    
    async def scan_multiple_targets(self, target_ids: List[int], db: Session):
        """Adiciona múltiplos targets para scan, executando imediatamente os que puder"""
        from .database import HawksTarget as HawksTargetDB
        
        for target_id in target_ids:
            target_obj = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
            if target_obj:
                await self.scan_target(target_id, target_obj.domain_ip, db)
    
    def get_queue_status(self):
        """Retorna status da fila de scans"""
        return {
            "active_scans": len(self.active_scans),
            "queued_scans": self.scan_queue.qsize(),
            "max_concurrent": hawks_config.max_concurrent_scans,
            "scan_threads": hawks_config.scan_threads
        }
    
    async def _run_scan_pipeline(self, target_id: int, target: str, db: Session):
        scan_id = f"scan_{target_id}"
        self.scan_jobs[scan_id] = {"status": "running", "progress": []}
        
        try:
            # Subfinder
            if self._should_stop(scan_id):
                return
                
            subfinder_result = await self.run_subfinder(target)
            scan_result = HawksScanResult(
                target_id=target_id,
                scan_type="subfinder",
                status="success" if subfinder_result["status"] == "success" else "error",
                result_data=json.dumps(subfinder_result),
                error_msg=subfinder_result.get("error")
            )
            db.add(scan_result)
            db.commit()
            
            if self._should_stop(scan_id):
                return
            
            all_subdomains = subfinder_result.get("subdomains", [])
            
            # Chaos (se API key disponível)
            if hawks_config.chaos_api_key and not self._should_stop(scan_id):
                chaos_result = await self.run_chaos(target)
                scan_result = HawksScanResult(
                    target_id=target_id,
                    scan_type="chaos",
                    status="success" if chaos_result["status"] == "success" else "error",
                    result_data=json.dumps(chaos_result),
                    error_msg=chaos_result.get("error")
                )
                db.add(scan_result)
                db.commit()
                
                if chaos_result["status"] == "success":
                    all_subdomains.extend(chaos_result.get("subdomains", []))
                    all_subdomains = list(set(all_subdomains))
            
            if self._should_stop(scan_id):
                return
                
            # HTTPX
            httpx_result = await self.run_httpx(all_subdomains)
            scan_result = HawksScanResult(
                target_id=target_id,
                scan_type="httpx",
                status="success" if httpx_result["status"] == "success" else "error",
                result_data=json.dumps(httpx_result),
                error_msg=httpx_result.get("error")
            )
            db.add(scan_result)
            db.commit()
            
            if self._should_stop(scan_id):
                return
            
            # Nuclei - usar templates custom salvos fisicamente
            if httpx_result["status"] == "success" and not self._should_stop(scan_id):
                nuclei_result = await self.run_nuclei(httpx_result.get("live_hosts", []))
                scan_result = HawksScanResult(
                    target_id=target_id,
                    scan_type="nuclei",
                    status="success" if nuclei_result["status"] == "success" else "error",
                    result_data=json.dumps(nuclei_result),
                    error_msg=nuclei_result.get("error")
                )
                db.add(scan_result)
                db.commit()
            
            # Finalizar scan
            if self._should_stop(scan_id):
                self.scan_jobs[scan_id]["status"] = "stopped"
            else:
                self.scan_jobs[scan_id]["status"] = "completed"
                
            # Atualizar status do target no banco
            from .database import HawksTarget as HawksTargetDB
            target_obj = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
            if target_obj:
                target_obj.scan_status = self.scan_jobs[scan_id]["status"]
                db.commit()
            
            # Remover da lista de scans ativos
            async with self.scan_lock:
                if scan_id in self.active_scans:
                    del self.active_scans[scan_id]
            
        except Exception as e:
            self.scan_jobs[scan_id]["status"] = "error"
            self.scan_jobs[scan_id]["error"] = str(e)
            
            # Atualizar status do target no banco
            from .database import HawksTarget as HawksTargetDB
            target_obj = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
            if target_obj:
                target_obj.scan_status = "error"
                db.commit()
            
            # Remover da lista de scans ativos
            async with self.scan_lock:
                if scan_id in self.active_scans:
                    del self.active_scans[scan_id]

    def stop_scan(self, target_id: int):
        """Para um scan em execução"""
        scan_id = f"scan_{target_id}"
        self.stop_flags[scan_id] = True
        if scan_id in self.scan_jobs:
            self.scan_jobs[scan_id]["status"] = "stopped"

    def _should_stop(self, scan_id: str) -> bool:
        """Verifica se o scan deve ser parado"""
        return self.stop_flags.get(scan_id, False)

hawks_scanner = HawksScanner()
