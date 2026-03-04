import re
with open('/etc/nginx/nginx.conf') as f:
    t = f.read()
t = re.sub(r'.*server_tokens.*', '\tserver_tokens off;', t)
with open('/etc/nginx/nginx.conf', 'w') as f:
    f.write(t)
print('done')
