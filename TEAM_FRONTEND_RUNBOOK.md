# Pacific BioArchive 开发、测试、构建与部署指南

本文档分成两条流程：日常前端测试，以及完整构建/云部署。

除非步骤明确要求进入 `frontend` 或 `build/azure-function-app`，所有命令都从仓库根目录执行。进入子目录完成操作后，先返回仓库根目录再执行下一节。

> **固定访问地址：`http://localhost:5173`。** 不要使用 `http://127.0.0.1:5173`，也不要让 Vite 切换到 `5174`。`localhost` 和 `127.0.0.1` 是不同的浏览器 origin；当前 API CORS 和 Cognito callback 只允许 `http://localhost:5173`。地址错误会导致登录/注册显示 `Authentication is not configured`。

共享开发环境：

| 项目 | 值 |
|---|---|
| AWS Region / Account | `ap-southeast-2` / `983367475562` |
| AWS API | `https://j85cs8gf3d.execute-api.ap-southeast-2.amazonaws.com` |
| API / Worker Lambda | `pacific-bioarchive-development-api` / `pacific-bioarchive-development-media-worker` |
| Azure Subscription | `6932a700-63c2-4df8-8964-c5e0e2b906e2` |
| Azure Resource Group | `pacific-bioarchive-development-rg-kr` |
| Azure Function | `pacific-bioarchive-development-data-pba826` |

## 第一部分：配置环境并运行前端进行测试

### 1. 环境要求

前端测试只需要已激活的 Python `3.12` 环境、Node.js/npm 和可访问云端 API 的网络。脚本使用当前 `PATH` 中的 `python`，不会创建 `.venv`，也不依赖固定环境名或绝对路径。

Windows 当前机器示例：

```powershell
conda activate 5225A2
python --version
python -c "import sys; print(sys.executable)"
```

macOS 可使用任意名称的 Conda 或 Python 3.12 虚拟环境：

```bash
source <environment-directory>/bin/activate
python --version
python -c 'import sys; print(sys.executable)'
```

### 2. 首次初始化

从项目根目录运行。

Windows PowerShell：

```powershell
python scripts/check-environment.py
.\scripts\bootstrap.ps1
```

macOS：

```bash
python scripts/check-environment.py
bash scripts/bootstrap.sh
```

`bootstrap` 使用当前 Python 安装项目依赖，并在 `frontend` 中执行 `npm ci`。仅运行前端时，缺少 Terraform、Docker 或云 CLI 不会阻止 Vite；部署前则必须补齐。

### 3. 配置云端 API

创建或检查 `frontend/.env.local`：

```dotenv
VITE_API_BASE_URL=https://j85cs8gf3d.execute-api.ap-southeast-2.amazonaws.com
```

该文件已被 Git 忽略。修改后必须重启 Vite。不要把 OAuth secret、云凭据或 token 写入前端变量。

### 4. 启动前端

Windows PowerShell：

```powershell
Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue
Set-Location frontend
npm run dev -- --host localhost --port 5173 --strictPort
```

macOS：

```bash
lsof -nP -iTCP:5173 -sTCP:LISTEN
cd frontend
npm run dev -- --host localhost --port 5173 --strictPort
```

只打开 `http://localhost:5173/login`。`--strictPort` 会在端口被占用时直接报错，不会切换到不在 Cognito 白名单中的端口。按 Ctrl+C 停止。

若还需同时测试本地 Python API，可从项目根目录运行：

```powershell
.\scripts\start-local.ps1
```

```bash
bash scripts/start-local.sh
```

即使日志显示监听环回地址，浏览器仍应使用 `localhost`，不要手动改为 `127.0.0.1`。

### 5. 前端测试和构建检查

```powershell
.\scripts\test-frontend.ps1
```

```bash
bash scripts/test-frontend.sh
```

也可在 `frontend` 目录分别运行：

```bash
npm test -- --run
npm run build
```

### 6. 浏览器测试顺序

