# /*
# * Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# *
# * Permission is hereby granted, free of charge, to any person obtaining a copy of this
# * software and associated documentation files (the "Software"), to deal in the Software
# * without restriction, including without limitation the rights to use, copy, modify,
# * merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
# * permit persons to whom the Software is furnished to do so.
# *
# * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# * INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# * PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# * HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# * OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# * SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
# */

import json
import boto3
import botocore
import logging
import uuid
from botocore.vendored import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

kms = boto3.client('kms')
sqs = boto3.client('sqs')
s3 = boto3.resource('s3')
lmd = boto3.client('lambda')

responsedata = {}
requireTags = []

def lambda_handler(event, context):
    # get account id
    account_id = context.invoked_function_arn.split(":")[4]
    # Need to make sure we don't waste time if the request type is
    # update or delete.  Exit gracefully
    if event['RequestType'] == "Delete":
        logger.info(f'Request Type is Delete; unsupported')
        cfnsend(event, context, 'SUCCESS', responsedata)
        return event
    if event['RequestType'] == "Update":
        logger.info(f'Request Type is Update; unsupported')
        cfnsend(event, context, 'SUCCESS', responsedata)
        return event

    # check if action provided in request
    if 'Action' in event['ResourceProperties']:

        action = (event['ResourceProperties']['Action']['Name'] if 'Name' in event['ResourceProperties']['Action']
                            else None)
        params = (event['ResourceProperties']['Action']['Parameters'] if action and 'Parameters' in event['ResourceProperties']['Action']
                            else None)

        # Convert tags provided as string in CFN to JSON format
        if 'json' == action.lower() and params:
            jObject = []
            keyList=[]
            sqsTags={}

            # Read tag string from paramter
            tags = (params['JSON'] if 'JSON' in params else '')
            type = (params['Type'] if 'Type' in params else None)

            logger.info(f'Processing tags: {tags}')

            try:
                # Convert string to JSON object
                for t in tags.split(','):
                    tg=None
                    k = t.split('=')
                    if len(k) != 2:
                        logger.info(f'No Key Value found in: '+t)
                        continue
                    if type.lower() == 'tags':
                        tg = {"Key":k[0], "Value":k[1]}
                        keyList.append(k[0])
                    elif type.lower() == 'dynamodbschema':
                        tg = {"AttributeName":k[0], "AttributeType":k[1]}
                    elif type.lower() == 'dynamodbkey':
                        tg = {"AttributeName":k[0], "KeyType":k[1]}
                    elif type.lower() == 'sqs':
                        sqsTags[k[0]] = k[1]

                    if tg:
                        jObject.append(tg)

            except Exception as e:
                logger.info(f'Error processing tags: {str(e)}')
                responsedata['error'] = str(e)
                cfnsend(event, context, 'FAILED', responsedata,'Error processing tags')
                return event

            # Validate if all require tags were provided
            if type.lower() == 'tags':
                for t in requireTags:
                    if not t in keyList:
                        # Failed CFN stack if missing require tag
                        logger.info(f'Missing require tag: {t}')
                        cfnsend(event, context, 'FAILED', responsedata,'Missing require tag: '+t)
                        return event

            # Add tags to SQS
            if type.lower() == 'sqs':
                queueURL = (params['SQS'] if 'SQS' in params else None)
                # Check if SQS URL provided
                if not queueURL:
                    # Failed CFN stack if missing SQS URL
                    logger.info(f'Missing SQS URI')
                    cfnsend(event, context, 'FAILED', responsedata,'Missing SQS URI')
                    return event

                # check if tags provided
                if not sqsTags:
                    # Failed CFN stack if missing tags
                    logger.info(f'Missing SQS tags')
                    cfnsend(event, context, 'FAILED', responsedata,'Missing SQS tags')
                    return event

                #add tags to sqs
                try:
                    logger.info(f'Add Tags to SQS: {queueURL}')
                    response = sqs.tag_queue(
                        QueueUrl=queueURL,
                        Tags=sqsTags
                    )
                except Exception as e:
                    logger.info(f'Error adding tag to SQS: {str(e)}')
                    responsedata['error'] = str(e)
                    cfnsend(event, context, 'FAILED', responsedata,'Error adding tags to SQS')
                    return event

            # response formated JSON object back to CFN
            responsedata['Json'] = jObject

        # put S3 notification to trigger lambda when new object created
        if 's3notification' == action.lower() and params:
            bucket_name = (params['Bucket'] if 'Bucket' in params else None)
            lambda_arn = (params['Lambda'] if 'Lambda' in params else None)
            filterRules = (params['FilterRules'] if 'FilterRules' in params else None)

            if bucket_name and lambda_arn:
                try:
                    response = lmd.add_permission(
                        Action='lambda:InvokeFunction',
                        FunctionName=lambda_arn,
                        Principal='s3.amazonaws.com',
                        SourceArn='arn:aws:s3:::'+bucket_name,
                        SourceAccount=account_id,
                        StatementId=str(uuid.uuid4())
                    )
                    bucket_notification = s3.BucketNotification(bucket_name)

                    if not filterRules:
                        response = bucket_notification.put(
                            NotificationConfiguration={
                                'LambdaFunctionConfigurations': [
                                    {
                                        'LambdaFunctionArn': lambda_arn,
                                        'Events': [
                                            's3:ObjectCreated:*'
                                        ]
                                    }
                                ]
                            }
                        )
                    else:
                        response = bucket_notification.put(
                            NotificationConfiguration={
                                'LambdaFunctionConfigurations': [
                                    {
                                        'LambdaFunctionArn': lambda_arn,
                                        'Events': [
                                            's3:ObjectCreated:*'
                                        ],
                                        'Filter': {
                                            'Key': {
                                                'FilterRules': filterRules
                                            }
                                        }
                                    }
                                ]
                            }
                        )
                    responsedata['Status']='SUCCESS'
                except Exception as e:
                    logger.info(f'Error processing S3 Notification: {str(e)}')
                    responsedata['error'] = str(e)
                    cfnsend(event, context, 'FAILED', responsedata,'Error processing S# Notification')
                    return event
            else:
                cfnsend(event, context, 'FAILED', responsedata,'S3 Notification - missing Bucket or Lambda paramters')
                return event

        # validate if kms is BYOK
        if 'byok' == action.lower() and params:
            key = (params['Key'] if 'Key' in params else None)
            # check if mks key provided
            if key:
                try:
                    # get information about kms key
                    response = kms.describe_key(
                        KeyId=key
                    )
                    # check if kms key is BYOK
                    if response['KeyMetadata']['Origin'] != 'EXTERNAL':
                        logger.info(f'BYOK No Found: {key}')
                        responsedata['Id'] = 'Provided Key is not BYOK'
                        cfnsend(event, context, 'FAILED', responsedata,'Provided Encryption Key is not BYOK')
                        return event
                except Exception as e:
                    logger.info(f'Error processing KMS: {str(e)}')
                    responsedata['error'] = str(e)
                    cfnsend(event, context, 'FAILED', responsedata,'Error processing KMS')
                    return event
            # Failed CFN stack if key not provided
            else:
                cfnsend(event, context, 'FAILED', responsedata,'No Encryption Key Provided')
                return event

        # validate if principal are not publicly open
        if 'principal' == action.lower() and params:
            accountid = (params['Account'] if 'Account' in params else None)
            principals = ((params['Principal']).split(',') if 'Principal' in params else None)
            type = (params['Type'] if 'Type' in params else None)

            if not accountid or not principals:
                # Failed CFN stack if account id or principal missing
                logger.info(f'Missing account id and/or principal')
                cfnsend(event, context, 'FAILED', responsedata,'Missing account id and/or principal')
                return event

            npList = []
            npstr = None
            if not type:
                for p in principals:
                    np = p
                    if p == "*":
                        np = accountid
                    if not np in npList:
                        npList.append(np)
                npstr = ','.join(npList)
            elif type == 'kms':
                for p in principals:
                    np = p.replace('{accountid}',accountid)
                    npList.append(np)

                npstr = npList

            # return formated principal back to CFN
            responsedata['Principal'] = npstr

    # Using the cfnsend function to format our response to Cloudforamtion and send it
    cfnsend(event, context, 'SUCCESS', responsedata)
    return event

def cfnsend(event, context, responseStatus, responseData, reason=None):
    if 'ResponseURL' in event:
        responseUrl = event['ResponseURL']
        # Build out the response json
        responseBody = {}
        responseBody['Status'] = responseStatus
        responseBody['Reason'] = reason or 'CWL Log Stream =' + context.log_stream_name
        responseBody['PhysicalResourceId'] = context.log_stream_name
        responseBody['StackId'] = event['StackId']
        responseBody['RequestId'] = event['RequestId']
        responseBody['LogicalResourceId'] = event['LogicalResourceId']
        responseBody['Data'] = responseData
        json_responseBody = json.dumps(responseBody)

        logger.info(f'Response body: + {json_responseBody}')

        headers = {
            'content-type': '',
            'content-length': str(len(json_responseBody))
        }
        # Send response back to CFN
        try:
            response = requests.put(responseUrl,
                                    data=json_responseBody,
                                    headers=headers)
            logger.info(f'Status code: {response.reason}')
        except Exception as e:
            logger.info(f'send(..) failed executing requests.put(..): {str(e)}')
