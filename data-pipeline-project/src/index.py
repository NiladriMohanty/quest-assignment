import json
import boto3
import pandas as pd
import requests
from datetime import datetime
import io
import os
from typing import Dict, List, Tuple
from botocore.exceptions import ClientError

class DataPipelineHandler:
    def __init__(self):
        """Initialize AWS services and configuration."""
        self.s3 = boto3.client('s3')
        self.sqs = boto3.client('sqs')
        self.bucket_name = os.environ['BUCKET_NAME']
        self.queue_url = os.environ['QUEUE_URL']
        # self.bls_api_key = os.environ['BLS_API_KEY']
        self.population_api_url = os.environ['POPULATION_API_URL']

    def sync_bls_data(self) -> None:
        """
        Synchronize BLS data from source to S3.
        Implements logic to avoid duplicate uploads and handles updates.
        """
        try:
            # Set up BLS API headers
            # headers = {
            #     'User-Agent': 'Mozilla/5.0',
            #     'X-API-Key': self.bls_api_key
            # }
            
            # Fetch current data from BLS
            bls_url = "https://download.bls.gov/pub/time.series/pr/"
            # response = requests.get(f"{bls_url}pr.data.0.Current", headers=headers)
            response = requests.get(f"{bls_url}pr.data.0.Current")
            current_data = response.content
            
            # Check if file exists and has different content
            try:
                existing_obj = self.s3.get_object(
                    Bucket=self.bucket_name,
                    Key='bls/pr.data.0.Current'
                )
                if existing_obj['Body'].read() == current_data:
                    print("BLS data is already up to date")
                    return
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchKey':
                    raise

            # Upload new/updated data
            self.s3.put_object(
                Bucket=self.bucket_name,
                Key='bls/pr.data.0.Current',
                Body=current_data
            )
            print("Successfully updated BLS data")
            
        except Exception as e:
            print(f"Error syncing BLS data: {str(e)}")
            raise

    def fetch_population_data(self) -> None:
        """
        Fetch population data from API and store in S3.
        """
        try:
            response = requests.get(self.population_api_url)
            data = response.json()
            
            # Store in S3
            self.s3.put_object(
                Bucket=self.bucket_name,
                Key='population/us_population.json',
                Body=json.dumps(data)
            )
            
            # Send message to SQS for analysis
            self.sqs.send_message(
                QueueUrl=self.queue_url,
                MessageBody=json.dumps({
                    'type': 'population_update',
                    'timestamp': datetime.now().isoformat()
                })
            )
            print("Successfully updated population data")
            
        except Exception as e:
            print(f"Error fetching population data: {str(e)}")
            raise

    def analyze_data(self) -> Dict:
        """
        Perform data analysis on both datasets.
        Returns analysis results as a dictionary.
        """
        try:
            # Load BLS data
            bls_obj = self.s3.get_object(
                Bucket=self.bucket_name,
                Key='bls/pr.data.0.Current'
            )
            bls_df = pd.read_csv(io.BytesIO(bls_obj['Body'].read()))
            
            # Load population data
            pop_obj = self.s3.get_object(
                Bucket=self.bucket_name,
                Key='population/us_population.json'
            )
            pop_data = json.loads(pop_obj['Body'].read())
            pop_df = pd.DataFrame(pop_data)
            
            # Clean data
            bls_df = bls_df.apply(lambda x: x.str.strip() if isinstance(x, str) else x)
            
            # Analysis 1: Population statistics
            pop_stats = pop_df[
                (pop_df['year'] >= 2013) & 
                (pop_df['year'] <= 2018)
            ]['population'].agg(['mean', 'std']).to_dict()
            
            # Analysis 2: Best year per series
            best_years = (
                bls_df.groupby(['series_id', 'year'])['value']
                .sum()
                .reset_index()
                .sort_values('value', ascending=False)
                .groupby('series_id')
                .first()
            ).to_dict('index')
            
            # Analysis 3: Combined report
            combined_report = (
                bls_df[
                    (bls_df['series_id'] == 'PRS30006032') & 
                    (bls_df['period'] == 'Q01')
                ]
                .merge(
                    pop_df,
                    on='year',
                    how='left'
                )
            ).to_dict('records')
            
            return {
                'population_stats': pop_stats,
                'best_years': best_years,
                'combined_report': combined_report
            }
            
        except Exception as e:
            print(f"Error analyzing data: {str(e)}")
            raise

def lambda_handler(event: Dict, context: Dict) -> Dict:
    """
    Main Lambda handler function.
    Handles both scheduled sync events and SQS analysis triggers.
    """
    handler = DataPipelineHandler()
    
    try:
        # Check if this is an SQS trigger
        if 'Records' in event:
            # Perform analysis for SQS messages
            results = handler.analyze_data()
            print("Analysis results:", json.dumps(results, indent=2))
            return {
                'statusCode': 200,
                'body': json.dumps(results)
            }
        else:
            # Scheduled event - perform sync
            handler.sync_bls_data()
            handler.fetch_population_data()
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Sync completed successfully'})
            }
            
    except Exception as e:
        print(f"Error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