1. 用自己的邮箱注册，填写 email、given name、family name 和密码。
2. 从邮箱或垃圾邮件取得 Cognito 验证码，确认后测试登录、退出和再次登录。
3. 上传 JPG/PNG 或 MP4/MOV，等待状态变为 `Ready`。
4. 重复上传相同文件，确认不会产生第二条记录。
5. 测试 Species、Tag counts、多标签 AND 查询和查询图片。
6. 测试批量添加/移除标签、删除媒体，并检查逐项结果。
7. 创建订阅、完成 SNS 邮箱确认、修改和删除订阅。

前端限制：图片最大 25 MiB；视频最大 512 MiB、最长 120 秒。查询文件不应进入媒体库或永久保留。

### 7. 常见问题

**`Authentication is not configured`**

依次检查：

1. 地址必须是 `http://localhost:5173`，不能是 `127.0.0.1` 或 `5174`。
2. `.env.local` 中必须有正确的 `VITE_API_BASE_URL`。
3. 修改配置后必须重启 Vite。
4. 以下接口应返回 `200` 和 `access-control-allow-origin: http://localhost:5173`：

```powershell
curl.exe -i -H "Origin: http://localhost:5173" https://j85cs8gf3d.execute-api.ap-southeast-2.amazonaws.com/auth/config
```

macOS 将 `curl.exe` 换为 `curl`。

**`Port 5173 is already in use`**

Windows：

```powershell
Get-NetTCPConnection -LocalPort 5173 -State Listen | Select-Object LocalPort,OwningProcess
Get-Process -Id <PID>
```

确认是旧 Vite 后再运行 `Stop-Process -Id <PID>`。macOS 使用 `lsof -nP -iTCP:5173 -sTCP:LISTEN`，确认后运行 `kill <PID>`。

**Processing、Failed 或缩略图 unavailable**

先刷新媒体库。若状态不变，检查 Worker 健康状态、CloudWatch 和 SQS/DLQ；`Failed` 是终止状态，不表示仍在生成预览。

**`401 Access token is invalid`**

退出后重新登录，且不要复制或分享任何 Cognito token。

## 第二部分：配置构建和部署环境及方法

### 1. 完整工具链和云登录

部署终端必须先激活 Python `3.12`，并安装 Node/npm、Terraform、Docker Desktop、AWS CLI、Azure CLI 和 Azure Functions Core Tools 4。统一检查：

```bash
python scripts/check-environment.py
```

部署前不应有 `MISSING` 或 `FAIL`。然后确认身份。

Windows PowerShell：

```powershell
$env:AWS_PROFILE = "pba-team"
$env:AWS_REGION = "ap-southeast-2"
aws sts get-caller-identity
az account set --subscription 6932a700-63c2-4df8-8964-c5e0e2b906e2
az account show --query "{subscription:id,tenant:tenantId,user:user.name}" -o table
```

macOS：

```bash
export AWS_PROFILE=pba-team
export AWS_REGION=ap-southeast-2
aws sts get-caller-identity
az account set --subscription 6932a700-63c2-4df8-8964-c5e0e2b906e2
az account show --query '{subscription:id,tenant:tenantId,user:user.name}' -o table
```

AWS Account 必须为 `983367475562`。凭据过期时按团队实际方式重新执行 `aws configure`/`aws sso login` 或 `az login`。学校 Conditional Access/VPN 阻止登录时，应在允许的网络完成认证，不要绕过策略。

### 2. 发布前验证

Windows：

```powershell
.\scripts\test-backend.ps1
.\scripts\test-contracts.ps1
.\scripts\test-frontend.ps1
.\scripts\validate-infra.ps1
```

macOS：

```bash
bash scripts/test-backend.sh
bash scripts/test-contracts.sh
bash scripts/test-frontend.sh
bash scripts/validate-infra.sh
```

`validate-infra` 构建 Linux/Python 3.12 Lambda ZIP、运行 contract tests、`fmt -check`、`init -backend=false -lockfile=readonly` 和 `validate`；不会运行 `plan/apply`。

### 3. AWS API Lambda

API Lambda 为 Python 3.12、`x86_64`。脚本会下载 Linux `manylinux2014_x86_64` wheels，不能直接打包 Windows/macOS native dependencies。

Windows：

