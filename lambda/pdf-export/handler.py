"""
PDF export Lambda — GET /evaluations/{evaluation_id}/pdf

Renders the evaluation's Excel scorecard (both sheets) to PDF and returns a
presigned download URL. The PDF is cached next to the workbook in S3 and
rebuilt only when the workbook is newer, so a re-scored evaluation never
serves a stale PDF.

Kept out of the API Lambda on purpose: pycel pulls in numpy, and every other
endpoint would pay for that import on a cold start.
"""
import json
import os
import re
from decimal import Decimal

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from pdf_render import workbook_to_pdf

# Regional endpoint + s3v4, as in the API Lambda: the global endpoint answers
# presigned GETs with a 307 that browsers refuse to follow.
AWS_REGION = os.environ.get('AWS_REGION', 'ap-south-1')
s3_client = boto3.client(
    's3',
    region_name=AWS_REGION,
    endpoint_url=f'https://s3.{AWS_REGION}.amazonaws.com',
    config=Config(signature_version='s3v4'),
)
dynamodb = boto3.resource('dynamodb')

REPORTS_BUCKET = os.environ['REPORTS_BUCKET']
DYNAMODB_TABLE = os.environ['DYNAMODB_TABLE']

TYPE_LABELS = {
    'BCM_PHYSICAL_PD': ('BCM_PD', 'BCM Physical PD'),
    'BM_AUDIO_FI': ('BM_FI', 'BM Audio FI'),
    'RCM_AUDIO_PD': ('RCM_PD', 'RCM Audio PD'),
}


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def response(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
            'Access-Control-Allow-Methods': 'GET,OPTIONS',
        },
        'body': json.dumps(body, cls=DecimalEncoder),
    }


def _last_modified(key):
    try:
        return s3_client.head_object(Bucket=REPORTS_BUCKET, Key=key)['LastModified']
    except ClientError as e:
        if e.response['Error']['Code'] in ('404', 'NoSuchKey', 'NotFound'):
            return None
        raise


def lambda_handler(event, context):
    method = event.get('httpMethod') or event.get('requestContext', {}).get('http', {}).get('method')
    if method == 'OPTIONS':
        return response(200, {})

    try:
        evaluation_id = (event.get('pathParameters') or {}).get('evaluation_id')
        if not evaluation_id:
            return response(400, {'error': 'evaluation_id is required'})

        result = dynamodb.Table(DYNAMODB_TABLE).query(
            KeyConditionExpression='evaluation_id = :eid',
            ExpressionAttributeValues={':eid': evaluation_id},
            ScanIndexForward=False,
            Limit=1,
        )
        if not result.get('Items'):
            return response(404, {'error': 'Evaluation not found'})
        item = result['Items'][0]

        excel_key = item.get('excel_s3_key')
        if not excel_key:
            return response(404, {'error': 'Scorecard not available yet'})

        pdf_key = excel_key.rsplit('.', 1)[0] + '.pdf'
        excel_modified = _last_modified(excel_key)
        if excel_modified is None:
            return response(404, {'error': 'Scorecard file not found'})
        pdf_modified = _last_modified(pdf_key)

        call_type = item.get('call_type', '')
        short, label = TYPE_LABELS.get(call_type, ('AUDIO_PD', 'Audio PD'))
        application_id = item.get('application_id') or evaluation_id

        version = item.get('scorecard_version')
        ver_label = f' v{version}' if version not in (None, '', '0') else ''
        if pdf_modified is None or pdf_modified < excel_modified:
            xlsx = s3_client.get_object(Bucket=REPORTS_BUCKET, Key=excel_key)['Body'].read()
            pdf = workbook_to_pdf(xlsx, doc_title=f'{label} Scorecard{ver_label} — {application_id}')
            s3_client.put_object(
                Bucket=REPORTS_BUCKET, Key=pdf_key, Body=pdf, ContentType='application/pdf',
            )
            print(f'PDF rendered: {pdf_key} ({len(pdf)} bytes)')
        else:
            print(f'PDF cache hit: {pdf_key}')

        # Content-Disposition must stay ASCII and unquoted-safe.
        safe_id = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(application_id)).strip('_') or evaluation_id
        filename = f"{short}_Scorecard{ver_label.replace(' ', '_')}_{safe_id}.pdf"
        download_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': REPORTS_BUCKET,
                'Key': pdf_key,
                'ResponseContentDisposition': f'attachment; filename="{filename}"',
                'ResponseContentType': 'application/pdf',
            },
            ExpiresIn=3600,
        )
        return response(200, {'download_url': download_url, 'filename': filename})

    except Exception as e:
        print(f'PDF export error: {e}')
        import traceback
        traceback.print_exc()
        return response(500, {'error': 'Could not create the PDF. Please try again.'})
