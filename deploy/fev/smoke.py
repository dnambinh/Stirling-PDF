"""Run real table OCR only against a labelled disposable checks container."""
import argparse
import csv
import io
import json
import pathlib
import subprocess
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

p = argparse.ArgumentParser()
p.add_argument('--container', required=True)
p.add_argument('--owner', required=True)
p.add_argument('--fixture', required=True)
a = p.parse_args()
x = json.loads(subprocess.check_output(['docker', 'inspect', a.container]))[0]
assert x['Config']['Labels'].get('com.fev.deploy-run') == a.owner
assert a.owner and a.container
ips = [v['IPAddress'] for v in x['NetworkSettings']['Networks'].values() if v['IPAddress']]
assert len(ips) == 1
base = 'http://' + ips[0] + ':8080'
with urllib.request.urlopen(base + '/api/v1/info/status', timeout=15) as r:
    assert json.load(r)['status'] == 'UP'
pdf = pathlib.Path(a.fixture).read_bytes()
ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
for fmt in ['xlsx', 'csv']:
    boundary = 'fev-' + uuid.uuid4().hex
    body = ('--'+boundary+'\r\nContent-Disposition: form-data; name="pageNumbers"\r\n\r\nall\r\n--'+boundary+'\r\nContent-Disposition: form-data; name="fileInput"; filename="table.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode()+pdf+('\r\n--'+boundary+'--\r\n').encode()
    req = urllib.request.Request(base+'/api/v1/convert/pdf/'+fmt, data=body, headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    with urllib.request.urlopen(req, timeout=900) as r:
        data = r.read()
    if fmt == 'xlsx':
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            shared = []
            if 'xl/sharedStrings.xml' in z.namelist():
                shared = [''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',ns)]
            values = []
            for name in z.namelist():
                if name.startswith('xl/worksheets/sheet') and name.endswith('.xml'):
                    for c in ET.fromstring(z.read(name)).findall('.//m:c',ns):
                        if c.get('t') == 'inlineStr':
                            value = ''.join(c.find('m:is',ns).itertext())
                        else:
                            value = c.findtext('m:v','',ns)
                            if c.get('t') == 's' and value:
                                value = shared[int(value)]
                        values.append(value)
    else:
        if data.startswith(b'PK'):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                text = '\n'.join(z.read(n).decode('utf-8-sig') for n in z.namelist() if n.endswith('.csv'))
        else:
            text = data.decode('utf-8-sig')
        values = [c for row in csv.reader(io.StringIO(text)) for c in row]
    assert all(v in values for v in ['FEV100','FEV200','FEV300','12','8','6']), (fmt, values)
    print('ISOLATED_REAL_OCR_'+fmt.upper()+'_PASSED', flush=True)
