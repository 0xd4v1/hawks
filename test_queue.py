#!/usr/bin/env python3
"""
Script para testar o sistema de fila do Hawks funcionando em background
"""
import asyncio
import requests
import time
import json
import sys

HAWKS_URL = "http://localhost:8000"
ADMIN_USER = "admin"
ADMIN_PASS = "hawksadmin123"

class HawksQueueTester:
    def __init__(self):
        self.session = requests.Session()
        self.authenticated = False
    
    def login(self):
        """Fazer login no Hawks"""
        try:
            login_data = {
                "username": ADMIN_USER,
                "password": ADMIN_PASS
            }
            response = self.session.post(f"{HAWKS_URL}/login", data=login_data, allow_redirects=False)
            
            if response.status_code == 302:
                print("✓ Login realizado com sucesso")
                self.authenticated = True
                return True
            else:
                print(f"✗ Erro no login: {response.status_code}")
                return False
        except Exception as e:
            print(f"✗ Erro ao conectar: {e}")
            return False
    
    def add_test_targets(self):
        """Adicionar targets de teste"""
        test_targets = [
            "example.com",
            "google.com", 
            "github.com",
            "stackoverflow.com",
            "httpbin.org"
        ]
        
        added = 0
        for target in test_targets:
            try:
                data = {"domain_ip": target}
                response = self.session.post(f"{HAWKS_URL}/targets", data=data, allow_redirects=False)
                if response.status_code == 302:
                    added += 1
                    print(f"✓ Target adicionado: {target}")
                else:
                    print(f"✗ Erro ao adicionar {target}: {response.status_code}")
            except Exception as e:
                print(f"✗ Erro ao adicionar {target}: {e}")
        
        print(f"Total de targets adicionados: {added}")
        return added > 0
    
    def get_queue_status(self):
        """Obter status da fila"""
        try:
            response = self.session.get(f"{HAWKS_URL}/api/queue-status")
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Erro ao obter status da fila: {response.status_code}")
                return None
        except Exception as e:
            print(f"Erro ao obter status da fila: {e}")
            return None
    
    def get_detailed_queue_status(self):
        """Obter status detalhado da fila"""
        try:
            response = self.session.get(f"{HAWKS_URL}/api/queue-status-detailed")
            if response.status_code == 200:
                return response.json()
            else:
                return None
        except Exception as e:
            return None
    
    def start_all_scans(self):
        """Iniciar scan em todos os targets"""
        try:
            response = self.session.post(f"{HAWKS_URL}/targets/scan-all", allow_redirects=False)
            if response.status_code == 200:
                data = response.json()
                print(f"✓ Scans iniciados para {data.get('targets_count', 0)} targets")
                return True
            else:
                print(f"✗ Erro ao iniciar scans: {response.status_code}")
                return False
        except Exception as e:
            print(f"✗ Erro ao iniciar scans: {e}")
            return False
    
    def monitor_queue(self, duration=120):
        """Monitorar a fila por um período"""
        print(f"\n📊 Monitorando fila por {duration} segundos...")
        print("=" * 60)
        
        start_time = time.time()
        while time.time() - start_time < duration:
            status = self.get_queue_status()
            detailed = self.get_detailed_queue_status()
            
            if status:
                timestamp = time.strftime("%H:%M:%S")
                active = status.get('active_scans', 0)
                queued = status.get('queued_scans', 0)
                max_concurrent = status.get('max_concurrent', 0)
                processor_running = status.get('queue_processor_running', False)
                
                print(f"[{timestamp}] Ativos: {active}/{max_concurrent} | Fila: {queued} | Processador: {'✓' if processor_running else '✗'}")
                
                if detailed and detailed.get('scan_jobs'):
                    jobs = detailed['scan_jobs']
                    running_jobs = [job_id for job_id, job_data in jobs.items() if job_data.get('status') == 'running']
                    if running_jobs:
                        print(f"           Jobs em execução: {', '.join(running_jobs)}")
                
                # Se não há mais atividade, pode encerrar
                if active == 0 and queued == 0:
                    print(f"[{timestamp}] ✓ Todas as atividades concluídas")
                    break
            else:
                print("✗ Erro ao obter status da fila")
            
            time.sleep(5)
        
        print("=" * 60)
        print("📊 Monitoramento concluído")

def main():
    print("Hawks - Teste do Sistema de Fila em Background")
    print("=" * 60)
    
    tester = HawksQueueTester()
    
    # Fazer login
    if not tester.login():
        print("Não foi possível fazer login. Verifique se o Hawks está rodando.")
        return
    
    # Verificar status inicial da fila
    print("\n📋 Status inicial da fila:")
    initial_status = tester.get_queue_status()
    if initial_status:
        print(f"  Processador ativo: {'✓' if initial_status.get('queue_processor_running') else '✗'}")
        print(f"  Scans ativos: {initial_status.get('active_scans', 0)}")
        print(f"  Fila: {initial_status.get('queued_scans', 0)}")
        print(f"  Máximo concorrente: {initial_status.get('max_concurrent', 0)}")
    
    # Adicionar targets de teste
    print("\n🎯 Adicionando targets de teste...")
    if not tester.add_test_targets():
        print("Erro ao adicionar targets")
        return
    
    # Iniciar scans
    print("\n🚀 Iniciando scans...")
    if not tester.start_all_scans():
        print("Erro ao iniciar scans")
        return
    
    # Monitorar fila
    tester.monitor_queue(duration=180)  # Monitorar por 3 minutos
    
    print("\n✅ Teste concluído!")
    print("O sistema de fila deve continuar processando em background")
    print("mesmo após o encerramento deste script.")

if __name__ == "__main__":
    main()
