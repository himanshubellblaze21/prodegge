"""
Build deployment-package.zip for the Excel Generator Lambda.
Packages openpyxl (from lambda/excel-generator/package/) + handler.py + the
scorecard registry (scorecards/: loader, registry.json, every rubric.json).
"""
import zipfile
import os

REPO = os.path.dirname(os.path.abspath(__file__))


def add_scorecards(z):
    """The scorecard registry both Lambdas read their rubric from."""
    base = os.path.join(REPO, 'scorecards')
    z.write(os.path.join(base, '__init__.py'), 'scorecards/__init__.py')
    z.write(os.path.join(base, 'registry.json'), 'scorecards/registry.json')
    for entry in sorted(os.listdir(base)):
        rubric = os.path.join(base, entry, 'rubric.json')
        if os.path.isfile(rubric):
            z.write(rubric, f'scorecards/{entry}/rubric.json')

def main():
    os.chdir(os.path.join(REPO, 'lambda', 'excel-generator'))

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

        add_scorecards(z)

        # Add handler.py at root level (always last — authoritative)
        z.write('handler.py', 'handler.py')

    print(f'✓ deployment-package.zip created ({len(added) + 1} files)')


if __name__ == '__main__':
    main()
