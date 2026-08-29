# Pacific BioArchive

Multi-cloud wildlife media application for FIT5225 Assignment 2.

## Run locally

```powershell
.\scripts\bootstrap.ps1
.\scripts\start-local.ps1
```

Open `http://localhost:5173`. The API runs at `http://localhost:8000`.

## Test

```powershell
.\scripts\test-backend.ps1
.\scripts\test-contracts.ps1
.\scripts\test-frontend.ps1
.\scripts\validate-infra.ps1
```

Cloud deployment requires authorised AWS and Azure credentials supplied outside the repository.

## Cloud deployment order

The Terraform stacks are intentionally separate. Run the AWS stack first so
you have the Cognito issuer/client values for Azure, then publish the Azure
Function and update the AWS worker configuration.

1. Install and authenticate the CLIs (these steps require your account):

   ```bash
   aws configure
   aws sts get-caller-identity
   az login
   az account set --subscription <SUBSCRIPTION_ID>
   terraform version
   ```

2. Build both Lambda packages from the project virtual environment. The API
   package is self-contained; the media worker also needs Lambda layers that
   contain `ffmpeg`, `ffprobe`, the ML runtime, and the three model files.

   ```powershell
   .\scripts\build-aws-api-package.ps1
   .\scripts\build-aws-worker-package.ps1
   ```

   On macOS or Linux, use the native shell scripts:

   ```bash
   ./scripts/build-aws-api-package.sh
   ./scripts/build-push-aws-worker-image.sh <ECR_REPOSITORY_URI>
   ```

3. Copy the examples to `terraform.tfvars` and fill in only your own region,
   Cognito domain prefix, Azure subscription, and unique suffix. Do not put
   OAuth secrets in these files; use `TF_VAR_google_client_secret` or
   `TF_VAR_microsoft_client_secret` in the shell.

4. Apply AWS and record its outputs:

   ```bash
   cd infra/aws
   terraform init
   terraform plan -var-file=terraform.tfvars
   terraform apply -var-file=terraform.tfvars
   terraform output
   ```

5. Put the AWS Cognito issuer and app client ID into `infra/azure/terraform.tfvars`,
   then apply Azure:

   ```bash
   cd ../azure
   terraform init
   terraform plan -var-file=terraform.tfvars
   terraform apply -var-file=terraform.tfvars
   ```

6. Publish the Function App from the repository root. Azure Functions uses the
   managed identity granted by Terraform to access Cosmos DB; no Cosmos key is
   required.

   ```bash
   python -m pip install -r requirements-azure-functions.txt
   func azure functionapp publish <FUNCTION_APP_NAME> --python
   ```

7. For the AWS worker, configure a workload identity that can obtain an Azure
   token for the Cosmos account, provide `worker_cosmos_endpoint`, and attach
   the ffmpeg/ML layer ARNs in `infra/aws/terraform.tfvars`. Without those
   three items Terraform can create the Lambda, but processing messages will
   fail by design.

8. Verify the deployed endpoints before mutation, then run the complete smoke
   flow with `scripts/verify-deployment.ps1 -AllowMutation` only after the
   read-only checks pass. Never paste access tokens or provider secrets into
   chat or source control.

After the first infrastructure apply, re-run the Azure stack whenever the
Cosmos container definitions change. The cloud API expects the `media`,
`subscriptions`, `delivery-ledger`, and `deletion-operations` containers. Then
rebuild and apply the AWS API package so its Lambda environment receives the
new container names. The temporary-file query invokes the ML worker
synchronously; API Gateway's request timeout means very large videos should be
tested through the normal asynchronous upload pipeline instead.

## Cloud feature notes

The deployed API stores upload reservations and media records in Cosmos DB and
uses S3 for original and derived objects. The SQS event source invokes the ECR
worker, which creates thumbnails and writes ML tags back to Cosmos DB. Keep
`worker_enabled = false` while changing infrastructure; enable it only after
the worker image and the Cosmos credential secret have been verified.

The temporary-file query endpoint (`POST /queries/by-file`) stores the request
under `temporary-query/`, invokes the ML worker synchronously, queries the
owner's Cosmos records, and removes the temporary prefix in a `finally` block.
The frontend refreshes pending media records automatically while the worker is
processing them.

For a safe read-only deployment check, set `PBA_API_BASE_URL` and
`PBA_ACCESS_TOKEN`, then run:

```powershell
.\scripts\verify-deployment.ps1 -Check Health
.\scripts\verify-deployment.ps1 -Check Auth
.\scripts\verify-deployment.ps1 -Check Query
```
