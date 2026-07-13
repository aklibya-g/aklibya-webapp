import urllib.request, re

try:
    r = urllib.request.urlopen('http://127.0.0.1:8000/transactions/', timeout=5)
    html = r.read().decode()
    
    # Check for JS errors in var declarations
    for m in re.finditer(r'var (\w+) = ([^;]+);', html):
        name, val = m.group(1), m.group(2)
        if ',' in val:
            print('WARNING JS: var %s = %s  <-- comma in value!' % (name, val))
    
    # Check buttons
    for btn in ['deleteSelected', 'clearAll', 'printPage']:
        if 'onclick="%s()"' % btn in html:
            print('Button %s: OK' % btn)
        else:
            print('Button %s: MISSING' % btn)
            
except Exception as e:
    print('Error: %s' % e)
