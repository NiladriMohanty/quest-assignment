#!/bin/bash
set -e

# Configuration
STACK_NAME="data-pipeline"
REGION="us-east-1"
POPULATION_API_URL="https://datausa.io/api/data?drilldowns=Nation&measures=Population"

# Set up directory paths
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
WORK_DIR="${SCRIPT_DIR}/work"
PACKAGE_DIR="${WORK_DIR}/package"
LAYER_DIR="${WORK_DIR}/layer"

echo "Setting up directories..."
echo "Project root: ${PROJECT_ROOT}"
echo "Working directory: ${WORK_DIR}"

# Clean up any existing work directory
rm -rf "${WORK_DIR}"

# Create fresh directories
mkdir -p "${PACKAGE_DIR}"
mkdir -p "${LAYER_DIR}/python"

# Create a requirements.txt file for Lambda layer
cat > "${LAYER_DIR}/requirements.txt" << EOL
numpy==1.24.3
pandas==2.0.3
requests==2.31.0
EOL

# Create Lambda layer with dependencies
echo "Creating Lambda Layer..."
cd "${LAYER_DIR}"
python3 -m venv venv
source venv/bin/activate

# Install dependencies in a way that's compatible with Lambda
pip install --upgrade pip
pip install --platform manylinux2014_x86_64 \
    --target=python \
    --implementation cp \
    --python-version 3.9 \
    --only-binary=:all: \
    -r requirements.txt

deactivate

# Create layer zip
cd "${LAYER_DIR}"
zip -r9 "${WORK_DIR}/lambda_layer.zip" python/

# Create the Lambda function package (now much simpler since dependencies are in layer)
echo "Creating Lambda function package..."
mkdir -p "${PACKAGE_DIR}"
cp "${PROJECT_ROOT}/src/index.py" "${PACKAGE_DIR}/"

cd "${PACKAGE_DIR}"
zip -r9 "${WORK_DIR}/lambda.zip" .

echo "Deploying CloudFormation stack..."
cd "${PROJECT_ROOT}"
aws cloudformation deploy \
    --template-file infrastructure/template.yaml \
    --stack-name $STACK_NAME \
    --parameter-overrides \
        PopulationApiUrl=$POPULATION_API_URL \
    --capabilities CAPABILITY_IAM

# First, create and publish the layer
echo "Publishing Lambda Layer..."
LAYER_ARN=$(aws lambda publish-layer-version \
    --layer-name data-pipeline-dependencies \
    --description "Dependencies for data pipeline" \
    --zip-file fileb://${WORK_DIR}/lambda_layer.zip \
    --compatible-runtimes python3.9 \
    --query 'LayerVersionArn' \
    --output text)

echo "Updating Lambda function..."
# Update the function code
aws lambda update-function-code \
    --function-name $STACK_NAME-pipeline \
    --zip-file fileb://${WORK_DIR}/lambda.zip

# Update the function configuration to use the layer
aws lambda update-function-configuration \
    --function-name $STACK_NAME-pipeline \
    --layers $LAYER_ARN \
    --timeout 300 \
    --memory-size 1024

echo "Deployment completed successfully!"