"""Run this to verify the app works before deploying."""
import sys, os

os.environ.setdefault('DB_PATH', '/tmp/check_sales.db')
os.environ.setdefault('SECRET_KEY', 'test-key-123')

try:
    from app import app
    c = app.test_client()
    
    # Test 1: Homepage
    r = c.get('/')
    assert r.status_code == 200, f"Homepage failed: {r.status_code}"
    assert b'login-screen' in r.data, "No login form"
    print("✅ Homepage loads")
    
    # Test 2: Login
    r = c.post('/api/auth/login',
        json={'email':'admin@salesai.com','password':'Admin@123456'})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.data[:100]}"
    tok = r.get_json()['data']['token']
    print("✅ Login works")
    
    # Test 3: API
    h = {'Authorization': f'Bearer {tok}'}
    for path in ['/api/companies', '/api/emails', '/api/meetings',
                 '/api/opportunities', '/health']:
        rr = c.get(path, headers=h)
        assert rr.status_code == 200, f"{path} returned {rr.status_code}"
    print("✅ All API routes work")
    
    print("\n🎉 App is ready to deploy!")
    print("   Login: admin@salesai.com / Admin@123456")
    
except Exception as e:
    print(f"❌ Check failed: {e}")
    sys.exit(1)
