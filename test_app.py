#!/usr/bin/env python3

import requests
import sys
import time

def test_hawks_app():
    """Simple test to verify Hawks application is working"""
    
    base_url = "http://localhost:8000"
    
    print("🦅 Testing Hawks Application...")
    print("=" * 40)
    
    # Test 1: Check if app is running
    try:
        response = requests.get(base_url, allow_redirects=False)
        if response.status_code == 302:
            print("✅ Root redirect working (302 to /login)")
        else:
            print(f"❌ Unexpected root response: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to Hawks. Make sure it's running on port 8000")
        return False
    
    # Test 2: Check login page
    try:
        response = requests.get(f"{base_url}/login")
        if response.status_code == 200 and "Hawks" in response.text:
            print("✅ Login page accessible")
        else:
            print(f"❌ Login page issue: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Login page error: {e}")
        return False
    
    # Test 3: Test authentication
    try:
        session = requests.Session()
        login_data = {"username": "admin", "password": "hawks"}
        response = session.post(f"{base_url}/login", data=login_data, allow_redirects=False)
        
        if response.status_code == 302:
            print("✅ Authentication working")
            
            # Test 4: Check dashboard access
            response = session.get(f"{base_url}/dashboard")
            if response.status_code == 200 and "Dashboard" in response.text:
                print("✅ Dashboard accessible after login")
            else:
                print(f"❌ Dashboard access issue: {response.status_code}")
                return False
                
        else:
            print(f"❌ Authentication failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Authentication test error: {e}")
        return False
    
    # Test 5: Test API endpoints
    try:
        response = session.get(f"{base_url}/api/targets")
        if response.status_code == 200:
            print("✅ API endpoints working")
        else:
            print(f"❌ API issue: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ API test error: {e}")
        return False
    
    print("\n🎉 All tests passed! Hawks is working correctly.")
    return True

if __name__ == "__main__":
    success = test_hawks_app()
    sys.exit(0 if success else 1)
