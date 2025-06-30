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
        self.tools_path = self._get_tools_path()
    
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
            with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
                f.write('\n'.join(subdomains))
                temp_file = f.name
            
            httpx_path = self._get_tool_path("httpx")
            cmd = [httpx_path, "-l", temp_file, "-o", "-", "-silent"]
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            os.unlink(temp_file)
            
            if process.returncode == 0:
                live_hosts = stdout.decode().strip().split('\n')
                return {"status": "success", "live_hosts": [h for h in live_hosts if h]}
            else:
                return {"status": "error", "error": stderr.decode()}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def run_nuclei(self, hosts: List[str], templates: List[str]) -> Dict:
        if not hosts or not templates:
            return {"status": "error", "error": "No hosts or templates"}
        
        try:
            with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
                f.write('\n'.join(hosts))
                hosts_file = f.name
            
            with tempfile.TemporaryDirectory() as temp_dir:
                template_files = []
                for i, template_content in enumerate(templates):
                    template_path = os.path.join(temp_dir, f"template_{i}.yaml")
                    with open(template_path, 'w') as tf:
                        tf.write(template_content)
                    template_files.append(template_path)
                
                nuclei_path = self._get_tool_path("nuclei")
                cmd = [nuclei_path, "-l", hosts_file, "-t"] + template_files + ["-json", "-silent"]
                process = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
            
            os.unlink(hosts_file)
            
            if process.returncode == 0:
                results = []
                for line in stdout.decode().strip().split('\n'):
                    if line:
                        try:
                            results.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                return {"status": "success", "results": results}
            else:
                return {"status": "error", "error": stderr.decode()}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def scan_target(self, target_id: int, target: str, db: Session):
        scan_id = f"scan_{target_id}_{datetime.now().timestamp()}"
        self.scan_jobs[scan_id] = {"status": "running", "progress": []}
        
        try:
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
            
            all_subdomains = subfinder_result.get("subdomains", [])
            
            if hawks_config.chaos_api_key:
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
            
            templates = db.query(HawksTemplate).filter(HawksTemplate.enabled == True).order_by(HawksTemplate.order_index).all()
            template_contents = [t.content for t in templates]
            
            if template_contents and httpx_result["status"] == "success":
                nuclei_result = await self.run_nuclei(httpx_result.get("live_hosts", []), template_contents)
                scan_result = HawksScanResult(
                    target_id=target_id,
                    scan_type="nuclei",
                    status="success" if nuclei_result["status"] == "success" else "error",
                    result_data=json.dumps(nuclei_result),
                    error_msg=nuclei_result.get("error")
                )
                db.add(scan_result)
                db.commit()
            
            self.scan_jobs[scan_id]["status"] = "completed"
            
        except Exception as e:
            self.scan_jobs[scan_id]["status"] = "error"
            self.scan_jobs[scan_id]["error"] = str(e)

hawks_scanner = HawksScanner()
