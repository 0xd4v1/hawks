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
        self.queue_processor_running = False
    
    async def start_queue_processor(self):
        """Inicia o processador de fila de scans"""
        if self.queue_processor_task is None and not self.queue_processor_running:
            self.queue_processor_running = True
            self.queue_processor_task = asyncio.create_task(self._queue_processor())
            print("Hawks Scanner - Processador de fila iniciado")
    
    async def stop_queue_processor(self):
        """Para o processador de fila de scans"""
        self.queue_processor_running = False
        if self.queue_processor_task:
            self.queue_processor_task.cancel()
            try:
                await self.queue_processor_task
            except asyncio.CancelledError:
                pass
            self.queue_processor_task = None
            print("Hawks Scanner - Processador de fila parado")
    
    async def _queue_processor(self):
        """Processador contínuo da fila de scans"""
        print("Hawks Scanner - Processador de fila em execução")
        while self.queue_processor_running:
            try:
                # Verificar se há capacidade para novos scans
                async with self.scan_lock:
                    has_capacity = len(self.active_scans) < hawks_config.max_concurrent_scans
                    active_count = len(self.active_scans)
                    queue_size = self.scan_queue.qsize()
                
                # Log periódico do status da fila (apenas se há atividade)
                if active_count > 0 or queue_size > 0:
                    print(f"Hawks Scanner - Scans ativos: {active_count}/{hawks_config.max_concurrent_scans}, Fila: {queue_size}")
                
                if has_capacity and not self.scan_queue.empty():
                    try:
                        scan_data = await asyncio.wait_for(self.scan_queue.get(), timeout=0.1)
                        # Atualizar status para running antes de executar
                        target_id = scan_data[0]
                        scan_id = f"scan_{target_id}"
                        if scan_id in self.scan_jobs:
                            self.scan_jobs[scan_id]["status"] = "running"
                        
                        print(f"Hawks Scanner - Iniciando scan do target {target_id}")
                        # Executar scan
                        asyncio.create_task(self._execute_scan(scan_data))
                    except asyncio.TimeoutError:
                        pass
                else:
                    # Se não há capacidade ou fila vazia, aguardar mais tempo
                    await asyncio.sleep(5)  # Aguardar 5 segundos antes de verificar novamente
                    
            except asyncio.CancelledError:
                print("Hawks Scanner - Processador de fila cancelado")
                break
            except Exception as e:
                print(f"Hawks Scanner - Erro no processador de fila: {e}")
                await asyncio.sleep(2)
    
    async def _execute_scan(self, scan_data):
        """Executa um scan da fila"""
        target_id, target, db = scan_data
        scan_id = f"scan_{target_id}"
        
        print(f"Hawks Scanner - Executando scan da fila para target {target_id}: {target}")
        
        async with self.scan_lock:
            self.active_scans[scan_id] = True
        
        try:
            await self._run_scan_pipeline(target_id, target, db)
            print(f"Hawks Scanner - Scan concluído para target {target_id}")
        except Exception as e:
            print(f"Hawks Scanner - Erro no scan do target {target_id}: {e}")
        finally:
            async with self.scan_lock:
                if scan_id in self.active_scans:
                    del self.active_scans[scan_id]
                    print(f"Hawks Scanner - Removido target {target_id} dos scans ativos")
    
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
            
            print(f"NUCLEI: Encontrados {len(yaml_files)} templates custom: {yaml_files}")
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
                    
                    # Implementar timeout manualmente para o nuclei
                    try:
                        stdout, stderr = await asyncio.wait_for(
                            process.communicate(), 
                            timeout=300  # 5 minutos
                        )
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                        raise asyncio.TimeoutError("Nuclei timeout after 5 minutes")
                    
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
    
    async def scan_target(self, target_id: int, target: str, db: Session):
        """Adiciona scan à fila ou executa imediatamente se há capacidade"""
        scan_id = f"scan_{target_id}"
        self.scan_jobs[scan_id] = {"status": "queued", "progress": []}
        self.stop_flags[scan_id] = False
        
        print(f"Hawks Scanner - Solicitação de scan para target {target_id}: {target}")
        
        # Verificar se pode executar imediatamente
        async with self.scan_lock:
            can_run_now = len(self.active_scans) < hawks_config.max_concurrent_scans
        
        if can_run_now:
            # Executar imediatamente
            async with self.scan_lock:
                self.active_scans[scan_id] = True
            
            self.scan_jobs[scan_id]["status"] = "running"
            print(f"Hawks Scanner - Executando scan imediatamente para target {target_id}")
            
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
            print(f"Hawks Scanner - Adicionando target {target_id} à fila (scans ativos: {len(self.active_scans)}/{hawks_config.max_concurrent_scans})")
            
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
        """Retorna status detalhado da fila de scans"""
        return {
            "active_scans": len(self.active_scans),
            "queued_scans": self.scan_queue.qsize(),
            "max_concurrent": hawks_config.max_concurrent_scans,
            "scan_threads": hawks_config.scan_threads,
            "queue_processor_running": self.queue_processor_running,
            "active_scan_ids": list(self.active_scans.keys()),
            "scan_jobs_count": len(self.scan_jobs)
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
                
            # HTTPX - usar arquivo do subfinder se disponível
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
