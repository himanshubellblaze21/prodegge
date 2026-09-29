"""
Build deployment-package.zip for the PDF Export Lambda.
Installs Linux (manylinux x86_64, Python 3.11) wheels into
lambda/pdf-export/package/ — uharfbuzz and numpy are compiled, so wheels
built for this machine would not import on Lambda — then zips them with
handler.py, pdf_render.py and fonts/.
"""
import os
import shutil
import subprocess
import sys
import zipfile

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lambda', 'pdf-export'))

shutil.rmtree('package', ignore_errors=True)
subprocess.check_call([
    sys.executable, '-m', 'pip', 'install', '-q',
    '-r', 'requirements.txt',
    '--target', 'package',
    '--platform', 'manylinux2014_x86_64',
    '--implementation', 'cp',
    '--python-version', '3.11',
    '--only-binary=:all:',
    '--upgrade',
])

# Only bytecode caches are dropped: networkx imports its own testing package.
SKIP_DIRS = {'__pycache__'}
count = 0
with zipfile.ZipFile('deployment-package.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk('package'):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for file in files:
            if file.endswith('.pyc'):
                continue
            path = os.path.join(root, file)
            z.write(path, os.path.relpath(path, 'package'))
            count += 1
    for file in os.listdir('fonts'):
        z.write(os.path.join('fonts', file), f'fonts/{file}')
        count += 1
    for file in ('handler.py', 'pdf_render.py'):
        z.write(file, file)
        count += 1

size_mb = os.path.getsize('deployment-package.zip') / 1024 / 1024
print(f'✓ deployment-package.zip created ({count} files, {size_mb:.1f} MB)')
