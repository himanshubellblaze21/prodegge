"""
Build deployment-package.zip for the Excel Generator Lambda.
Packages openpyxl (from lambda/excel-generator/package/) + handler.py.
"""
import zipfile
import os

os.chdir('lambda/excel-generator')

added = set()
with zipfile.ZipFile('deployment-package.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    # Add dependency files from package/ directory
    for root, dirs, files in os.walk('package'):
        for file in files:
            file_path = os.path.join(root, file)
            # Strip leading "package\" to get the Lambda-layer path
            arcname = os.path.relpath(file_path, 'package')
            # Skip if it would conflict with handler.py
            if arcname == 'handler.py':
                continue
            if arcname not in added:
                z.write(file_path, arcname)
                added.add(arcname)

    # Add handler.py at root level (always last — authoritative)
    z.write('handler.py', 'handler.py')

print(f'✓ deployment-package.zip created ({len(added) + 1} files)')
