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
        # Estado simplificado da fila
        self.scan_jobs = {}  # {scan_id: {"status": str, "progress": list, "error": str}}
        self.stop_flags = {}  # {scan_id: bool}
        
        # Sistema de fila simplificado
        self.scan_queue = asyncio.Queue()
        self.active_scans = set()  # Set simples ao invés de dict
        self.processor_running = False
        self.processor_task = None
        
        # Configurações
        self.tools_path = self._get_tools_path()
        self.max_concurrent = hawks_config.max_concurrent_scans

    async def start_queue_processor(self):
        """Inicia o processador de fila"""
        if not self.processor_running:
            self.processor_running = True
            self.processor_task = asyncio.create_task(self._queue_processor_loop())
            print("🚀 Hawks Scanner - Processador de fila iniciado")

    async def stop_queue_processor(self):
        """Para o processador de fila"""
        self.processor_running = False
        if self.processor_task:
            self.processor_task.cancel()
            try:
                await self.processor_task
            except asyncio.CancelledError:
                pass
            self.processor_task = None
        print("🛑 Hawks Scanner - Processador de fila parado")

    async def _queue_processor_loop(self):
        """Loop principal do processador de fila - versão simplificada e robusta"""
        print("⚡ Hawks Scanner - Processador de fila ativo")
        
        while self.processor_running:
            try:
                # Status rápido para debug
                queue_size = self.scan_queue.qsize()
                active_count = len(self.active_scans)
                
                # Log apenas quando há atividade
                if queue_size > 0 or active_count > 0:
                    print(f"📊 Fila: {queue_size} aguardando | {active_count}/{self.max_concurrent} ativos")

                # Verificar se pode processar mais scans
                if active_count < self.max_concurrent and queue_size > 0:
                    try:
                        # Pegar próximo scan da fila (timeout curto)
                        scan_data = await asyncio.wait_for(
                            self.scan_queue.get(), 
                            timeout=0.5
                        )
                        
                        # Processar scan imediatamente
                        asyncio.create_task(self._execute_queued_scan(scan_data))
                        
                    except asyncio.TimeoutError:
                        # Timeout esperado, continuar loop
                        pass
                else:
                    # Aguardar antes de verificar novamente
                    await asyncio.sleep(2)
                    
            except asyncio.CancelledError:
                print("❌ Processador de fila cancelado")
                break
            except Exception as e:
                print(f"⚠️ Erro no processador de fila: {e}")
                # Aguardar antes de tentar novamente
                await asyncio.sleep(5)

    async def _execute_queued_scan(self, scan_data):
        """Executa um scan vindo da fila - método simplificado"""
        target_id, target, db_session_data = scan_data
        scan_id = f"scan_{target_id}"
        
        # Adicionar aos scans ativos
        self.active_scans.add(scan_id)
        
        # Atualizar status
        if scan_id in self.scan_jobs:
            self.scan_jobs[scan_id]["status"] = "running"
        
        print(f"🔍 Iniciando scan: Target {target_id} ({target})")
        
        try:
            # Executar pipeline de scan
            await self._run_scan_pipeline(target_id, target, db_session_data)
            print(f"✅ Scan concluído: Target {target_id}")
            
        except Exception as e:
            print(f"❌ Erro no scan {target_id}: {e}")
            if scan_id in self.scan_jobs:
                self.scan_jobs[scan_id]["status"] = "error"
                self.scan_jobs[scan_id]["error"] = str(e)
        finally:
            # Sempre remover dos scans ativos
            self.active_scans.discard(scan_id)

    async def scan_target(self, target_id: int, target: str, db: Session):
        """Interface principal para iniciar scan de um target"""
        scan_id = f"scan_{target_id}"
        
        # Inicializar job
        self.scan_jobs[scan_id] = {
            "status": "queued", 
            "progress": [], 
            "error": None
        }
        self.stop_flags[scan_id] = False
        
        print(f"📨 Adicionando à fila: Target {target_id} ({target})")
        
        # Sempre adicionar à fila para processamento uniforme
        # Serializar dados necessários do banco para evitar problemas de sessão
        db_data = self._serialize_db_session(db)
        await self.scan_queue.put((target_id, target, db_data))
        
        # Atualizar status no banco
        self._update_target_status(target_id, "queued", db)

    async def scan_multiple_targets(self, target_ids: List[int], db: Session):
        """Adiciona múltiplos targets à fila"""
        from .database import HawksTarget as HawksTargetDB
        
        for target_id in target_ids:
            target_obj = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
            if target_obj:
                await self.scan_target(target_id, target_obj.domain_ip, db)

    def _serialize_db_session(self, db: Session):
        """Serializa dados necessários da sessão do banco"""
        return {
            "database_url": hawks_config.database_url
        }

    def _update_target_status(self, target_id: int, status: str, db: Session):
        """Atualiza status do target no banco de forma segura"""
        try:
            from .database import HawksTarget as HawksTargetDB
            target_obj = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
            if target_obj:
                target_obj.scan_status = status
                target_obj.last_scan = datetime.utcnow()
                db.commit()
        except Exception as e:
            print(f"⚠️ Erro ao atualizar status do target {target_id}: {e}")

    def get_queue_status(self):
        """Retorna status atual da fila"""
        return {
            "active_scans": len(self.active_scans),
            "queued_scans": self.scan_queue.qsize(),
            "max_concurrent": self.max_concurrent,
            "scan_threads": hawks_config.scan_threads,
            "queue_processor_running": self.processor_running,
            "active_scan_ids": list(self.active_scans),
            "scan_jobs_count": len(self.scan_jobs)
        }

    def stop_scan(self, target_id: int):
        """Para um scan específico"""
        scan_id = f"scan_{target_id}"
        self.stop_flags[scan_id] = True
        if scan_id in self.scan_jobs:
            self.scan_jobs[scan_id]["status"] = "stopped"
        print(f"🛑 Parando scan: {scan_id}")

    def _should_stop(self, scan_id: str) -> bool:
        """Verifica se um scan deve ser parado"""
        return self.stop_flags.get(scan_id, False)

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
            
            # Criar arquivo temporário para salvar subdomínios
            subfinder_output_file = tempfile.mktemp(suffix='_subfinder.txt')
            
            cmd = [subfinder_path, "-d", target, "-o", subfinder_output_file, "-silent"]
            print(f"SUBFINDER: Comando: {' '.join(cmd)}")
            print(f"SUBFINDER: Arquivo de saída: {subfinder_output_file}")
            
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            print(f"SUBFINDER: Return code: {process.returncode}")
            print(f"SUBFINDER: Stderr: {stderr.decode()[:200]}...")
            
            if process.returncode == 0:
                # Verificar se arquivo foi criado e ler conteúdo
                if os.path.exists(subfinder_output_file):
                    with open(subfinder_output_file, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        if content:
                            subdomains = [s.strip() for s in content.split('\n') if s.strip()]
                            print(f"SUBFINDER: Encontrados {len(subdomains)} subdomínios")
                            return {
                                "status": "success", 
                                "subdomains": subdomains,
                                "output_file": subfinder_output_file  # Para usar com cat | httpx
                            }
                        else:
                            # Arquivo vazio, limpar
                            try:
                                os.unlink(subfinder_output_file)
                            except:
                                pass
                            print("SUBFINDER: Nenhum subdomínio encontrado")
                            return {"status": "success", "subdomains": []}
                else:
                    print("SUBFINDER: Arquivo de saída não foi criado")
                    return {"status": "error", "error": "Subfinder output file not created"}
            else:
                # Limpar arquivo em caso de erro
                if os.path.exists(subfinder_output_file):
                    try:
                        os.unlink(subfinder_output_file)
                    except:
                        pass
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
    
    async def run_httpx(self, subdomains: List[str] = None, subfinder_file: str = None) -> Dict:
        # Priorizar arquivo do subfinder se disponível
        if subfinder_file and os.path.exists(subfinder_file):
            return await self._run_httpx_from_file(subfinder_file)
        elif subdomains:
            return await self._run_httpx_from_list(subdomains)
        else:
            return {"status": "error", "error": "No subdomains or file provided"}
    
    async def _run_httpx_from_file(self, subfinder_file: str) -> Dict:
        """Executa HTTPX usando cat arquivo | httpx -silent -o arquivo"""
        try:
            print(f"HTTPX: Processando arquivo do subfinder: {subfinder_file}")
            
            # Verificar se arquivo existe e tem conteúdo
            if not os.path.exists(subfinder_file):
                return {"status": "error", "error": "Subfinder file not found"}
            
            with open(subfinder_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    return {"status": "error", "error": "Subfinder file is empty"}
                
                subdomains_count = len([line for line in content.split('\n') if line.strip()])
                print(f"HTTPX: Arquivo contém {subdomains_count} subdomínios")
            
            httpx_path = self._get_tool_path("httpx")
            print(f"HTTPX: Executável: {httpx_path}")
            
            # Verificar se httpx existe
            if not os.path.exists(httpx_path) and httpx_path == "httpx":
                import shutil
                httpx_path = shutil.which("httpx")
                if not httpx_path:
                    return {"status": "error", "error": "HTTPX not found in PATH"}
            
            # Criar arquivo de saída para o HTTPX
            httpx_output_file = tempfile.mktemp(suffix='_httpx.txt')
            
            # Comando: cat subfinder_file | httpx -silent -o httpx_output
            cmd = f"cat {subfinder_file} | {httpx_path} -silent -o {httpx_output_file}"
            print(f"HTTPX: Comando: {cmd}")
            
            # Executar via shell para usar pipe
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy()
            )
            
            stdout, stderr = await process.communicate()
            
            print(f"HTTPX: Return code: {process.returncode}")
            print(f"HTTPX: Stdout: {stdout.decode()[:200]}...")
            print(f"HTTPX: Stderr: {stderr.decode()[:200]}...")
            
            if process.returncode == 0:
                # Verificar se arquivo de saída foi criado
                if os.path.exists(httpx_output_file):
                    with open(httpx_output_file, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        if content:
                            live_hosts = [line.strip() for line in content.split('\n') if line.strip()]
                            print(f"HTTPX: Encontrados {len(live_hosts)} hosts vivos (via arquivo)")
                            
                            # Limpar arquivo do subfinder
                            try:
                                os.unlink(subfinder_file)
                            except:
                                pass
                            
                            return {
                                "status": "success", 
                                "live_hosts": live_hosts,
                                "output_file": httpx_output_file
                            }
                        else:
                            # Arquivo vazio, limpar
                            try:
                                os.unlink(httpx_output_file)
                                os.unlink(subfinder_file)
                            except:
                                pass
                            return {"status": "error", "error": "No live hosts found"}
                else:
                    # Arquivo não foi criado, mas verificar se há saída no stdout
                    stdout_content = stdout.decode().strip()
                    if stdout_content:
                        print("HTTPX: Arquivo não criado, usando stdout")
                        live_hosts = [line.strip() for line in stdout_content.split('\n') if line.strip()]
                        print(f"HTTPX: Encontrados {len(live_hosts)} hosts vivos (via stdout)")
                        
                        # Criar arquivo manualmente com o conteúdo do stdout
                        try:
                            with open(httpx_output_file, 'w', encoding='utf-8') as f:
                                f.write(stdout_content)
                            print(f"HTTPX: Arquivo criado manualmente: {httpx_output_file}")
                        except Exception as e:
                            print(f"HTTPX: Erro ao criar arquivo manualmente: {e}")
                            return {"status": "error", "error": f"Failed to create output file: {e}"}
                        
                        # Limpar arquivo do subfinder
                        try:
                            os.unlink(subfinder_file)
                        except:
                            pass
                        
                        return {
                            "status": "success", 
                            "live_hosts": live_hosts,
                            "output_file": httpx_output_file
                        }
                    else:
                        print("HTTPX: Arquivo de saída não foi criado e sem stdout")
                        return {"status": "error", "error": "HTTPX output file not created and no stdout"}
            else:
                error_msg = stderr.decode().strip()
                if not error_msg:
                    error_msg = f"HTTPX failed with return code {process.returncode}"
                print(f"HTTPX: Erro - {error_msg}")
                return {"status": "error", "error": error_msg}
                
        except Exception as e:
            print(f"HTTPX: Exception - {str(e)}")
            import traceback
            traceback.print_exc()
            return {"status": "error", "error": str(e)}
    
    async def _run_httpx_from_list(self, subdomains: List[str]) -> Dict:
        """Versão fallback para quando não há arquivo do subfinder"""
        if not subdomains:
            return {"status": "error", "error": "No subdomains to check"}
        
        try:
            print(f"HTTPX: Verificando {len(subdomains)} subdomínios (via lista)...")
            
            # Preparar lista de hosts
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
                
                if subdomain:
                    hosts_to_check.append(subdomain)
            
            if not hosts_to_check:
                return {"status": "error", "error": "No valid hosts to check"}
            
            print(f"HTTPX: Processando {len(hosts_to_check)} hosts...")
            
            httpx_path = self._get_tool_path("httpx")
            print(f"HTTPX: Executável: {httpx_path}")
            
            # Verificar se httpx existe
            if not os.path.exists(httpx_path) and httpx_path == "httpx":
                import shutil
                httpx_path = shutil.which("httpx")
                if not httpx_path:
                    return {"status": "error", "error": "HTTPX not found in PATH"}
            
            # Preparar input para stdin
            hosts_input = '\n'.join(hosts_to_check)
            
            # Criar arquivo de saída
            httpx_output_file = tempfile.mktemp(suffix='_httpx.txt')
            
            # Comando: echo hosts | httpx -silent -o output
            cmd = f"echo '{hosts_input}' | {httpx_path} -silent -o {httpx_output_file}"
            print(f"HTTPX: Comando: {cmd}")
            
            # Executar via shell
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy()
            )
            
            stdout, stderr = await process.communicate()
            
            print(f"HTTPX: Return code: {process.returncode}")
            print(f"HTTPX: Stdout: {stdout.decode()}")
            print(f"HTTPX: Stderr: {stderr.decode()}")
            
            if process.returncode == 0:
                if os.path.exists(httpx_output_file):
                    with open(httpx_output_file, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        if content:
                            live_hosts = [line.strip() for line in content.split('\n') if line.strip()]
                            print(f"HTTPX: Encontrados {len(live_hosts)} hosts vivos (via arquivo)")
                            
                            return {
                                "status": "success", 
                                "live_hosts": live_hosts,
                                "output_file": httpx_output_file
                            }
                        else:
                            try:
                                os.unlink(httpx_output_file)
                            except:
                                pass
                            return {"status": "error", "error": "No live hosts found"}
                else:
                    # Arquivo não foi criado, usar stdout
                    stdout_content = stdout.decode().strip()
                    if stdout_content:
                        print("HTTPX: Arquivo não criado, usando stdout")
                        live_hosts = [line.strip() for line in stdout_content.split('\n') if line.strip()]
                        print(f"HTTPX: Encontrados {len(live_hosts)} hosts vivos (via stdout)")
                        
                        # Criar arquivo manualmente
                        try:
                            with open(httpx_output_file, 'w', encoding='utf-8') as f:
                                f.write(stdout_content)
                            print(f"HTTPX: Arquivo criado manualmente: {httpx_output_file}")
                        except Exception as e:
                            print(f"HTTPX: Erro ao criar arquivo: {e}")
                            return {"status": "error", "error": f"Failed to create output file: {e}"}
                        
                        return {
                            "status": "success", 
                            "live_hosts": live_hosts,
                            "output_file": httpx_output_file
                        }
                    else:
                        return {"status": "error", "error": "HTTPX output file not created and no stdout"}
            else:
                error_msg = stderr.decode().strip()
                return {"status": "error", "error": error_msg}
                
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
            
            # Verificar se nuclei existe
            if not os.path.exists(nuclei_path) and nuclei_path == "nuclei":
                import shutil
                nuclei_path = shutil.which("nuclei")
                if not nuclei_path:
                    return {"status": "error", "error": "NUCLEI not found in PATH"}
            
            # Usar APENAS templates custom
            custom_templates_dir = os.path.join(os.getcwd(), "templates", "custom")
            
            # Criar diretório se não existir
            if not os.path.exists(custom_templates_dir):
                os.makedirs(custom_templates_dir, exist_ok=True)
                print(f"NUCLEI: Criado diretório de templates custom: {custom_templates_dir}")
            
            # Verificar se há templates YAML na pasta custom
            yaml_files = []
            if os.path.exists(custom_templates_dir):
                yaml_files = [f for f in os.listdir(custom_templates_dir) if f.endswith('.yaml') or f.endswith('.yml')]
            
            if not yaml_files:
                print(f"NUCLEI: Nenhum template encontrado em {custom_templates_dir}")
                print("NUCLEI: Adicione templates .yaml na pasta ./templates/custom/ para executar scans")
                return {"status": "error", "error": "No templates found in ./templates/custom/"}
            
            template_configs = [("custom-only", ["-t", custom_templates_dir])]
            
            # Tentar cada configuração até uma funcionar
            for config_name, template_args in template_configs:
                print(f"NUCLEI: Tentando configuração '{config_name}'...")
                
                # Montar comando nuclei
                nuclei_cmd = [nuclei_path, "-jsonl", "-silent", "-nc", "-l", hosts_file]
                
                # Adicionar templates se especificados
                if template_args:
                    nuclei_cmd.extend(template_args)
                
                print(f"NUCLEI: Comando: {' '.join(nuclei_cmd)}")
                
                try:
                    # Executar nuclei diretamente usando o arquivo de hosts
                    process = await asyncio.create_subprocess_exec(
                        *nuclei_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=os.environ.copy()
                    )
                    
                    # Implementar timeout reduzido para o nuclei
                    try:
                        stdout, stderr = await asyncio.wait_for(
                            process.communicate(), 
                            timeout=120  # 2 minutos - reduzido para evitar travamentos
                        )
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                        raise asyncio.TimeoutError("Nuclei timeout after 2 minutes")
                    
                    print(f"NUCLEI: Return code: {process.returncode}")
                    stderr_content = stderr.decode().strip()
                    if stderr_content:
                        print(f"NUCLEI: Stderr: {stderr_content[:200]}...")
                    
                    # Nuclei pode retornar 0 (sucesso) ou 1 (quando não há resultados)
                    if process.returncode in [0, 1]:
                        results = []
                        output_text = stdout.decode().strip()
                        
                        if output_text:
                            output_lines = output_text.split('\n')
                            print(f"NUCLEI: Processando {len(output_lines)} linhas de saída com configuração '{config_name}'...")
                            
                            for line in output_lines:
                                line = line.strip()
                                if line and line.startswith('{'):
                                    try:
                                        result = json.loads(line)
                                        results.append(result)
                                        # Log específico para detecção de .git
                                        if result.get('template-id') == 'git-exposure-check':
                                            print(f"NUCLEI: ⚠️  EXPOSIÇÃO DE .GIT DETECTADA em {result.get('matched-at', 'unknown')}")
                                    except json.JSONDecodeError:
                                        continue
                        
                        print(f"NUCLEI: Encontradas {len(results)} vulnerabilidades com configuração '{config_name}'")
                        
                        # Limpar arquivo temporário se foi criado por nós
                        if cleanup_file and os.path.exists(hosts_file):
                            try:
                                os.unlink(hosts_file)
                            except:
                                pass
                        
                        return {"status": "success", "results": results, "config_used": config_name}
                    
                    elif process.returncode == 2:
                        print(f"NUCLEI: Configuração '{config_name}' falhou com return code 2 (template não encontrado), tentando próxima...")
                        continue  # Tentar próxima configuração
                    
                    else:
                        print(f"NUCLEI: Configuração '{config_name}' falhou com return code {process.returncode}")
                        error_output = stderr_content[:500] if stderr_content else "No error output"
                        print(f"NUCLEI: Error output: {error_output}")
                        continue  # Tentar próxima configuração
                        
                except asyncio.TimeoutError:
                    print(f"NUCLEI: Timeout na configuração '{config_name}', tentando próxima...")
                    continue
                except Exception as e:
                    print(f"NUCLEI: Erro na configuração '{config_name}': {e}")
                    continue
            
            # Se chegou aqui, todas as configurações falharam
            error_msg = "All nuclei configurations failed"
            print(f"NUCLEI: {error_msg}")
            
            # Limpar arquivo temporário se foi criado por nós
            if cleanup_file and os.path.exists(hosts_file):
                try:
                    os.unlink(hosts_file)
                except:
                    pass
            
            return {"status": "error", "error": error_msg}
                
        except Exception as e:
            print(f"NUCLEI: Exception - {str(e)}")
            return {"status": "error", "error": str(e)}
    
    async def _run_scan_pipeline(self, target_id: int, target: str, db_session_data: dict):
        """Pipeline de scan corrigido e simplificado"""
        scan_id = f"scan_{target_id}"
        
        # Atualizar status
        if scan_id in self.scan_jobs:
            self.scan_jobs[scan_id]["status"] = "running"
            self.scan_jobs[scan_id]["progress"] = []
        
        # Criar nova sessão de banco para este scan
        from .database import SessionLocal, HawksTarget as HawksTargetDB
        db = SessionLocal()
        
        try:
            # Atualizar status no banco
            target_obj = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
            if target_obj:
                target_obj.scan_status = "running"
                db.commit()
            
            # 1. SUBFINDER
            if self._should_stop(scan_id):
                return
                
            print(f"🔍 {scan_id}: Executando Subfinder...")
            subfinder_result = await self.run_subfinder(target)
            
            # Salvar resultado do subfinder
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
                
            # 2. HTTPX - usar arquivo do subfinder se disponível
            print(f"🌐 {scan_id}: Executando HTTPX...")
            subfinder_file = subfinder_result.get("output_file")
            all_subdomains = subfinder_result.get("subdomains", [])
            
            # Chaos (se API key disponível) - adicionar ao arquivo do subfinder ou criar lista
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
                    chaos_subdomains = chaos_result.get("subdomains", [])
                    if chaos_subdomains:
                        all_subdomains.extend(chaos_subdomains)
                        all_subdomains = list(set(all_subdomains))
                        
                        # Se temos arquivo do subfinder, adicionar chaos domains ao arquivo
                        if subfinder_file and os.path.exists(subfinder_file):
                            with open(subfinder_file, 'a', encoding='utf-8') as f:
                                for domain in chaos_subdomains:
                                    if domain not in subfinder_result.get("subdomains", []):
                                        f.write(f"\n{domain}")
                            print(f"CHAOS: Adicionados {len(chaos_subdomains)} domínios ao arquivo do subfinder")
            
            if self._should_stop(scan_id):
                return
                
            # HTTPX - priorizar arquivo do subfinder
            if subfinder_file and os.path.exists(subfinder_file):
                print("PIPELINE: Usando arquivo do subfinder para HTTPX")
                httpx_result = await self.run_httpx(subfinder_file=subfinder_file)
            else:
                print("PIPELINE: Usando lista de subdomínios para HTTPX")
                httpx_result = await self.run_httpx(subdomains=all_subdomains)
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
            
            # Finalizar scan com sucesso
            status = "stopped" if self._should_stop(scan_id) else "completed"
            
            if scan_id in self.scan_jobs:
                self.scan_jobs[scan_id]["status"] = status
                
            # Atualizar status final no banco
            target_obj = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
            if target_obj:
                target_obj.scan_status = status
                db.commit()
                
            print(f"✅ {scan_id}: Pipeline concluído com status {status}")
            
        except Exception as e:
            error_msg = str(e)
            print(f"❌ {scan_id}: Erro no pipeline - {error_msg}")
            
            # Atualizar status de erro
            if scan_id in self.scan_jobs:
                self.scan_jobs[scan_id]["status"] = "error"
                self.scan_jobs[scan_id]["error"] = error_msg
            
            # Atualizar banco com erro
            try:
                target_obj = db.query(HawksTargetDB).filter(HawksTargetDB.id == target_id).first()
                if target_obj:
                    target_obj.scan_status = "error"
                    db.commit()
            except:
                pass
                
        finally:
            # Sempre fechar sessão do banco
            db.close()

hawks_scanner = HawksScanner()
