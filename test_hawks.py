#!/usr/bin/env python3
"""
Teste básico do Hawks para verificar funcionalidades principais
"""

import requests
import time
import json

BASE_URL = "http://localhost:8000"

def test_login():
    """Testa login do admin"""
    response = requests.post(f"{BASE_URL}/login", data={
        "username": "admin",
        "password": "hawks"
    }, allow_redirects=False)
    
    if response.status_code == 302:
        cookies = response.cookies
        print("✅ Login realizado com sucesso")
        return cookies
    else:
        print("❌ Erro no login")
        return None

def test_add_target(cookies, domain):
    """Testa adicionar um target"""
    response = requests.post(f"{BASE_URL}/targets", 
                           data={"domain_ip": domain}, 
                           cookies=cookies,
                           allow_redirects=False)
    
    if response.status_code == 302:
        print(f"✅ Target {domain} adicionado")
        return True
    else:
        print(f"❌ Erro ao adicionar target {domain}")
        return False

def test_queue_status(cookies):
    """Testa status da fila"""
    response = requests.get(f"{BASE_URL}/api/queue-status", cookies=cookies)
    
    if response.status_code == 200:
        status = response.json()
        print(f"✅ Status da fila: {status}")
        return status
    else:
        print("❌ Erro ao obter status da fila")
        return None

def test_scan_target(cookies, target_id):
    """Testa iniciar scan de um target"""
    response = requests.post(f"{BASE_URL}/targets/{target_id}/scan", cookies=cookies)
    
    if response.status_code == 200:
        result = response.json()
        print(f"✅ Scan do target {target_id} iniciado: {result}")
        return True
    else:
        print(f"❌ Erro ao iniciar scan do target {target_id}")
        return False

def main():
    print("🦅 Testando Hawks...")
    
    # Login
    cookies = test_login()
    if not cookies:
        return
    
    # Verificar status inicial da fila
    print("\n📊 Status inicial da fila:")
    test_queue_status(cookies)
    
    # Adicionar alguns targets de teste
    test_domains = ["example.com", "httpbin.org", "google.com"]
    
    print(f"\n🎯 Adicionando {len(test_domains)} targets de teste...")
    for domain in test_domains:
        test_add_target(cookies, domain)
    
    # Verificar se targets foram adicionados
    response = requests.get(f"{BASE_URL}/api/targets", cookies=cookies)
    if response.status_code == 200:
        targets = response.json()
        print(f"✅ {len(targets)} targets no banco")
        
        # Testar scan do primeiro target (deve executar imediatamente)
        if targets:
            target_id = targets[0]["id"]
            print(f"\n🔍 Testando scan imediato do target {target_id}...")
            test_scan_target(cookies, target_id)
            
            # Aguardar um pouco e verificar status
            time.sleep(2)
            status = test_queue_status(cookies)
            
            if status and status["active_scans"] > 0:
                print("✅ Scan executando imediatamente (não foi para fila)")
            else:
                print("⚠️  Scan pode ter terminado muito rápido ou foi para fila")
    
    print("\n🎉 Teste concluído!")

if __name__ == "__main__":
    main()
