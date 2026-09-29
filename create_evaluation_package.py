"""
Build deployment-package.zip for the Evaluation Lambda: handler.py plus the
scorecard registry (scorecards/). boto3 comes from the Lambda runtime.
"""
import os
import zipfile

from create_excel_package import add_scorecards, REPO

os.chdir(os.path.join(REPO, 'lambda', 'evaluation'))
with zipfile.ZipFile('deployment-package.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    add_scorecards(z)
    z.write('handler.py', 'handler.py')
    names = z.namelist()
print(f'✓ lambda/evaluation/deployment-package.zip created ({len(names)} files)')
