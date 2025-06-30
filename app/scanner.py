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
            print(f"SUBFINDER: Executando para target: {target}")
            subfinder_path = self._get_tool_path("subfinder")
            print(f"SUBFINDER: Caminho do executável: {subfinder_path}")
            
            cmd = [subfinder_path, "-d", target, "-o", "-", "-silent"]
            print(f"SUBFINDER: Comando: {' '.join(cmd)}")
            
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            print(f"SUBFINDER: Return code: {process.returncode}")
            print(f"SUBFINDER: Stdout length: {len(stdout.decode())}")
            print(f"SUBFINDER: Stderr: {stderr.decode()[:200]}...")
            
            if process.returncode == 0:
                output_text = stdout.decode().strip()
                if output_text:
                    subdomains = [s.strip() for s in output_text.split('\n') if s.strip()]
                    print(f"SUBFINDER: Encontrados {len(subdomains)} subdomínios")
                    return {"status": "success", "subdomains": subdomains}
                else:
                    print("SUBFINDER: Nenhum subdomínio encontrado")
                    return {"status": "success", "subdomains": []}
            else:
                error_msg = stderr.decode().strip()
                print(f"SUBFINDER: Erro - {error_msg}")
                return {"status": "error", "error": error_msg}
        except Exception as e:
            print(f"SUBFINDER: Exception - {str(e)}")
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
            print(f"HTTPX: Verificando {len(subdomains)} subdomínios...")
            
            # Preparar lista de hosts (sem protocolo para httpx processar)
            hosts_to_check = []
            for subdomain in subdomains:
                subdomain = subdomain.strip()
                if not subdomain:
                    continue
                    
                # Remover protocolo se presente
                if subdomain.startswith(('http://', 'https://')):
                    subdomain = subdomain.split('://', 1)[1]
                
                # Remover path se presente
                if '/' in subdomain:
                    subdomain = subdomain.split('/')[0]
                
                # Adicionar à lista se não vazio
                if subdomain:
                    hosts_to_check.append(subdomain)
            
            if not hosts_to_check:
                return {"status": "error", "error": "No valid hosts to check"}
            
            print(f"HTTPX: Processando {len(hosts_to_check)} hosts...")
            
            # Criar arquivos temporários
            input_file = None
            output_file = None
            
            try:
                # Arquivo de entrada para httpx
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as f:
                    for host in hosts_to_check:
                        f.write(f"{host}\n")
                    input_file = f.name
                
                # Arquivo de saída para httpx
                output_file = tempfile.mktemp(suffix='.txt')
                
                print(f"HTTPX: Input: {input_file}")
                print(f"HTTPX: Output: {output_file}")
                
                httpx_path = self._get_tool_path("httpx")
                print(f"HTTPX: Executável: {httpx_path}")
                
                # Comando simples: apenas httpx -silent -l input -o output
                cmd = [
                    httpx_path,
                    "-silent",
                    "-l", input_file,
                    "-o", output_file
                ]
                
                print(f"HTTPX: Comando: {' '.join(cmd)}")
                
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=os.environ.copy()
                )
                stdout, stderr = await process.communicate()
                
                print(f"HTTPX: Return code: {process.returncode}")
                print(f"HTTPX: Stderr: {stderr.decode()}")
                
                # Ler arquivo de saída
                live_hosts = []
                if os.path.exists(output_file):
                    with open(output_file, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        if content:
                            live_hosts = [line.strip() for line in content.split('\n') if line.strip()]
                    
                    print(f"HTTPX: Encontrados {len(live_hosts)} hosts vivos")
                    
                    if live_hosts:
                        # Retornar tanto os hosts quanto o caminho do arquivo
                        return {
                            "status": "success", 
                            "live_hosts": live_hosts,
                            "output_file": output_file  # Para usar no Nuclei
                        }
                    else:
                        # Se não há hosts vivos, limpar arquivo e retornar erro
                        try:
                            os.unlink(output_file)
                        except:
                            pass
                        return {"status": "error", "error": "No live hosts found"}
                else:
                    print("HTTPX: Arquivo de saída não foi criado")
                    return {"status": "error", "error": "HTTPX output file not created"}
                    
            finally:
                # Limpar arquivo de entrada
                if input_file and os.path.exists(input_file):
                    try:
                        os.unlink(input_file)
                    except:
                        pass
                # NÃO remover output_file aqui - será usado pelo Nuclei
                
        except Exception as e:
            print(f"HTTPX: Exception - {str(e)}")
            return {"status": "error", "error": str(e)}
    
    async def run_nuclei(self, httpx_output_file: str = None, live_hosts: List[str] = None) -> Dict:
        if not httpx_output_file and not live_hosts:
            return {"status": "error", "error": "No hosts to scan"}
        
        try:
            print(f"NUCLEI: Iniciando scan...")
            
            # Usar arquivo do HTTPX se disponível, senão criar arquivo temporário
            hosts_file = None
            cleanup_file = False
            
            if httpx_output_file and os.path.exists(httpx_output_file):
                hosts_file = httpx_output_file
                print(f"NUCLEI: Usando arquivo do HTTPX: {hosts_file}")
            elif live_hosts:
                # Criar arquivo temporário se não temos arquivo do HTTPX
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as f:
                    for host in live_hosts:
                        f.write(f"{host}\n")
                    hosts_file = f.name
                    cleanup_file = True
                print(f"NUCLEI: Criado arquivo temporário: {hosts_file}")
            else:
                return {"status": "error", "error": "No valid hosts file or list"}
            
            # Verificar se arquivo existe e tem conteúdo
            if not os.path.exists(hosts_file):
                return {"status": "error", "error": "Hosts file not found"}
            
            with open(hosts_file, 'r') as f:
                hosts_content = f.read().strip()
                if not hosts_content:
                    if cleanup_file:
                        os.unlink(hosts_file)
                    return {"status": "error", "error": "Hosts file is empty"}
                hosts_count = len([line for line in hosts_content.split('\n') if line.strip()])
                print(f"NUCLEI: Arquivo contém {hosts_count} hosts")
            
            nuclei_path = self._get_tool_path("nuclei")
            print(f"NUCLEI: Executável: {nuclei_path}")
            
            # Comando básico do nuclei
            cmd = [nuclei_path, "-l", hosts_file, "-json", "-silent", "-nc"]
            
            # Usar templates custom se disponíveis na pasta templates/custom
            custom_templates_dir = os.path.join(os.getcwd(), "templates", "custom")
            if os.path.exists(custom_templates_dir) and os.listdir(custom_templates_dir):
                yaml_files = [f for f in os.listdir(custom_templates_dir) if f.endswith('.yaml') or f.endswith('.yml')]
                if yaml_files:
                    cmd.extend(["-t", custom_templates_dir])
                    print(f"NUCLEI: Usando {len(yaml_files)} templates custom")
                else:
                    # Usar templates padrão se não há custom
                    cmd.extend(["-t", "~/.nuclei-templates/"])
                    print("NUCLEI: Usando templates padrão")
            else:
                # Usar templates padrão se pasta custom não existe ou está vazia
                cmd.extend(["-t", "~/.nuclei-templates/"])
                print("NUCLEI: Usando templates padrão")
            
            print(f"NUCLEI: Comando: {' '.join(cmd)}")
            
            process = await asyncio.create_subprocess_exec(
                *cmd, 
                stdout=asyncio.subprocess.PIPE, 
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy()
            )
            stdout, stderr = await process.communicate()
            
            print(f"NUCLEI: Return code: {process.returncode}")
            print(f"NUCLEI: Stderr: {stderr.decode()}")
            
            # Limpar arquivo temporário se foi criado por nós
            if cleanup_file and os.path.exists(hosts_file):
                try:
                    os.unlink(hosts_file)
                except:
                    pass
            
            # Nuclei pode retornar 0 (sucesso) ou 1 (quando não há resultados)
            if process.returncode in [0, 1]:
                results = []
                output_text = stdout.decode().strip()
                
                if output_text:
                    output_lines = output_text.split('\n')
                    print(f"NUCLEI: Processando {len(output_lines)} linhas de saída...")
                    
                    for line in output_lines:
                        line = line.strip()
                        if line and line.startswith('{'):
                            try:
                                result = json.loads(line)
                                results.append(result)
                            except json.JSONDecodeError:
                                continue
                
                print(f"NUCLEI: Encontradas {len(results)} vulnerabilidades")
                return {"status": "success", "results": results}
            else:
                error_msg = stderr.decode().strip()
                if not error_msg:
                    error_msg = f"Nuclei failed with return code {process.returncode}"
                print(f"NUCLEI: Erro - {error_msg}")
                return {"status": "error", "error": error_msg}
                
        except Exception as e:
            print(f"NUCLEI: Exception - {str(e)}")
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
                # Usar arquivo de saída do HTTPX diretamente
                httpx_output_file = httpx_result.get("output_file")
                nuclei_result = await self.run_nuclei(httpx_output_file=httpx_output_file, live_hosts=httpx_result.get("live_hosts", []))
                scan_result = HawksScanResult(
                    target_id=target_id,
                    scan_type="nuclei",
                    status="success" if nuclei_result["status"] == "success" else "error",
                    result_data=json.dumps(nuclei_result),
                    error_msg=nuclei_result.get("error")
                )
                db.add(scan_result)
                db.commit()
                
                # Limpar arquivo temporário do HTTPX após uso do Nuclei
                if httpx_output_file and os.path.exists(httpx_output_file):
                    try:
                        os.unlink(httpx_output_file)
                    except:
                        pass
            
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