```powershell
.\scripts\build-aws-api-package.ps1
aws lambda update-function-code --function-name pacific-bioarchive-development-api --zip-file fileb://build/aws-api.zip
aws lambda wait function-updated --function-name pacific-bioarchive-development-api
```

macOS：

```bash
bash scripts/build-aws-api-package.sh
aws lambda update-function-code --function-name pacific-bioarchive-development-api --zip-file fileb://build/aws-api.zip
aws lambda wait function-updated --function-name pacific-bioarchive-development-api
```

### 4. Azure Function

每次修改 Azure Function 代码或依赖后，**必须重新生成 staging，并从 staging 发布**。不要从项目根目录运行 `func publish`，否则会扫描测试、模型和权限受限的临时目录。

Windows：

```powershell
.\scripts\stage-azure-function.ps1
Set-Location build\azure-function-app
func azure functionapp publish pacific-bioarchive-development-data-pba826 --python
```

macOS：

```bash
bash scripts/stage-azure-function.sh
(cd build/azure-function-app && func azure functionapp publish pacific-bioarchive-development-data-pba826 --python)
```

### 5. Worker 镜像和模型

Worker 固定构建为 `linux/amd64`，与 Lambda architecture 一致并兼容 Apple Silicon。默认模型目录是仓库根目录的 `models/`，也可显式指定。

Windows：

```powershell
.\scripts\build-push-aws-worker-image.ps1 `
  -RepositoryUri 983367475562.dkr.ecr.ap-southeast-2.amazonaws.com/pacific-bioarchive-development-media-worker `
  -Tag ml-v2 `
  -ModelDirectory <model-directory>
```

macOS：

```bash
bash scripts/build-push-aws-worker-image.sh \
  983367475562.dkr.ecr.ap-southeast-2.amazonaws.com/pacific-bioarchive-development-media-worker \
  --tag ml-v2 --model-directory <model-directory>
```

脚本输出 `repository@sha256:digest` 后更新 Worker：

```bash
aws lambda update-function-code --function-name pacific-bioarchive-development-media-worker --image-uri '<repository@sha256:digest>'
aws lambda wait function-updated --function-name pacific-bioarchive-development-media-worker
```

### 6. Terraform state 和变更规则

`terraform.tfstate` 与 `terraform.tfvars` 是本地敏感文件且不得提交。云资源已存在：

1. 普通代码发布只运行 `validate-infra`，不需要 `plan/apply`。
2. 仅获授权成员在确认使用团队当前 state 和正确 tfvars 后才能生成 plan。
3. apply 前必须审阅完整 plan，确认资源 ID、变更数量和所有 `destroy`。
4. 不要 apply 旧 plan，不要在 state 缺失时重新创建资源。

只有 provider 约束改变时才更新多平台 lockfile：

```powershell
.\scripts\lock-terraform-providers.ps1
```

```bash
bash scripts/lock-terraform-providers.sh
```

### 7. 部署后验证

```bash
curl -i https://j85cs8gf3d.execute-api.ap-southeast-2.amazonaws.com/health
curl -i https://pacific-bioarchive-development-data-pba826.azurewebsites.net/health
```

检查 Worker：

```bash
aws lambda invoke --function-name pacific-bioarchive-development-media-worker --cli-binary-format raw-in-base64-out --payload '{"health_check":true}' <output-file>
aws lambda invoke --function-name pacific-bioarchive-development-media-worker --cli-binary-format raw-in-base64-out --payload '{"model_check":true}' <output-file>
```

必要时查看日志：

```bash
aws logs tail /aws/lambda/pacific-bioarchive-development-api --since 10m --format short
aws logs tail /aws/lambda/pacific-bioarchive-development-media-worker --since 10m --format short
```

### 8. 安全和提交检查

不得提交或分享云密钥/密码/MFA、Cognito token、OAuth secret、Cosmos key、`terraform.tfvars`、`*.tfstate*`、`.env.local`、构建产物、模型权重或本地依赖目录。

提交前运行：

```bash
git status --short
git diff --check
```

只暂存本次确认过的文件，不要使用 `git add .` 将本地配置、state 或构建产物一并提交。
